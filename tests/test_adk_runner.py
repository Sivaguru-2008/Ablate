"""Steps 4-5 offline: the real ADK runner + recorder plugin against a fake
Gemini, the scheduler, and kill-and-resume with zero repeated API calls.

The live exit test (20 trials on Flash-Lite) needs GOOGLE_API_KEY; see
test_live_flash_lite below, skipped without a key.
"""

import asyncio
import os

import pytest

from ablate import differ
from ablate.plan import ArmSpec, Plan
from ablate.runner.cache import DiskCache
from ablate.runner.scheduler import RateLimiter, run_one, run_plan
from ablate.scenarios import load_scenarios

SCN = ("s01_cancel_email", "s02_cancel_email", "s03_cancel_email")


def setup(root, models=("fake-careful", "fake-hasty")):
    base = differ.load_config(root / "configs" / "base.yaml")
    scen = {s["id"]: s for s in load_scenarios(root / "scenarios" / "retail.yaml")}
    configs = {"base": dict(base, model=models[0]), "cand": dict(base, model=models[1])}
    plan = Plan(scenarios=SCN, units=(), arms=(ArmSpec("base", "base", None, "b"), ArmSpec("cand", "cand", None, "c")),
                k=2, agent="adk")
    return plan, configs, scen


def agent(cache):
    from ablate.agents.adk_agent import AdkAgent
    return AdkAgent(cache=cache, limiter=RateLimiter(None))


def test_single_trial_routes_tools_to_world(root, tmp_path, fake_key):
    plan, configs, scen = setup(root, ("fake-careful", "fake-rogue"))
    cache = DiskCache(tmp_path)
    ok = asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0, agent(cache), cache))
    assert ok.status == "OK" and not ok.violated
    assert [c["tool"] for c in ok.trace] == ["find_user_id_by_email", "get_order_details", "cancel_pending_order"]
    assert ok.structured_output == {"outcome": "cancelled", "order_id": "#W5065081"}
    assert ok.model_version == "fake-001" and len(ok.state_hash) == 64
    rogue = asyncio.run(run_one(plan, configs["cand"], scen["s01_cancel_email"], "cand", 0, agent(cache), cache))
    assert rogue.trace[0]["tool"] == "issue_refund" and rogue.trace[0]["blocked"]
    assert "forbidden_action" in rogue.contracts


def test_tool_descriptions_come_from_config(root):
    from ablate.agents.adk_agent import build_tools
    base = differ.load_config(root / "configs" / "base.yaml")
    base["tools"]["get_order_details"]["description"] = "CUSTOM DESCRIPTION"
    tools = {t.name: t for t in build_tools(base)}
    decl = tools["get_order_details"]._get_declaration()
    assert decl.description.startswith("CUSTOM DESCRIPTION")
    schema = decl.parameters_json_schema or decl.parameters.model_dump(exclude_none=True)
    assert schema["required"] == ["order_id"] and "order_id" in schema["properties"]
    assert "submit_outcome" in tools


def test_kill_mid_run_then_resume_repeats_no_api_calls(root, tmp_path, fake_key):
    fake = fake_key
    plan, configs, scen = setup(root)
    fake.CALLS.clear()
    fake.KILL_AT.clear()
    clean = asyncio.run(run_plan(plan, configs, scen, agent(DiskCache(tmp_path / "clean")), DiskCache(tmp_path / "clean"),
                                 concurrency=1))
    total = len(fake.CALLS)
    assert total > 10

    cache = DiskCache(tmp_path / "killed")
    fake.CALLS.clear()
    fake.KILL_AT[:] = [total // 2]
    with pytest.raises(fake.Kill):
        asyncio.run(run_plan(plan, configs, scen, agent(cache), cache, concurrency=1))
    before = len(fake.CALLS)
    assert before == total // 2

    fake.KILL_AT.clear()
    fake.CALLS.clear()
    resumed = asyncio.run(run_plan(plan, configs, scen, agent(cache), cache, concurrency=1))
    after = len(fake.CALLS)
    assert before + after == total, "a resumed run must not repeat any model call"
    assert {r.key: r.state_hash for r in resumed} == {r.key: r.state_hash for r in clean}

    fake.CALLS.clear()
    again = asyncio.run(run_plan(plan, configs, scen, agent(cache), cache, concurrency=1))
    assert len(fake.CALLS) == 0 and len(again) == len(clean)


def test_infra_errors_retry_then_invalid(root, tmp_path):
    from ablate.agents.base import AgentInfraError

    class Flaky:
        name = "flaky"

        def __init__(self):
            self.n = 0

        async def run_trial(self, *a, **k):
            self.n += 1
            raise AgentInfraError("429 RESOURCE_EXHAUSTED")

    plan, configs, scen = setup(root)
    cache = DiskCache(tmp_path)
    fl = Flaky()

    async def nosleep(_):
        return None

    r = asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0, fl, cache, max_retries=3, sleep=nosleep))
    assert r.status == "INVALID" and fl.n == 4 and "429" in r.error


