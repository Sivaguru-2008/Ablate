"""Scenario loading. Scenarios are plain dicts validated for required keys."""

from __future__ import annotations

from pathlib import Path

import yaml

REQUIRED = ("id", "intent", "user_id", "identity", "order_id", "user_turns", "allowed", "expect")
INTENTS = ("cancel", "return", "status")


def load_scenarios(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    items = data["scenarios"] if isinstance(data, dict) else data
    seen = set()
    out = []
    for s in items:
        missing = [k for k in REQUIRED if k not in s]
        if missing:
            raise ValueError(f"scenario {s.get('id', '?')}: missing {missing}")
        if s["intent"] not in INTENTS:
            raise ValueError(f"scenario {s['id']}: unknown intent {s['intent']}")
        if not 1 <= len(s["user_turns"]) <= 3:
            raise ValueError(f"scenario {s['id']}: needs 1-3 user turns")
        if s["id"] in seen:
            raise ValueError(f"duplicate scenario id {s['id']}")
        seen.add(s["id"])
        s.setdefault("confirm_turn", None)
        s.setdefault("volunteers_user_id", None)
        s.setdefault("injection", False)
        s["expect"] = s.get("expect") or {}
        out.append(s)
    return out
