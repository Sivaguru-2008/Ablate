"""ADK plugin that records model traffic and routes every tool call to the world.

Pattern after google/adk-samples core/python/safety-plugins (Apache-2.0):
a BasePlugin attached to the App/Runner; returning a dict from
before_tool_callback short-circuits the tool and hands that dict to the model
as the tool's result, which is how unknown tools fail closed. No code copied.

* before_model_callback: computes a digest of the request (with ADK's random
  function-call ids stripped) and serves the response from the disk cache if
  this exact call already happened; otherwise waits on the rate limiter.
* after_model_callback: stores the fresh response in the cache and records
  the returned model version (DRIFT is declared if it changes mid-run).
* before_tool_callback: submit_outcome -> structured output; a tool in the
  config's toolset -> world.call; anything else -> world.block (fail closed).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from .base import MissingCredentials

OUTCOME_TOOL = "submit_outcome"


def _strip_ids(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_ids(v) for k, v in obj.items() if k not in ("id", "labels", "http_options")}
    if isinstance(obj, list):
        return [_strip_ids(x) for x in obj]
    return obj


def request_digest(llm_request) -> str:
    payload = {
        "model": llm_request.model,
        "contents": [c.model_dump(mode="json", exclude_none=True) for c in (llm_request.contents or [])],
        "config": llm_request.config.model_dump(mode="json", exclude_none=True) if llm_request.config else None,
    }
    canon = json.dumps(_strip_ids(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def have_credentials() -> bool:
    return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"))


class RecorderPlugin(BasePlugin):
    def __init__(self, *, world, cache, trial_key: str, limiter, toolset: set[str], needs_key: bool = True) -> None:
        super().__init__(name="ablate_recorder")
        self.world = world
        self.cache = cache
        self.trial_key = trial_key
        self.limiter = limiter
        self.toolset = set(toolset)
        self.needs_key = needs_key
        self.turn = 0
        self.call_index = 0
        self.api_calls = 0
        self.cached_calls = 0
        self.versions: list[str] = []
        self.structured_output: dict | None = None
        self.transcript: list[dict] = []
        self._pending: str | None = None

    async def before_model_callback(self, *, callback_context, llm_request) -> Optional[LlmResponse]:
        key = self.cache.call_key(self.trial_key, self.call_index, request_digest(llm_request))
        self.call_index += 1
        hit = self.cache.get_call(key)
        if hit is not None:
            self.cached_calls += 1
            resp = LlmResponse.model_validate(hit["response"])
            if resp.model_version:
                self.versions.append(resp.model_version)
            self._pending = None
            self._log(resp, cached=True)
            return resp
        if self.needs_key and not have_credentials():
            raise MissingCredentials("no GOOGLE_API_KEY / GEMINI_API_KEY set and this model call is not cached")
        await self.limiter.acquire()
        self.api_calls += 1
        self._pending = key
        return None

    async def after_model_callback(self, *, callback_context, llm_response) -> Optional[LlmResponse]:
        if self._pending and not llm_response.partial and not llm_response.error_code:
            self.cache.put_call(self._pending, {"response": llm_response.model_dump(mode="json", exclude_none=True)})
            if llm_response.model_version:
                self.versions.append(llm_response.model_version)
            self._log(llm_response, cached=False)
        self._pending = None
        return None

    async def before_tool_callback(self, *, tool, tool_args, tool_context) -> Optional[dict]:
        name = tool.name
        if name == OUTCOME_TOOL:
            if self.structured_output is None:
                self.structured_output = {"outcome": str(tool_args.get("outcome", "")),
                                          "order_id": str(tool_args.get("order_id", ""))}
            return {"result": "recorded"}
        if name in self.toolset:
            return self.world.call(name, dict(tool_args), turn=self.turn)
        return self.world.block(name, dict(tool_args), turn=self.turn)

    def _log(self, resp: LlmResponse, *, cached: bool) -> None:
        parts = (resp.content.parts if resp.content and resp.content.parts else [])
        text = " ".join(p.text for p in parts if getattr(p, "text", None))
        calls = [p.function_call.name for p in parts if getattr(p, "function_call", None)]
        self.transcript.append({"turn": self.turn, "text": text[:2000], "calls": calls, "cached": cached,
                                "model_version": resp.model_version})
