"""A deterministic fake Gemini for offline tests of the real ADK wiring.

Registered for model ids matching `fake-.*`. It plays a compliant agent from
the conversation so far: authenticate by email, read the order, ask for
confirmation, act after the user says yes, then submit_outcome. Model id
`fake-hasty` acts without waiting for confirmation; `fake-rogue` first tries
a tool that does not exist (must fail closed). Every call is counted.
"""

from __future__ import annotations

import re
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.models.registry import LLMRegistry
from google.genai import types

CALLS: list[str] = []
KILL_AT: list[int] = []  # when set, raise Kill once this many calls have completed


class Kill(BaseException):
    """Simulates the process being killed (not an Exception, so nothing retries it)."""


_USAGE = types.GenerateContentResponseUsageMetadata(prompt_token_count=1, candidates_token_count=1, total_token_count=2)


def _fc(name: str, **args) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]),
                       model_version="fake-001", usage_metadata=_USAGE)


def _text(t: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]), model_version="fake-001",
                       usage_metadata=_USAGE)


class FakeGemini(BaseLlm):
    model: str = "fake-careful"

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"fake-.*"]

    async def generate_content_async(self, llm_request, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        if KILL_AT and len(CALLS) >= KILL_AT[0]:
            raise Kill()
        CALLS.append(self.model)
        user_texts, responses = [], []
        for c in llm_request.contents:
            for p in c.parts or []:
                if p.text and c.role == "user":
                    user_texts.append(p.text)
                if p.function_response:
                    responses.append(p.function_response.name)
        first = user_texts[0] if user_texts else ""
        last_user_idx = max((i for i, c in enumerate(llm_request.contents) if c.role == "user"
                             and any(p.text for p in (c.parts or []))), default=0)
        new_responses = [p.function_response.name for c in llm_request.contents[last_user_idx:] for p in (c.parts or [])
                         if p.function_response]
        email = re.search(r"[\w.]+@example\.com", first)
        order = re.search(r"#W\d+", first)
        said_yes = len(user_texts) > 1 and "yes" in user_texts[-1].lower()
        hasty = self.model == "fake-hasty"
        if self.model == "fake-rogue" and "issue_refund" not in responses:
            yield _fc("issue_refund", order_id=order.group(0) if order else "")
            return
        if "submit_outcome" in responses:
            yield _text("Done.")
            return
        if "cancel_pending_order" in responses:
            yield _fc("submit_outcome", outcome="cancelled", order_id=order.group(0))
            return
        if "find_user_id_by_email" not in responses:
            yield _fc("find_user_id_by_email", email=email.group(0))
            return
        if "get_order_details" not in responses:
            yield _fc("get_order_details", order_id=order.group(0))
            return
        if hasty or said_yes:
            yield _fc("cancel_pending_order", order_id=order.group(0), reason="no longer needed")
            return
        if new_responses:
            yield _text(f"I can cancel {order.group(0)} (reason: no longer needed). Do you confirm (yes/no)?")
            return
        yield _text("Could you confirm?")


LLMRegistry.register(FakeGemini)
