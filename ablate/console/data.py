"""Read-side data for the console. Everything here reads run outputs on disk; nothing calls a model.

Sealed labels are read only through envelopes.reveal_one / envelopes.reveal, i.e. only when a
viewer explicitly opens an envelope.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import envelopes

ALPHA = 0.05


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- statistics ----------------------------------------------------------------


def _binom_cdf(x: int, n: int, p: float) -> float:
    from math import comb
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(x + 1))


def upper_bound(x: int, n: int, alpha: float = ALPHA) -> float | None:
    """One-sided (1 - alpha) Clopper-Pearson upper bound on a rate with x events in n trials."""
    if n <= 0:
        return None
    if x >= n:
        return 1.0
    lo, hi = x / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _binom_cdf(x, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


# --- assurance (sealed envelopes) ----------------------------------------------------


def assurance(env_dir: Path, sealed_dir: Path, revealed: set[str]) -> dict:
    env_dir, sealed_dir = Path(env_dir), Path(sealed_dir)
    meta = _read_json(env_dir / "hashes.json")
    if not meta:
        return {"available": False, "env_dir": str(env_dir)}
    index = _read_json(env_dir / "results" / "index.json") or {"releases": {}}
    full = _read_json(env_dir / "reveal.json")
    full_rows = {r["release"]: r for r in (full or {}).get("rows", [])}
    has_labels = (sealed_dir / "labels.json").exists()

    rows = []
    for rid, committed in sorted(meta["envelopes"].items()):
        got = index["releases"].get(rid)
        row = {"release": rid, "committed": committed, "ran": bool(got),
               "decision": got and got["decision"], "status": got and got.get("status"),
               "named": sorted(got["named"]) if got else [],
               "units": got.get("units", {}) if got else {},
               "report": (env_dir / "results" / rid / "report.html").exists(),
               "revealed": False}
        opened = full_rows.get(rid)
        if opened is None and rid in revealed and has_labels:
            opened = envelopes.reveal_one(env_dir, sealed_dir, rid)
        if opened is not None:
            row.update(revealed=True, **{k: opened[k] for k in
                       ("recomputed", "hash_ok", "kind", "expected_decision", "expected_named", "correct", "unit_ok")})
            row["expected_named"] = sorted(row["expected_named"] or [])
        rows.append(row)

    sealed_at, run_at = meta.get("sealed_at"), index.get("run_at")
    return {"available": True, "env_dir": str(env_dir), "algorithm": meta.get("algorithm"),
            "sealed_at": sealed_at, "run_at": run_at, "agent": index.get("agent"),
            "ordered": bool(sealed_at and run_at and run_at >= sealed_at),
            "labels_present": has_labels, "fully_revealed": full is not None,
            "rows": rows, "score": score(rows)}


def score(rows: list[dict]) -> dict:
    opened = [r for r in rows if r["revealed"] and r["ran"]]
    attr = [r for r in opened if r["expected_named"]]
    abst = [r for r in opened if not r["expected_named"]]
    false_attr = [r for r in opened if set(r["named"]) - set(r["expected_named"])]
    return {"scored": len(opened), "total": len(rows),
            "correct_attribution": sum(r["correct"] for r in attr), "attribution_cases": len(attr),
            "correct_abstention": sum(r["correct"] for r in abst), "abstention_cases": len(abst),
            "false_attribution": len(false_attr),
            "false_attribution_upper": upper_bound(len(false_attr), len(opened)),
            "hash_mismatches": sum(not r["hash_ok"] for r in opened)}


# --- releases (finished `ablate check` runs) ----------------------------------------------


def releases(runs_dir: Path) -> list[dict]:
    runs_dir = Path(runs_dir)
    out = []
    if not runs_dir.is_dir():
        return out
    for d in sorted(runs_dir.iterdir()):
        v = _read_json(d / "verdict.json") if d.is_dir() else None
        if not v:
            continue
        plan = _read_json(d / "plan.json") or {}
        out.append({
            "name": d.name, "decision": v.get("decision"), "status": v.get("status"),
            "named": [u["unit"] for u in v.get("units", []) if u.get("status") in ("CONFIRMED", "MULTIPLE_CONTRIBUTORS")],
            "units": [{"unit": u["unit"], "status": u["status"]} for u in v.get("units", [])],
            "base_rate": v.get("base_rate"), "cand_rate": v.get("cand_rate"), "repro_p": v.get("repro_p"),
            "next_step": v.get("next_step"), "plan_hash": v.get("plan_hash"),
            "agent": plan.get("agent"), "model": plan.get("model"), "k": plan.get("k"),
            "n": len(plan.get("scenarios", [])) or None,
            "updated": datetime.fromtimestamp((d / "verdict.json").stat().st_mtime, timezone.utc)
                               .isoformat(timespec="seconds"),
            "files": [f for f in RUN_FILES if (d / f).exists()],
        })
    out.sort(key=lambda r: r["updated"], reverse=True)
    return out


RUN_FILES = ("report.html", "pr_comment.md", "verdict.json", "plan.json", "preflight.json")
