"""The verdict engine: a pure function `verdict(plan, results) -> Verdict`.

No I/O, no clock, no randomness except numpy generators seeded from
`plan.seed` (inside ablate.stats.tests). No LLM anywhere on this path.

Rules (spec v2.2):
  1. A/A: unadjusted two-sided alpha test of the replicate base arm against
     base, plus one constant returned model version. Either fails -> DRIFT.
  2. INVALID rates differ between arms (Fisher, alpha) -> INFRA_SUSPECTED.
  3. cand vs base not significantly worse -> NOT_REPRODUCED.
  4. Per revertible unit X, Holm across units within each direction:
       nec(X): cand-X non-inferior to base (NI bound < delta)
       suf(X): base+X significantly worse than base
       nec & suf -> CONFIRMED; suf & !nec -> MULTIPLE_CONTRIBUTORS;
       nec & !suf -> INTERACTION_SUSPECTED; neither -> INCONCLUSIVE (priced).
  5. PR decision: BLOCK / NEEDS MORE EVIDENCE / APPROVE.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction

import numpy as np

from .plan import Plan
from .stats import tests as st

# Release-level statuses
DRIFT = "DRIFT"
INFRA_SUSPECTED = "INFRA_SUSPECTED"
NOT_REPRODUCED = "NOT_REPRODUCED"
REPRODUCED = "REPRODUCED"
INCOMPATIBLE = "INCOMPATIBLE"

# Unit statuses
CONFIRMED = "CONFIRMED"
MULTIPLE_CONTRIBUTORS = "MULTIPLE_CONTRIBUTORS"
INTERACTION_SUSPECTED = "INTERACTION_SUSPECTED"
INCONCLUSIVE = "INCONCLUSIVE"
NOT_REVERTIBLE = "NOT_REVERTIBLE"
NOT_TESTED = "NOT_TESTED"

# PR decisions
APPROVE = "APPROVE"
BLOCK = "BLOCK"
NEEDS_MORE_EVIDENCE = "NEEDS MORE EVIDENCE"

NAMED = (CONFIRMED, MULTIPLE_CONTRIBUTORS)


@dataclass
class ArmCounts:
    viol: np.ndarray
    valid: np.ndarray
    invalid: np.ndarray


@dataclass
class UnitVerdict:
    unit: str
    status: str
    nec: bool | None = None
    suf: bool | None = None
    ni_diff: float | None = None
    ni_upper: float | None = None
    ni_level: float | None = None
    ni_p: float | None = None
    ni_p_holm: float | None = None
    suf_diff: float | None = None
    suf_p: float | None = None
    suf_p_holm: float | None = None
    affected: int | None = None
    more_scenarios: int | None = None
    more_usd: float | None = None
    note: str = ""


@dataclass
class Verdict:
    plan_hash: str
    status: str
    decision: str
    aa_p: float | None = None
    model_versions: dict = field(default_factory=dict)
    infra_p: dict = field(default_factory=dict)
    repro_p: float | None = None
    repro_diff: float | None = None
    base_rate: float | None = None
    cand_rate: float | None = None
    equivalence: tuple[float, float] | None = None
    affected_floor: int | None = None
    units: list[UnitVerdict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    next_step: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["equivalence"] = list(self.equivalence) if self.equivalence else None
        return d

    @property
    def named(self) -> list[str]:
        return [u.unit for u in self.units if u.status in NAMED]


# ---------------------------------------------------------------------------
# tabulation


def tabulate(plan: Plan, results) -> tuple[dict[str, ArmCounts], dict[str, list[str]]]:
    """Per-arm, per-scenario counts aligned to plan.scenarios, plus the
    returned model versions grouped by requested model id."""
    idx = {s: i for i, s in enumerate(plan.scenarios)}
    arms = set(plan.arm_names)
    n = plan.n
    counts = {a: ArmCounts(np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n, np.int64)) for a in arms}
    versions: dict[str, set[str]] = {}
    seen: set[tuple] = set()
    for r in results:
        if r.plan_hash != plan.plan_hash:
            raise ValueError(f"result {r.key} belongs to plan {r.plan_hash[:12]}, not {plan.plan_hash[:12]}")
        if r.arm not in arms or r.scenario not in idx:
            raise ValueError(f"result {r.key} is outside the frozen plan")
        if r.key in seen:
            raise ValueError(f"duplicate result {r.key}")
        seen.add(r.key)
        c = counts[r.arm]
        i = idx[r.scenario]
        if r.status == "INVALID":
            c.invalid[i] += 1
            continue
        c.valid[i] += 1
        c.viol[i] += int(bool(r.violated))
        if r.model_version:
            versions.setdefault(r.model_requested or "?", set()).add(r.model_version)
    return counts, {m: sorted(vs) for m, vs in sorted(versions.items())}


def _paired(counts: dict[str, ArmCounts], t: str, r: str):
    ct, cr = counts[t], counts[r]
    mask = (ct.valid > 0) & (cr.valid > 0)
    diffs = [
        Fraction(int(ct.viol[i]), int(ct.valid[i])) - Fraction(int(cr.viol[i]), int(cr.valid[i]))
        for i in np.flatnonzero(mask)
    ]
    return mask, diffs


def _cmp(counts, t, r, plan: Plan, salt: int):
    ct, cr = counts[t], counts[r]
    mask = (ct.valid > 0) & (cr.valid > 0)
    return st.pooled_comparison(ct.viol[mask], ct.valid[mask], cr.viol[mask], cr.valid[mask],
                                B=plan.bootstrap_B, seed=plan.seed + salt)


def _usd(plan: Plan, n_more: int | None) -> float | None:
    if n_more is None:
        return None
    return round(n_more * len(plan.arms) * plan.k * plan.cost_per_trial_usd, 2)


# ---------------------------------------------------------------------------
# the engine


def verdict(plan: Plan, results) -> Verdict:
    counts, versions = tabulate(plan, results)
    return verdict_from_counts(plan, counts, versions)


def verdict_from_counts(plan: Plan, counts: dict[str, ArmCounts], versions: dict[str, list[str]]) -> Verdict:
    a = plan.alpha
    U = len(plan.revertible)
    floor = st.affected_scenario_floor(plan.n, max(1, U), a, plan.flips)
    v = Verdict(plan_hash=plan.plan_hash, status="", decision=NEEDS_MORE_EVIDENCE,
                model_versions=dict(versions), affected_floor=floor)
    for u in plan.units:
        if u.status != "REVERTIBLE":
            v.units.append(UnitVerdict(unit=u.id, status=u.status, note=u.reason))

    if plan.release_status == INCOMPATIBLE:
        v.status = INCOMPATIBLE
        v.reasons.append("base or candidate config is rejected by the model API (preflight)")
        v.next_step = "Fix the rejected config; nothing was run."
        return v

    base, cand, aa = "base", "cand", "aa"

    # 1. A/A + model version --------------------------------------------------
    _, aa_diffs = _paired(counts, aa, base)
    v.aa_p = st.sign_flip_p(aa_diffs, alternative="two-sided", flips=plan.flips, seed=plan.seed)
    drift = []
    if v.aa_p < a:
        drift.append(f"A/A replicate differs from base (two-sided p = {v.aa_p:.4f} < {a})")
    for req, got in versions.items():
        if len(got) > 1:
            drift.append(f"returned model version for {req} changed mid-run: {', '.join(got)}")
    if drift:
        v.status = DRIFT
        v.reasons += drift
        v.next_step = "Re-run on a pinned model version; no attribution while the A/A control is not clean."
        return v

    # 2. INVALID rates ----------------------------------------------------------
    cb = counts[base]
    for arm in plan.arm_names:
        if arm == base:
            continue
        c = counts[arm]
        p = st.fisher_exact(int(c.invalid.sum()), int(c.valid.sum()), int(cb.invalid.sum()), int(cb.valid.sum()))
        v.infra_p[arm] = p
        if p < a:
            v.reasons.append(f"INVALID rate of arm {arm} differs from base (Fisher p = {p:.4f})")
    if v.reasons:
        v.status = INFRA_SUSPECTED
        v.next_step = "Fix the infrastructure errors (rate limits, timeouts) and re-run the affected arms."
        return v

    # 3. reproduction -------------------------------------------------------------
    _, rd = _paired(counts, cand, base)
    v.repro_p = st.sign_flip_p(rd, alternative="greater", flips=plan.flips, seed=plan.seed)
    cmp_cb = _cmp(counts, cand, base, plan, salt=1)
    v.repro_diff = cmp_cb.diff
    v.base_rate = float(cb.viol.sum() / max(1, cb.valid.sum()))
    v.cand_rate = float(counts[cand].viol.sum() / max(1, counts[cand].valid.sum()))
    if v.repro_p >= a:
        v.status = NOT_REPRODUCED
        lo, hi = cmp_cb.lower(a), cmp_cb.upper(a)
        v.equivalence = (lo, hi)
        if -plan.delta < lo and hi < plan.delta:
            v.decision = APPROVE
            v.next_step = "No regression beyond the margin under this scenario mix."
        else:
            v.decision = NEEDS_MORE_EVIDENCE
            gap = plan.delta - abs(v.repro_diff)
            n_req = st.more_scenarios_needed(rd, effect=gap, alpha=a, k=plan.k) if gap > 0 else None
            n_more = None if n_req is None else max(0, n_req - plan.n)
            head = f"Equivalence interval [{lo:+.3f}, {hi:+.3f}] is not inside ±{plan.delta:.2f}"
            v.next_step = (f"{head}: ~{n_more} more paired scenarios ≈ ${_usd(plan, n_more):,.2f}"
                           if n_more is not None else f"{head}; the observed gap is at the margin.")
        for uid in plan.revertible:
            v.units.append(UnitVerdict(unit=uid, status=NOT_TESTED, note="regression not reproduced"))
        return v
    v.status = REPRODUCED

    # 4. per-unit necessity / sufficiency -------------------------------------
    suf_p, suf_diff, affected, suf_diffs = {}, {}, {}, {}
    ni_p, ni_cmp = {}, {}
    for j, uid in enumerate(plan.revertible):
        bp = plan.arm("base_plus", uid).name
        cm = plan.arm("cand_minus", uid).name
        _, d = _paired(counts, bp, base)
        suf_diffs[uid] = d
        suf_p[uid] = st.sign_flip_p(d, alternative="greater", flips=plan.flips, seed=plan.seed)
        suf_diff[uid] = float(sum(d) / len(d)) if d else 0.0
        affected[uid] = sum(1 for x in d if x > 0)
        c = _cmp(counts, cm, base, plan, salt=100 + j)
        ni_cmp[uid] = c
        ni_p[uid] = st.ni_p_value(c, plan.delta)

    h_suf = st.holm(suf_p, a)
    h_ni = st.holm(ni_p, a)
    order = sorted(ni_p, key=lambda u: (ni_p[u], u))
    level = {u: a / (U - i) for i, u in enumerate(order)}

    for uid in plan.revertible:
        # DECISION: suf also requires >= floor scenarios moving the bad way.
        # It can only remove a sufficiency claim, never add one.
        suf = bool(h_suf[uid][1]) and affected[uid] >= floor
        nec = bool(h_ni[uid][1])
        c = ni_cmp[uid]
        uv = UnitVerdict(
            unit=uid, status="", nec=nec, suf=suf,
            ni_diff=c.diff, ni_upper=c.upper(level[uid]), ni_level=level[uid],
            ni_p=ni_p[uid], ni_p_holm=h_ni[uid][0],
            suf_diff=suf_diff[uid], suf_p=suf_p[uid], suf_p_holm=h_suf[uid][0],
            affected=affected[uid],
        )
        if nec and suf:
            uv.status = CONFIRMED
        elif suf:
            uv.status = MULTIPLE_CONTRIBUTORS
            uv.note = "adding it alone hurts, but reverting it alone does not restore base"
        elif nec:
            uv.status = INTERACTION_SUSPECTED
            uv.note = "reverting it restores base, but adding it alone does not hurt"
        else:
            uv.status = INCONCLUSIVE
            a_u = a / max(1, U)
            n_suf = st.more_scenarios_needed(suf_diffs[uid], effect=max(suf_diff[uid], plan.delta), alpha=a_u, k=plan.k)
            _, nd = _paired(counts, plan.arm("cand_minus", uid).name, base)
            n_ni = (st.more_scenarios_needed(nd, effect=plan.delta - c.diff, alpha=a_u, k=plan.k)
                    if c.diff < plan.delta else None)
            reqs = [x for x in (n_suf, n_ni) if x is not None]
            n_req = max(reqs) if reqs else None
            uv.more_scenarios = None if n_req is None else max(0, n_req - plan.n)
            uv.more_usd = _usd(plan, uv.more_scenarios)
            if uv.more_scenarios is None:
                uv.note = "not resolvable by more scenarios at this margin"
            elif uv.more_scenarios == 0:
                uv.note = f"current n is already powered for an effect of {plan.delta:.0%}; treat any effect as smaller"
            else:
                uv.note = f"{uv.more_scenarios} more paired scenarios ≈ ${uv.more_usd:,.2f}"
        v.units.append(uv)

    # 5. decision ---------------------------------------------------------------
    if any(u.status in NAMED for u in v.units):
        v.decision = BLOCK
        v.next_step = "Revert the named change(s) and re-run the gate."
    else:
        v.decision = NEEDS_MORE_EVIDENCE
        priced = [u for u in v.units if u.more_scenarios]
        if priced:
            u = max(priced, key=lambda x: x.more_scenarios)
            v.next_step = f"Regression reproduced but no change is named: {u.more_scenarios} more paired scenarios ≈ ${u.more_usd:,.2f}"
        else:
            v.next_step = "Regression reproduced but no single change is named; inspect interacting units together."
    return v
