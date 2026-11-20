"""Trial result record and JSONL helpers (I/O lives here, not in verdict.py)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TrialResult:
    plan_hash: str
    scenario: str
    arm: str
    trial: int
    status: str = "OK"  # OK | INVALID (infrastructure failure, not a violation)
    violated: bool = False
    contracts: list[str] = field(default_factory=list)  # violated contract ids
    model_version: str | None = None
    state_hash: str = ""
    effects: list[dict] = field(default_factory=list)  # world effect log
    trace: list[dict] = field(default_factory=list)  # [{tool, args, observation, turn}]
    structured_output: dict | None = None
    amount_inr_at_risk: float = 0.0
    api_calls: int = 0
    cached_calls: int = 0
    error: str | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.scenario, self.arm, self.trial)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TrialResult":
        return cls(**d)


def write_jsonl(path: Path, results: list[TrialResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in sorted(results, key=lambda r: (r.arm, r.scenario, r.trial)):
            f.write(json.dumps(r.to_dict(), sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[TrialResult]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(TrialResult.from_dict(json.loads(line)))
    return out
