"""Step 2: the pure verdict engine on synthetic counts (no agent, no I/O)."""

import numpy as np
import pytest

from ablate.results import TrialResult
from ablate.stats.simulate import design_plan
from ablate.verdict import (APPROVE, BLOCK, CONFIRMED, DRIFT, INCONCLUSIVE, INFRA_SUSPECTED, INTERACTION_SUSPECTED,
                            MULTIPLE_CONTRIBUTORS, NEEDS_MORE_EVIDENCE, NOT_REPRODUCED, ArmCounts, verdict,
                            verdict_from_counts)

PLAN = design_plan()
N, K = PLAN.n, PLAN.k


def counts(rates: dict[str, np.ndarray], seed=0, invalid=None):
    """Deterministic counts: violations = round(rate * valid trials) per scenario."""
    del seed
    out = {}
    for arm in PLAN.arm_names:
        p = rates.get(arm, rates["base"])
        inv = np.zeros(N, np.int64) if not invalid or arm not in invalid else np.full(N, invalid[arm], np.int64)
        valid = K - inv
        out[arm] = ArmCounts(np.floor(p * valid + 0.5).astype(np.int64), valid.astype(np.int64), inv)
    return out


LOW = np.full(N, 0.02)
BAD = np.where(np.arange(N) < 14, 0.8, 0.02)  # 14 of 24 scenarios hurt
V = {"sim": ["sim-001"]}


def by_unit(v):
    return {u.unit: u for u in v.units}


def test_planted_culprit_confirmed_decoys_not_named():
    r = {"base": LOW, "aa": LOW, "cand": BAD, "cand-u0": LOW, "cand-u1": BAD, "cand-u2": BAD,
         "base+u0": BAD, "base+u1": LOW, "base+u2": LOW}
    v = verdict_from_counts(PLAN, counts(r), V)
    u = by_unit(v)
    assert v.decision == BLOCK
    assert u["u0"].status == CONFIRMED
    assert u["u1"].status == INCONCLUSIVE and u["u2"].status == INCONCLUSIVE
    assert v.named == ["u0"]


def test_multiple_contributors():
    half_a = np.where(np.arange(N) < 12, 0.8, 0.02)
    half_b = np.where(np.arange(N) >= 12, 0.8, 0.02)
    r = {"base": LOW, "aa": LOW, "cand": np.full(N, 0.8), "cand-u0": half_b, "cand-u1": half_a,
         "cand-u2": np.full(N, 0.8), "base+u0": half_a, "base+u1": half_b, "base+u2": LOW}
    v = verdict_from_counts(PLAN, counts(r, seed=1), V)
    u = by_unit(v)
    assert u["u0"].status == MULTIPLE_CONTRIBUTORS and u["u1"].status == MULTIPLE_CONTRIBUTORS
    assert v.decision == BLOCK


def test_interaction_suspected_is_not_named():
    r = {"base": LOW, "aa": LOW, "cand": BAD, "cand-u0": LOW, "cand-u1": LOW, "cand-u2": BAD,
         "base+u0": LOW, "base+u1": LOW, "base+u2": LOW}
    v = verdict_from_counts(PLAN, counts(r, seed=2), V)
    u = by_unit(v)
    assert u["u0"].status == INTERACTION_SUSPECTED and u["u1"].status == INTERACTION_SUSPECTED
    assert v.decision == NEEDS_MORE_EVIDENCE and v.named == []


def test_null_release_abstains_and_approves():
    v = verdict_from_counts(PLAN, counts({"base": LOW}, seed=4), V)
    assert v.status == NOT_REPRODUCED
    assert v.decision == APPROVE
    assert v.named == []


def test_aa_failure_is_drift():
    r = {"base": LOW, "aa": BAD, "cand": BAD, "base+u0": BAD, "cand-u0": LOW}
    v = verdict_from_counts(PLAN, counts(r, seed=5), V)
    assert v.status == DRIFT and v.decision == NEEDS_MORE_EVIDENCE and v.named == []


def test_model_version_change_is_drift():
    v = verdict_from_counts(PLAN, counts({"base": LOW}, seed=6), {"m": ["m-001", "m-002"]})
    assert v.status == DRIFT


def test_differential_invalid_rate_is_infra_suspected():
    v = verdict_from_counts(PLAN, counts({"base": LOW, "cand": BAD}, seed=7, invalid={"cand": 3}), V)
    assert v.status == INFRA_SUSPECTED and v.decision == NEEDS_MORE_EVIDENCE


def test_inconclusive_is_priced():
    plan = design_plan()
    plan = plan.__class__(**{**plan.__dict__, "cost_per_trial_usd": 0.01})
    r = {"base": LOW, "aa": LOW, "cand": BAD, "cand-u0": BAD, "cand-u1": BAD, "cand-u2": BAD,
         "base+u0": np.where(np.arange(N) < 3, 0.5, 0.02), "base+u1": LOW, "base+u2": LOW}
    v = verdict_from_counts(plan, counts(r, seed=8), V)
    u = by_unit(v)["u0"]
    assert u.status == INCONCLUSIVE
    assert u.more_scenarios is not None and u.more_usd is not None


def test_verdict_is_a_pure_function_and_checks_plan_hash():
    r = {"base": LOW, "aa": LOW, "cand": BAD, "cand-u0": LOW, "cand-u1": BAD, "cand-u2": BAD,
         "base+u0": BAD, "base+u1": LOW, "base+u2": LOW}
    c = counts(r, seed=9)
    results = []
    for arm, ac in c.items():
        for i, s in enumerate(PLAN.scenarios):
            for t in range(K):
                results.append(TrialResult(PLAN.plan_hash, s, arm, t, violated=t < ac.viol[i], model_requested="m",
                                           model_version="m-1"))
    a, b = verdict(PLAN, results), verdict(PLAN, list(reversed(results)))
    assert a.to_dict() == b.to_dict()
    bad = [TrialResult("0" * 64, PLAN.scenarios[0], "base", 0)]
    with pytest.raises(ValueError):
        verdict(PLAN, bad)
