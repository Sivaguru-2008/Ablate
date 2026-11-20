"""Step 1: statistics core."""

import itertools
import random
from fractions import Fraction

import numpy as np
import pytest

from ablate.stats import intervals as iv
from ablate.stats import tests as st


def brute_force(diffs, alternative):
    obs = sum(diffs)
    n = len(diffs)
    hit = 0
    for signs in itertools.product((-1, 1), repeat=n):
        s = sum(x * d for x, d in zip(signs, diffs))
        if alternative == "greater":
            hit += s >= obs
        else:
            hit += abs(s) >= abs(obs)
    return hit / 2**n


@pytest.mark.parametrize("alternative", ["greater", "two-sided"])
def test_sign_flip_exact_matches_enumeration(alternative):
    rng = random.Random(3)
    for _ in range(20):
        n = rng.randint(1, 11)
        diffs = [Fraction(rng.randint(-5, 5), 5) for _ in range(n)]
        assert st.sign_flip_p(diffs, alternative=alternative) == pytest.approx(brute_force(diffs, alternative))


def test_vendored_sign_flip_enumerates_exactly_for_small_n():
    diffs = [0.2, 0.4, -0.2, 0.6, 0.0, 0.2]
    assert iv._sign_flip_p(diffs) == pytest.approx(brute_force([Fraction(d).limit_denominator(10) for d in diffs], "two-sided"))


def test_sign_flip_monte_carlo_above_20_is_close_to_exact(monkeypatch):
    rng = random.Random(5)
    diffs = [Fraction(rng.choice([0, 0, 1, 2, -1]), 5) for _ in range(24)]
    mc = st.sign_flip_p(diffs, flips=20_000, seed=11)
    # (count + 1) / (flips + 1): never zero
    assert st.sign_flip_p([Fraction(1)] * 24, flips=20_000, seed=1) == pytest.approx(1 / 20_001)
    monkeypatch.setattr(st, "EXACT_MAX_N", 64)
    exact = st.sign_flip_p(diffs)
    assert abs(mc - exact) < 0.01


def test_sign_flip_is_deterministic_given_seed():
    d = [Fraction(i % 3, 5) for i in range(24)]
    assert st.sign_flip_p(d, seed=9) == st.sign_flip_p(d, seed=9)


def test_affected_scenario_floor_matches_spec():
    assert st.affected_scenario_floor(24, 3, 0.05, 20_000) == 7
    # exact regime: 2^-6 = 0.0156 < 0.05/3
    assert st.affected_scenario_floor(20, 3, 0.05, 20_000) == 6
    # with 6 positive scenarios at n = 24 the best p sits near alpha/3
    p6 = st.sign_flip_p([Fraction(1)] * 6 + [Fraction(0)] * 18)
    assert 0.0125 < p6 < 0.0195


def test_holm():
    adj = iv.holm({"a": 0.01, "b": 0.04, "c": 0.03}, 0.05)
    assert adj["a"] == (pytest.approx(0.03), True)
    assert adj["c"][0] == pytest.approx(0.06) and not adj["c"][1]
    assert adj["b"][0] == pytest.approx(0.06) and not adj["b"][1]


def test_wilson_and_newcombe_reference_values():
    w = iv.wilson_interval(0, 50)
    assert w.low == 0.0 and w.high == pytest.approx(0.0713, abs=1e-3)
    lo, hi = st.newcombe_bounds(5, 50, 5, 50, 0.025)
    ref = iv.newcombe_diff_interval(0.1, 50, 0.1, 50, 0.05)
    assert lo == pytest.approx(ref.low) and hi == pytest.approx(ref.high)


def test_ni_bound_does_not_collapse_when_both_arms_are_clean():
    zeros = np.zeros(24, dtype=int)
    k = np.full(24, 5)
    c = st.pooled_comparison(zeros, k, zeros, k, B=2000, seed=1)
    assert c.boot_bounds(0.05) == (0.0, 0.0)  # bootstrap alone collapses
    assert c.upper(0.05) > 0.0  # Newcombe keeps it honest
    assert c.upper(0.05) < 0.10


def test_ni_p_value_is_the_crossing_level():
    rng = np.random.default_rng(2)
    vt, vr = rng.binomial(5, 0.04, 24), rng.binomial(5, 0.04, 24)
    k = np.full(24, 5)
    c = st.pooled_comparison(vt, k, vr, k, B=4000, seed=3)
    p = st.ni_p_value(c, 0.10)
    assert 0 < p < 0.5
    assert c.upper(p * 1.05) < 0.10 <= c.upper(p * 0.95)


def test_design_effect():
    k = np.full(10, 5)
    assert st.design_effect(np.zeros(10, int), k) == 1.0
    clustered = np.array([5, 5, 0, 0, 0, 0, 0, 0, 0, 0])
    assert st.design_effect(clustered, k) > 3.0


def test_fisher_exact_reference():
    # scipy.stats.fisher_exact([[1, 9], [11, 3]]) -> 0.0027594...
    assert st.fisher_exact(1, 9, 11, 3) == pytest.approx(0.0027594, rel=1e-4)
    assert st.fisher_exact(0, 120, 0, 120) == 1.0


def test_more_scenarios_needed():
    assert st.more_scenarios_needed([0.0] * 24, effect=0.0, alpha=0.05) is None
    n_small = st.more_scenarios_needed([0.0] * 24, effect=0.10, alpha=0.05 / 3)
    n_big = st.more_scenarios_needed([0.0] * 24, effect=0.05, alpha=0.05 / 3)
    assert n_big > n_small > 0


def test_simulator_reports_operating_characteristics():
    from ablate.stats.simulate import simulate
    out = simulate(reps=40, grid_reps=20, heterogeneity="low", seed=1)
    for key in ("type1_repro", "false_named_null", "power_confirm", "ni_power", "floor", "mde_repro"):
        assert key in out
    assert out["floor"] == 7
    assert out["type1_repro"] <= 0.15
    assert out["power_repro"] >= 0.8
