"""World protocol: a deterministic fake environment the agent acts on.

* reset(seed) restores the exact seed state (the world has no randomness of
  its own; the seed is recorded for provenance).
* call(tool, args) returns a JSON-able observation dict. Unknown tools fail
  closed: nothing changes, the call is logged as blocked, and an error comes
  back to the agent as if the tool had failed.
* state_hash() is SHA-256 over canonical JSON of the full state.
* effect_log lists every successful state-changing call.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class World(Protocol):
    effect_log: list[dict]
    call_log: list[dict]

    def reset(self, seed: int) -> None: ...

    def call(self, tool: str, args: dict, *, turn: int = 0) -> dict: ...

    def state_hash(self) -> str: ...

    def snapshot(self) -> dict: ...

    def tool_names(self) -> list[str]: ...