def test_rate_limiter_blocks_when_full():
    async def go():
        rl = RateLimiter(2)
        await rl.acquire()
        await rl.acquire()
        try:
            await asyncio.wait_for(rl.acquire(), timeout=0.2)
        except asyncio.TimeoutError:
            return True
        return False

    assert asyncio.run(go())


@pytest.mark.skipif(not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")),
                    reason="live exit test needs GOOGLE_API_KEY")
def test_live_flash_lite_20_trials(root, tmp_path):
    base = differ.load_config(root / "configs" / "base.yaml")
    scen = load_scenarios(root / "scenarios" / "retail.yaml")[:4]
    plan = Plan(scenarios=tuple(s["id"] for s in scen), units=(), arms=(ArmSpec("base", "base", None, "b"),), k=5, agent="adk")
    cache = DiskCache(tmp_path)
    res = asyncio.run(run_plan(plan, {"base": base}, {s["id"]: s for s in scen}, agent(cache), cache, concurrency=2))
    assert len(res) == 20
    assert all(len(r.state_hash) == 64 for r in res if r.status == "OK")
    assert sum(r.status == "OK" for r in res) >= 18


def test_non_retryable_errors_are_invalid_at_once(root, tmp_path):
    from ablate.agents.base import AgentInfraError

    class Broken:
        name = "broken"
        n = 0

        async def run_trial(self, *a, **k):
            Broken.n += 1
            raise AgentInfraError("malformed function call", retryable=False)

    plan, configs, scen = setup(root)
    r = asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0, Broken(), DiskCache(tmp_path)))
    assert r.status == "INVALID" and Broken.n == 1


def test_missing_key_stops_instead_of_retrying(root, tmp_path, monkeypatch):
    import fake_llm  # noqa: F401

    from ablate.agents.base import MissingCredentials
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    plan, configs, scen = setup(root)
    cache = DiskCache(tmp_path)
    with pytest.raises(MissingCredentials):
        asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0, agent(cache), cache))


def test_daily_quota_stops_and_retry_hint_is_used(root, tmp_path):
    from google.genai import errors as genai_errors

    from ablate.agents.adk_agent import classify_error
    from ablate.agents.base import QuotaExhausted

    def err(msg):
        return genai_errors.ClientError(429, {"error": {"code": 429, "message": msg, "status": "RESOURCE_EXHAUSTED"}})

    class Boom:
        name = "boom"

        def __init__(self, msg):
            self.msg = msg

        async def run_trial(self, *a, **k):
            try:
                raise RuntimeError("Error in plugin") from err(self.msg)  # ADK-style wrapping
            except Exception as e:
                raise classify_error(e) from None

    plan, configs, scen = setup(root)
    cache = DiskCache(tmp_path)
    with pytest.raises(QuotaExhausted):
        asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0,
                            Boom("Quota exceeded for metric: GenerateRequestsPerDayPerProjectPerModel"), cache))
    slept = []

    async def rec(d):
        slept.append(d)

    r = asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0,
                            Boom("per-minute limit. Please retry in 45.4s."), cache, max_retries=2, sleep=rec))
    assert r.status == "INVALID" and len(slept) == 2 and all(d >= 45.4 for d in slept)


def test_local_model_server_errors_are_retryable():
    litellm = pytest.importorskip("litellm")
    from ablate.agents.adk_agent import classify_error
    try:
        raise RuntimeError("wrapped by ADK") from litellm.exceptions.APIConnectionError(
            message="connection refused", llm_provider="ollama_chat", model="ollama_chat/qwen2.5:7b")
    except RuntimeError as e:
        c = classify_error(e)
    assert c.retryable and type(c).__name__ == "AgentInfraError"


def test_model_version_log_and_drift_stop(root, tmp_path, fake_key):
    import json
    from ablate.agents.adk_agent import AdkAgent
    from ablate.agents.base import ModelVersionDrift
    from ablate.agents.recorder_plugin import RecorderPlugin
    from ablate.world.retail.tools import RetailWorld
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    plan, configs, scen = setup(root, ("fake-careful", "fake-hasty"))
    cache = DiskCache(tmp_path / "cache")
    vlog = tmp_path / "model_versions.jsonl"
    ag = AdkAgent(cache=cache, limiter=RateLimiter(None), version_log=vlog)
    r = asyncio.run(run_one(plan, configs["base"], scen["s01_cancel_email"], "base", 0, ag, cache))
    assert r.status == "OK" and vlog.exists()
    rows = [json.loads(line) for line in vlog.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 3 and all(row["model_version"] == "fake-001" for row in rows)

    # Pinned-id mismatch immediately raises ModelVersionDrift
    p = RecorderPlugin(world=RetailWorld(0), cache=cache, trial_key="k", limiter=RateLimiter(None),
                       toolset=set(), model_requested="gemini-3.5-flash-lite",
                       pinned_model="gemini-3.5-flash-lite", version_log=vlog)
    p._pending = "dummy"
    drifted = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="hi")]),
                          model_version="gemini-3.5-flash-lite-2026-99")
    with pytest.raises(ModelVersionDrift, match="DRIFT"):
        asyncio.run(p.after_model_callback(callback_context=None, llm_response=drifted))
