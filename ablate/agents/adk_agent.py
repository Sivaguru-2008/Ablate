"""ADK LlmAgent built from an Ablate config. Tools are the world's tools.

Tool functions are stubs with the right signature and the config's
description (ADK builds the function declaration from them); the recorder
plugin's before_tool_callback answers every call from the world, so the stub
bodies never run.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import errors as genai_errors
from google.genai import types

from ..differ import render_instruction
from ..preflight import Issue
from ..world.retail.tools import TOOL_SPECS
from .base import HARNESS_INSTRUCTION, AgentInfraError, AgentRun, MissingCredentials
from .recorder_plugin import OUTCOME_TOOL, RecorderPlugin, have_credentials, request_digest

_TYPES = {"str": str, "list[str]": list[str]}
APP_NAME = "ablate"


def _stub(name: str, params: list[tuple[str, str, str]], description: str) -> FunctionTool:
    def stub(**kwargs: Any) -> dict:
        return {"error": "tool stub called directly; calls are routed to the world by the recorder plugin"}

    sig = [inspect.Parameter(p, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=_TYPES[t]) for p, t, _ in params]
    stub.__signature__ = inspect.Signature(sig, return_annotation=dict)
    stub.__annotations__ = {p: _TYPES[t] for p, t, _ in params} | {"return": dict}
    stub.__name__ = name
    stub.__qualname__ = name
    args = "\n".join(f"    {p}: {d}" for p, _, d in params)
    stub.__doc__ = f"{description}\n\nArgs:\n{args}\n"
    return FunctionTool(stub)


def build_tools(config: dict) -> list[FunctionTool]:
    tools = []
    for name, t in config.get("tools", {}).items():
        spec = TOOL_SPECS.get(name)
        params = spec["params"] if spec else []
        tools.append(_stub(name, params, t.get("description") or (spec or {}).get("description", "")))
    tools.append(_stub(OUTCOME_TOOL, [
        ("outcome", "str", "One of: cancelled, return_requested, info, refused, failed."),
        ("order_id", "str", "The order id the request was about, such as '#W0000000'."),
    ], "Record the final outcome of the user's request. Call exactly once, at the end."))
    return tools


def generate_config(config: dict) -> types.GenerateContentConfig:
    gen = dict(config.get("generation_config") or {})
    thinking = {}
    for k in ("thinking_budget", "thinking_level"):
        if k in gen:
            thinking[k] = gen.pop(k)
    kw = {k: v for k, v in gen.items() if k in types.GenerateContentConfig.model_fields}
    if thinking:
        kw["thinking_config"] = types.ThinkingConfig(**thinking)
    return types.GenerateContentConfig(**kw)


def build_agent(config: dict) -> LlmAgent:
    instruction = render_instruction(config) + "\n\n" + HARNESS_INSTRUCTION
    return LlmAgent(name="retail_agent", model=config["model"], instruction=instruction,
                    tools=build_tools(config), generate_content_config=generate_config(config))


def _is_infra(e: BaseException) -> bool:
    if isinstance(e, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(e, genai_errors.APIError):
        return e.code in (408, 429) or (e.code or 0) >= 500
    return False


def _find(e: BaseException, kind, test=None):
    """First exception in the cause/context chain of type `kind` (and passing `test`)."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, kind) and (test is None or test(e)):
            return e
        e = e.__cause__ or e.__context__
    return None


class AdkAgent:
    name = "adk"

    def __init__(self, *, cache, limiter, timeout_s: float = 120.0) -> None:
        self.cache = cache
        self.limiter = limiter
        self.timeout_s = timeout_s

    async def run_trial(self, config: dict, scenario: dict, world, *, seed: int, cache_key: str) -> AgentRun:
        plugin = RecorderPlugin(world=world, cache=self.cache, trial_key=cache_key, limiter=self.limiter,
                                toolset=set(config.get("tools", {})))
        app = App(name=APP_NAME, root_agent=build_agent(config), plugins=[plugin])
        runner = Runner(app=app, session_service=InMemorySessionService())
        session = await runner.session_service.create_session(app_name=APP_NAME, user_id="user")
        try:
            for turn, text in enumerate(scenario["user_turns"]):
                plugin.turn = turn
                msg = types.Content(role="user", parts=[types.Part(text=text)])
                async with asyncio.timeout(self.timeout_s):
                    async for _ in runner.run_async(user_id="user", session_id=session.id, new_message=msg):
                        pass
        except Exception as e:  # noqa: BLE001 - classified below
            missing = _find(e, MissingCredentials)
            if missing:
                raise missing from None  # ADK wraps plugin errors; surface the real one
            infra = _find(e, BaseException, test=_is_infra)
            if infra:
                raise AgentInfraError(f"{type(infra).__name__}: {infra}") from e
            # DECISION: any other runtime failure of the live stack (malformed
            # function call, SDK error) makes the trial INVALID rather than a
            # violation. INVALID rates are compared across arms (Fisher), so a
            # change that breaks the stack shows up as INFRA_SUSPECTED.
            raise AgentInfraError(f"{type(e).__name__}: {e}", retryable=False) from e
        finally:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001
                pass
        versions = sorted(set(plugin.versions))
        return AgentRun(plugin.structured_output, "|".join(versions) or None, api_calls=plugin.api_calls,
                        cached_calls=plugin.cached_calls, transcript=plugin.transcript)


def make_probe(cache):
    """Preflight probe: one minimal live request per distinct (model, config)."""
    from google import genai

    seen: dict[str, list] = {}

    def probe(cfg: dict, arm: str) -> list:
        gc = generate_config(cfg)
        key = cache.call_key("probe", 0, request_digest(type("R", (), {
            "model": cfg["model"], "contents": [], "config": gc})()))
        if key in seen:
            return [Issue(i.rule, i.severity, i.message, arm) for i in seen[key]]
        hit = cache.get_call(key)
        issues: list = []
        if hit is not None:
            issues = [Issue(**i) for i in hit["issues"]]
        elif not have_credentials():
            issues = [Issue("probe-skipped", "WARN", "no API key; live probe skipped", arm)]
        else:
            try:
                gc.max_output_tokens = 8
                genai.Client().models.generate_content(model=cfg["model"], contents="ping", config=gc)
            except genai_errors.ClientError as e:
                if e.code == 400 or e.code == 404:
                    issues = [Issue("probe-rejected", "INCOMPATIBLE", f"API rejected the config: {e.message}", arm)]
                else:
                    issues = [Issue("probe-error", "WARN", f"probe failed with {e.code}", arm)]
            cache.put_call(key, {"issues": [i.__dict__ for i in issues]})
        seen[key] = issues
        return [Issue(i.rule, i.severity, i.message, arm) for i in issues]

    return probe
