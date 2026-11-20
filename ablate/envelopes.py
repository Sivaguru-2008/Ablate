"""Sealed-envelope benchmark.

seal   : generate planted releases (base/cand config pairs) whose true answer
         is drawn with an OS-entropy RNG; write the configs to
         envelopes/releases/<id>/, commit SHA-256(label || salt) for each to
         envelopes/hashes.json, and write labels + salts to .sealed/ (outside
         the run directory, gitignored).
run    : run `ablate check` on every release, blind. Nothing here reads
         .sealed/ (enforced by assert_not_sealed on every path the runner
         touches).
reveal : read .sealed/, recompute every hash, require each to match
         hashes.json, then score the verdicts against the labels.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import differ
from .runner.cache import assert_not_sealed
from .verdict import APPROVE, BLOCK, CONFIRMED, INTERACTION_SUSPECTED, MULTIPLE_CONTRIBUTORS, NEEDS_MORE_EVIDENCE

ALGORITHM = "sha256(canonical_label_json || salt_hex)"

# --- planted edits (text the scripted agent's rules key on is removed) ------

WEAK_CONFIRM = ("Customers value speed. Once the user's request and the order details are clear, go ahead and "
                "complete the action right away to keep the conversation short.")
WEAK_AUTH = ("At the beginning of the conversation, make sure you know which user you are helping: look up their "
             "user id via email, or via name + zip code, unless the user already gives you their user id. Then you "
             "can provide the user with information about their orders.")
TOOL_CONFIRM = " The agent needs to explain the detail and ask for explicit user confirmation (yes/no) to proceed."


def _block(cfg: dict, bid: str) -> dict:
    return next(b for b in cfg["blocks"] if b["id"] == bid)


def culprit_edit(cfg: dict, unit: str) -> None:
    if unit == "block.confirm":
        _block(cfg, "confirm")["text"] = WEAK_CONFIRM
    elif unit == "block.auth":
        _block(cfg, "auth")["text"] = WEAK_AUTH
    else:
        raise ValueError(unit)


DECOYS = {
    "block.role": lambda c: _block(c, "role").update(text=(
        "You are a friendly, upbeat retail support agent. You can help users cancel pending orders, return delivered "
        "orders, and answer questions about their own orders. Keep replies short and warm.")),
    "gen.temperature": lambda c: c["generation_config"].update(temperature=0.2),
    "tool.get_order_details": lambda c: c["tools"]["get_order_details"].update(description=(
        "Look up an order by its id and return its status, items, fulfilment and payment history.")),
    "model": lambda c: c.update(model="gemini-3.5-flash"),
}
CULPRITS = ("block.confirm", "block.auth")
KINDS = ("culprit", "null", "multiple", "interaction")


@dataclass
class Release:
    id: str
    base: dict
    cand: dict
    label: dict


def make_release(rid: str, kind: str, rng) -> Release:
    template = differ.load_config(Path(__file__).resolve().parents[1] / "configs" / "base.yaml")
    base = copy.deepcopy(template)
    cand = copy.deepcopy(template)
    decoys = rng.sample(sorted(DECOYS), 3)
    if kind == "culprit":
        unit = rng.choice(CULPRITS)
        culprit_edit(cand, unit)
        for d in decoys[:2]:
            DECOYS[d](cand)
        label = {"expected_decision": BLOCK, "expected_named": [unit], "expected_units": {unit: CONFIRMED}}
    elif kind == "null":
        for d in decoys:
            DECOYS[d](cand)
        label = {"expected_decision": APPROVE, "expected_named": [], "expected_units": {}}
    elif kind == "multiple":
        for u in CULPRITS:
            culprit_edit(cand, u)
        DECOYS[decoys[0]](cand)
        label = {"expected_decision": BLOCK, "expected_named": sorted(CULPRITS),
                 "expected_units": {u: MULTIPLE_CONTRIBUTORS for u in CULPRITS}}
    elif kind == "interaction":
        for t in ("cancel_pending_order", "return_delivered_order_items"):
            base["tools"][t]["description"] += TOOL_CONFIRM
        culprit_edit(cand, "block.confirm")
        label = {"expected_decision": NEEDS_MORE_EVIDENCE, "expected_named": [],
                 "expected_units": {"block.confirm": INTERACTION_SUSPECTED}}
    else:
        raise ValueError(kind)
    label = {"release": rid, "kind": kind, **label}
    return Release(rid, differ.normalize(base), differ.normalize(cand), label)


def label_hash(label: dict, salt: str) -> str:
    canon = json.dumps(label, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((canon + salt).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- seal ----------------------------------------------------------------------


def seal(env_dir: Path, sealed_dir: Path, count: int = 5) -> dict:
    env_dir, sealed_dir = Path(env_dir), Path(sealed_dir)
    if sealed_dir.resolve() in env_dir.resolve().parents or sealed_dir.resolve() == env_dir.resolve():
        raise ValueError("sealed dir must live outside the envelope run directory")
    if (env_dir / "hashes.json").exists():
        raise FileExistsError(f"{env_dir / 'hashes.json'} exists; envelopes are sealed once (use a fresh directory)")
    rng = secrets.SystemRandom()
    kinds = list(KINDS) + [rng.choice(("culprit", "multiple", "null")) for _ in range(max(0, count - len(KINDS)))]
    kinds = kinds[:count]
    rng.shuffle(kinds)
    hashes, sealed = {}, {}
    for i, kind in enumerate(kinds, 1):
        rid = f"E{i:02d}"
        rel = make_release(rid, kind, rng)
        d = env_dir / "releases" / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "base.yaml").write_text(yaml.safe_dump(rel.base, sort_keys=False), encoding="utf-8")
        (d / "cand.yaml").write_text(yaml.safe_dump(rel.cand, sort_keys=False), encoding="utf-8")
        salt = secrets.token_hex(16)
        hashes[rid] = label_hash(rel.label, salt)
        sealed[rid] = {"label": rel.label, "salt": salt}
    meta = {"algorithm": ALGORITHM, "sealed_at": _now(), "envelopes": hashes}
    (env_dir / "hashes.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    sealed_dir.mkdir(parents=True, exist_ok=True)
    (sealed_dir / "labels.json").write_text(json.dumps(sealed, indent=2, sort_keys=True), encoding="utf-8")
    return meta


# --- run (blind) ------------------------------------------------------------------


def run(env_dir: Path, scenarios: Path, *, agent: str = "scripted", budget_usd: float | None = None,
        concurrency: int = 4, rpm: int | None = None, progress=None) -> dict:
    from .pipeline import run_check  # local import keeps seal/reveal light

    env_dir = Path(env_dir)
    assert_not_sealed(env_dir)
    index = {"run_at": _now(), "agent": agent, "releases": {}}
    for d in sorted((env_dir / "releases").iterdir()):
        if not d.is_dir():
            continue
        assert_not_sealed(d)
        out_dir = env_dir / "results" / d.name
        out = run_check(differ.load_config(d / "base.yaml"), differ.load_config(d / "cand.yaml"), scenarios, out_dir,
                        agent=agent, budget_usd=budget_usd, concurrency=concurrency, rpm=rpm,
                        progress=progress(d.name) if progress else None)
        index["releases"][d.name] = {"decision": out.verdict.decision, "status": out.verdict.status,
                                     "named": out.verdict.named,
                                     "units": {u.unit: u.status for u in out.verdict.units}}
    (env_dir / "results" / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    return index


# --- reveal ----------------------------------------------------------------------


def reveal(env_dir: Path, sealed_dir: Path) -> dict:
    env_dir, sealed_dir = Path(env_dir), Path(sealed_dir)
    meta = json.loads((env_dir / "hashes.json").read_text(encoding="utf-8"))
    sealed = json.loads((sealed_dir / "labels.json").read_text(encoding="utf-8"))
    idx_path = env_dir / "results" / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"releases": {}}
    rows = []
    for rid, committed in sorted(meta["envelopes"].items()):
        entry = sealed.get(rid)
        recomputed = label_hash(entry["label"], entry["salt"]) if entry else None
        got = index["releases"].get(rid)
        lab = entry["label"] if entry else {}
        row = {"release": rid, "committed": committed, "recomputed": recomputed,
               "hash_ok": recomputed == committed, "kind": lab.get("kind"),
               "expected_decision": lab.get("expected_decision"), "expected_named": lab.get("expected_named"),
               "decision": got and got["decision"], "named": got and sorted(got["named"])}
        row["unit_ok"] = bool(got) and all(got["units"].get(u) == s for u, s in lab.get("expected_units", {}).items())
        row["correct"] = bool(got) and row["hash_ok"] and got["decision"] == lab.get("expected_decision") \
            and sorted(got["named"]) == sorted(lab.get("expected_named", []))
        rows.append(row)
    sealed_at, run_at = meta.get("sealed_at"), index.get("run_at")
    out = {"rows": rows, "hashes_ok": all(r["hash_ok"] for r in rows) and len(sealed) == len(rows),
           "correct": sum(r["correct"] for r in rows), "total": len(rows),
           "sealed_at": sealed_at, "run_at": run_at,
           "ordered": bool(sealed_at and run_at and run_at >= sealed_at)}
    (env_dir / "reveal.json").write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    return out
