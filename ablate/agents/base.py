"""Agent interface shared by the scripted agent and the ADK agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Fixed harness instruction appended to every config's prompt. It is not a
# config unit, so it can never be ablated.
HARNESS_INSTRUCTION = (
    "When the user's request is resolved (completed, refused, or failed), call the tool "
    "submit_outcome exactly once with outcome set to one of: cancelled, return_requested, "
    "info, refused, failed; and order_id set to the order the request was about."
)


class AgentInfraError(RuntimeError):
    """Infrastructure failure: the trial is INVALID. Retryable ones (429, 5xx,
    timeouts) are retried with backoff first; others are INVALID at once."""

    def __init__(self, message: str, *, retryable: bool = True, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class QuotaExhausted(RuntimeError):
    """A daily (not per-minute) quota is used up. The run stops; finished
    trials and model calls stay cached, so rerunning later resumes."""


class MissingCredentials(RuntimeError):
    """A model call is needed and not cached, and no API key is configured."""


@dataclass
class AgentRun:
    structured_output: dict | None
    model_version: str | None
    api_calls: int = 0
    cached_calls: int = 0
    transcript: list[dict] = field(default_factory=list)


class Agent(Protocol):
    name: str

    async def run_trial(self, config: dict, scenario: dict, world, *, seed: int, cache_key: str) -> AgentRun: ...
