"""YAML agent config -> typed units; dependency-closed arms.

Unit ids:
  model             the model id string
  gen.<key>         one generation_config key
  block.<id>        one named instruction block
  tool.<name>       one tool's description

Any block or tool may declare `requires: [unit ids]`. A requirement pins the
required unit to the value it has in the same config as the requiring unit.
An arm mixes units from base and cand; it is dependency-closed when every
unit present in the arm has its requirements met with the values from its
own source config. A unit whose cand-X or base+X arm is not closed is
NOT_REVERTIBLE (no arms are run for it).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .world.base import sha256_json

MISSING = None  # unit absent from a config


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str
    base: Any
    cand: Any
    base_requires: tuple[str, ...]
    cand_requires: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.base != self.cand or self.base_requires != self.cand_requires


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return normalize(cfg)


def normalize(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg.setdefault("name", "agent")
    cfg.setdefault("generation_config", {})
    cfg["generation_config"] = cfg["generation_config"] or {}
    cfg.setdefault("blocks", [])
    cfg.setdefault("tools", {})
    ids = [b["id"] for b in cfg["blocks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate block ids")
    for b in cfg["blocks"]:
        b["text"] = " ".join(str(b.get("text", "")).split())
        b["requires"] = list(b.get("requires") or [])
    for name, t in list(cfg["tools"].items()):
        t = dict(t or {})
        t["description"] = " ".join(str(t.get("description", "")).split())
        t["requires"] = list(t.get("requires") or [])
        cfg["tools"][name] = t
    return cfg


def unit_table(cfg: dict) -> dict[str, tuple[Any, tuple[str, ...]]]:
    """unit id -> (value, requires)."""
    t: dict[str, tuple[Any, tuple[str, ...]]] = {"model": (cfg.get("model"), ())}
    for k, v in cfg["generation_config"].items():
        t[f"gen.{k}"] = (v, ())
    for b in cfg["blocks"]:
        t[f"block.{b['id']}"] = (b["text"], tuple(b["requires"]))
    for name, tool in cfg["tools"].items():
        t[f"tool.{name}"] = (tool["description"], tuple(tool["requires"]))
    return t


def diff(base: dict, cand: dict) -> list[Unit]:
    tb, tc = unit_table(base), unit_table(cand)
    out = []
    for uid in sorted(set(tb) | set(tc)):
        bv, br = tb.get(uid, (MISSING, ()))
        cv, cr = tc.get(uid, (MISSING, ()))
        out.append(Unit(uid, uid.split(".")[0], bv, cv, br, cr))
    return out


def changed_units(base: dict, cand: dict) -> list[Unit]:
    return [u for u in diff(base, cand) if u.changed]


def compose(base: dict, cand: dict, from_cand: set[str]) -> dict:
    """Config with every unit from base except those in from_cand."""
    tb, tc = unit_table(base), unit_table(cand)

    def pick(uid):
        return (tc if uid in from_cand else tb).get(uid, (MISSING, ()))

    out = {"name": base.get("name", "agent"), "model": pick("model")[0], "generation_config": {}, "blocks": [], "tools": {}}
    gen_keys = sorted({u[4:] for u in (set(tb) | set(tc)) if u.startswith("gen.")})
    for k in gen_keys:
        v, _ = pick(f"gen.{k}")
        if v is not MISSING:
            out["generation_config"][k] = v
    present = []
    for uid in [f"block.{b['id']}" for b in base["blocks"]] + [f"block.{b['id']}" for b in cand["blocks"]]:
        if uid not in present and pick(uid)[0] is not MISSING:
            present.append(uid)
    base_order = [f"block.{b['id']}" for b in base["blocks"]]
    cand_order = [f"block.{b['id']}" for b in cand["blocks"]]
    order = [u for u in base_order if u in present]
    for i, u in enumerate(cand_order):
        if u in present and u not in order:
            pos = 0
            for prev in reversed(cand_order[:i]):
                if prev in order:
                    pos = order.index(prev) + 1
                    break
            order.insert(pos, u)
    for uid in order:
        text, req = pick(uid)
        out["blocks"].append({"id": uid[len("block."):], "text": text, "requires": list(req)})
    tool_names = list(base["tools"]) + [n for n in cand["tools"] if n not in base["tools"]]
    for n in tool_names:
        desc, req = pick(f"tool.{n}")
        if desc is not MISSING:
            out["tools"][n] = {"description": desc, "requires": list(req)}
    return out


def closure_violations(base: dict, cand: dict, from_cand: set[str]) -> list[str]:
    tb, tc = unit_table(base), unit_table(cand)
    arm = unit_table(compose(base, cand, from_cand))
    problems = []
    for uid in arm:
        src = tc if uid in from_cand else tb
        _, reqs = src.get(uid, (MISSING, ()))
        for r in reqs:
            want = src.get(r, (MISSING, ()))[0]
            have = arm.get(r, (MISSING, ()))[0]
            if have is MISSING or have != want:
                problems.append(f"{uid} requires {r} as in {'cand' if src is tc else 'base'}")
    return problems


def config_hash(cfg: dict) -> str:
    return sha256_json(cfg)


def render_instruction(cfg: dict) -> str:
    return "\n\n".join(f"## {b['id']}\n{b['text']}" for b in cfg["blocks"])
