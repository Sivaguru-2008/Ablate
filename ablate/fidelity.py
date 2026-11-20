"""Fidelity gate: replay recorded tool sequences into the world and require
an exact match of every observation and of the final state hash.

Used to (a) check the world is deterministic over recorded runs, (b) back the
Layer A pytest tests, and (c) accept or reject a drafted tool implementation
(drafter.py) by swapping it in via `overrides`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .world.retail.tools import RetailWorld


@dataclass
class Replay:
    world: RetailWorld
    state_hash: str
    mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def replay(trace: list[dict], *, seed: int = 0, overrides: dict[str, Callable[..., Any]] | None = None) -> Replay:
    w = RetailWorld(seed)
    if overrides:
        w.overrides = dict(overrides)
    mism = []
    for i, c in enumerate(trace):
        obs = w.call(c["tool"], c["args"], turn=c.get("turn", 0))
        if obs != c["observation"]:
            mism.append({"index": i, "tool": c["tool"], "expected": c["observation"], "got": obs})
    return Replay(w, w.state_hash(), mism)


def check_trial(result, *, overrides=None) -> tuple[bool, str]:
    rep = replay(result.trace, overrides=overrides)
    if rep.mismatches:
        m = rep.mismatches[0]
        return False, f"observation mismatch at call {m['index']} ({m['tool']})"
    if rep.state_hash != result.state_hash:
        return False, "final state hash differs"
    return True, "exact match"


def check_results(results, *, overrides=None, tools: set[str] | None = None) -> dict:
    """Replay every OK trial (optionally only those that call one of `tools`)."""
    checked = passed = 0
    failures = []
    for r in results:
        if r.status != "OK":
            continue
        if tools and not any(c["tool"] in tools for c in r.trace):
            continue
        checked += 1
        ok, why = check_trial(r, overrides=overrides)
        if ok:
            passed += 1
        else:
            failures.append({"trial": list(r.key), "why": why})
    return {"checked": checked, "passed": passed, "failures": failures[:20], "ok": checked > 0 and passed == checked}
