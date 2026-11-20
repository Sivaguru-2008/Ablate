"""Planted-fault simulator: operating characteristics of THIS design.

Draws per-scenario binomial violation counts for every arm of a 3-unit
release and pushes them through the real verdict engine. Reports:

* type I error of the reproduction gate (cand == base),
* familywise false-naming rate under the null (any unit CONFIRMED or
  MULTIPLE_CONTRIBUTORS when nothing changed),
* A/A false-DRIFT rate,
* power to CONFIRM a planted culprit, and how often a decoy gets named,
* MDE (smallest planted effect with >= 80% power to CONFIRM),
* NI power (necessity passes when reverting the culprit restores base),
* the affected-scenario floor.

Scenario heterogeneity: base violation rates ~ Beta(a, b) with mean 5%.
HETEROGENEITY["low"] = Beta(2, 38) (ICC ~ 0.02), ["high"] = Beta(0.5, 9.5)
(ICC ~ 0.09). High heterogeneity inflates the design effect that deflates
the Newcombe NI bound, which caps NI power; both are reported.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..plan import ALPHA, BOOTSTRAP_B, DELTA, FLIPS, K_TRIALS, N_SCENARIOS, SEED, ArmSpec, Plan, UnitSpec, arm_name
from ..verdict import CONFIRMED, NAMED, ArmCounts, verdict_from_counts
from . import tests as st


HETEROGENEITY = {"low": (2.0, 38.0), "high": (0.5, 9.5)}


def design_plan(n: int = N_SCENARIOS, k: int = K_TRIALS, n_units: int = 3, *, delta: float = DELTA,
                alpha: float = ALPHA, B: int = BOOTSTRAP_B, flips: int = FLIPS, seed: int = SEED) -> Plan:
    units = tuple(UnitSpec(f"u{i}") for i in range(n_units))
    arms = [ArmSpec(r, r, None, "sim") for r in ("base", "cand", "aa")]
    for u in units:
        arms.append(ArmSpec(arm_name("cand_minus", u.id), "cand_minus", u.id, "sim"))
        arms.append(ArmSpec(arm_name("base_plus", u.id), "base_plus", u.id, "sim"))
    return Plan(scenarios=tuple(f"s{i:02d}" for i in range(n)), units=units, arms=tuple(arms), k=k,
                delta=delta, alpha=alpha, bootstrap_B=B, flips=flips, seed=seed, agent="simulated")


def _rates(plan: Plan, base: np.ndarray, effect: float, affected: np.ndarray) -> dict[str, np.ndarray]:
    bad = np.clip(base + effect * affected, 0, 1)
    out = {"base": base, "aa": base, "cand": bad}
    for i, uid in enumerate(plan.revertible):
        culprit = i == 0 and effect > 0
        out[arm_name("cand_minus", uid)] = base if culprit else bad
        out[arm_name("base_plus", uid)] = bad if culprit else base
    return out


def _draw(rng, plan: Plan, rates: dict[str, np.ndarray]) -> dict[str, ArmCounts]:
    k = plan.k
    return {
        a: ArmCounts(rng.binomial(k, p).astype(np.int64), np.full(plan.n, k, np.int64), np.zeros(plan.n, np.int64))
        for a, p in rates.items()
    }


@dataclass
class SimRow:
    label: str
    value: float
    note: str = ""


def run_setting(plan: Plan, rng, *, effect: float, m_affected: int, reps: int,
                beta: tuple[float, float] = HETEROGENEITY["high"]) -> dict:
    confirm = decoy = nec = named_any = repro = aa_fail = 0
    for _ in range(reps):
        base = rng.beta(beta[0], beta[1], plan.n)
        aff = np.zeros(plan.n)
        if effect > 0:
            aff[rng.permutation(plan.n)[:m_affected]] = 1.0
        counts = _draw(rng, plan, _rates(plan, base, effect, aff))
        v = verdict_from_counts(plan, counts, ["sim"])
        repro += v.repro_p is not None and v.repro_p < plan.alpha
        aa_fail += v.aa_p is not None and v.aa_p < plan.alpha
        named_any += bool(v.named)
        by = {u.unit: u for u in v.units}
        u0 = by.get(plan.revertible[0])
        confirm += u0 is not None and u0.status == CONFIRMED
        nec += u0 is not None and bool(u0.nec)
        decoy += any(by[u].status in NAMED for u in plan.revertible[1:] if u in by)
    r = float(reps)
    return {"repro": repro / r, "aa_fail": aa_fail / r, "named_any": named_any / r,
            "confirm": confirm / r, "decoy_named": decoy / r, "nec": nec / r}


def simulate(plan: Plan | None = None, *, reps: int = 300, grid_reps: int = 120, m_affected: int = 12,
             planted_effect: float = 0.40, seed: int = 7, heterogeneity: str = "high",
             progress=None) -> dict:
    plan = plan or design_plan()
    rng = np.random.default_rng(seed)
    beta = HETEROGENEITY[heterogeneity]
    out: dict = {"heterogeneity": heterogeneity, "plan": {"n": plan.n, "k": plan.k, "delta": plan.delta, "alpha": plan.alpha,
                          "B": plan.bootstrap_B, "flips": plan.flips, "units": len(plan.revertible),
                          "arms": len(plan.arms), "trials": plan.n_trials},
                 "reps": reps, "m_affected": m_affected}
    out["floor"] = st.affected_scenario_floor(plan.n, len(plan.revertible), plan.alpha, plan.flips)
    null = run_setting(plan, rng, effect=0.0, m_affected=0, reps=reps, beta=beta)
    out["type1_repro"] = null["repro"]
    out["false_named_null"] = null["named_any"]
    out["aa_false_drift"] = null["aa_fail"]
    if progress:
        progress("null done")
    alt = run_setting(plan, rng, effect=planted_effect, m_affected=m_affected, reps=reps, beta=beta)
    out["planted_effect"] = planted_effect
    out["power_confirm"] = alt["confirm"]
    out["power_repro"] = alt["repro"]
    out["decoy_named"] = alt["decoy_named"]
    out["ni_power"] = alt["nec"]
    if progress:
        progress("planted done")
    grid = []
    mde = mde_repro = None
    for e in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60]:
        g = run_setting(plan, rng, effect=e, m_affected=m_affected, reps=grid_reps, beta=beta)
        grid.append({"effect": e, "confirm": g["confirm"], "repro": g["repro"], "nec": g["nec"]})
        if progress:
            progress(f"grid {e:.2f}: confirm {g['confirm']:.2f}")
        if mde_repro is None and g["repro"] >= 0.8:
            mde_repro = e
        if mde is None and g["confirm"] >= 0.8:
            mde = e
        if mde is not None and mde_repro is not None:
            break
    out["grid"] = grid
    out["mde_confirm"] = mde
    out["mde_repro"] = mde_repro
    return out
