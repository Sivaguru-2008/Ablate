"""Ablate's statistical tests.

Everything here is a pure function of its inputs plus an explicit integer
seed. No I/O. The verdict engine composes these.

* `sign_flip_p`: paired sign-flip permutation test on per-scenario rate
  differences. Exact (full enumeration, done by convolution over exact
  integer-scaled differences) for n <= 20; above that, 20,000 seeded flips
  with p = (count + 1) / (flips + 1).
* `holm`: re-exported from the vendored evalsig code.
* `ni_bound`: non-inferiority upper bound = max(Newcombe-Wilson on pooled
  counts deflated by the design effect, cluster bootstrap). Never the
  bootstrap alone, which collapses to [0, 0] when both arms have zero
  violations.
* `affected_scenario_floor`: smallest number of scenarios that must move in
  the bad direction before a Holm-corrected sign-flip can reject at all.
* `fisher_exact`: two-sided Fisher exact test on a 2x2 table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from statistics import NormalDist

import numpy as np

from .intervals import holm  # noqa: F401  (re-export)

EXACT_MAX_N = 20
_ND = NormalDist()


# --------------------------------------------------------------------------
# helpers


def _as_fraction(x) -> Fraction:
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    # Rates are violations/valid with valid <= k (small), so differences have
    # small denominators. limit_denominator snaps float noise back to them.
    return Fraction(x).limit_denominator(10_000)


def _scaled_ints(diffs) -> tuple[list[int], int]:
    fr = [_as_fraction(d) for d in diffs]
    lcm = 1
    for f in fr:
        lcm = lcm * f.denominator // math.gcd(lcm, f.denominator)
    return [int(f * lcm) for f in fr], lcm


@lru_cache(maxsize=32)
def _sign_matrix(seed: int, flips: int, n: int) -> np.ndarray:
    rng = np.random.default_rng([seed, n, 0x51F7])
    return (rng.integers(0, 2, size=(flips, n), dtype=np.int64) * 2 - 1)


@lru_cache(maxsize=32)
def _boot_index(seed: int, B: int, n: int) -> np.ndarray:
    rng = np.random.default_rng([seed, n, 0xB007])
    return rng.integers(0, n, size=(B, n), dtype=np.int64)


# --------------------------------------------------------------------------
# sign-flip permutation test


def sign_flip_p(diffs, *, alternative: str = "greater", flips: int = 20_000, seed: int = 0) -> float:
    """Paired sign-flip permutation p-value for mean(diffs).

    alternative="greater": H1 mean > 0 (one-sided; "arm is worse").
    alternative="two-sided": H1 mean != 0.
    """
    if alternative not in ("greater", "two-sided"):
        raise ValueError(alternative)
    n = len(diffs)
    if n == 0:
        return 1.0
    a, _ = _scaled_ints(diffs)
    obs = sum(a)
    if n <= EXACT_MAX_N:
        # Exact: distribution of S = sum(s_i * a_i), s_i in {-1, +1}, by
        # convolution. 2^n outcomes, each equally likely.
        dist: dict[int, int] = {0: 1}
        for ai in a:
            nxt: dict[int, int] = {}
            for s, c in dist.items():
                nxt[s + ai] = nxt.get(s + ai, 0) + c
                nxt[s - ai] = nxt.get(s - ai, 0) + c
            dist = nxt
        if alternative == "greater":
            count = sum(c for s, c in dist.items() if s >= obs)
        else:
            count = sum(c for s, c in dist.items() if abs(s) >= abs(obs))
        return count / (1 << n)
    signs = _sign_matrix(seed, flips, n)
    stats = signs @ np.asarray(a, dtype=np.int64)
    if alternative == "greater":
        count = int(np.count_nonzero(stats >= obs))
    else:
        count = int(np.count_nonzero(np.abs(stats) >= abs(obs)))
    return (count + 1) / (flips + 1)


def affected_scenario_floor(n_scenarios: int, n_units: int, alpha: float, flips: int) -> int:
    """Smallest m such that m scenarios all moving the bad way can reject.

    With m non-zero positive differences the most extreme one-sided p-value
    is 2^-m (exact). Above EXACT_MAX_N the p-value is a Monte Carlo estimate,
    so we also demand a 3-SD Monte Carlo margin below the threshold.
    # DECISION: the margin makes the floor 7 (not 6) at n = 24, U = 3,
    # alpha = 0.05: at m = 6 the best-case p (~0.0157) sits ~1 MC SD below
    # alpha/3 = 0.0167, so CONFIRMED would hinge on RNG noise. This matches
    # the spec's stated floor of 7.
    """
    thr = alpha / max(1, n_units)
    for m in range(1, n_scenarios + 1):
        q = 2.0 ** -m
        if n_scenarios <= EXACT_MAX_N:
            if q < thr:
                return m
        else:
            p = (flips * q + 1) / (flips + 1)
            sd = math.sqrt(flips * q * (1 - q)) / (flips + 1)
            if p + 3 * sd < thr:
                return m
    return n_scenarios + 1  # impossible at this n


# --------------------------------------------------------------------------
# design effect and non-inferiority


def design_effect(viol: np.ndarray, valid: np.ndarray) -> float:
    """1 + (m_bar - 1) * ICC, ANOVA ICC estimator for clustered binary data."""
    viol = np.asarray(viol, dtype=float)
    valid = np.asarray(valid, dtype=float)
    keep = valid > 0
    viol, valid = viol[keep], valid[keep]
    g = len(valid)
    N = valid.sum()
    if g < 2 or N <= g:
        return 1.0
    p_i = viol / valid
    p_bar = viol.sum() / N
    msb = float((valid * (p_i - p_bar) ** 2).sum() / (g - 1))
    msw = float((valid * p_i * (1 - p_i)).sum() / (N - g))
    n0 = (N - (valid**2).sum() / N) / (g - 1)
    denom = msb + (n0 - 1) * msw
    icc = 0.0 if denom <= 0 else (msb - msw) / denom
    icc = min(1.0, max(0.0, icc))
    return max(1.0, 1.0 + (valid.mean() - 1.0) * icc)


def _wilson(p: float, n: float, z: float) -> tuple[float, float]:
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(max(0.0, p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def newcombe_bounds(s1: float, n1: float, s2: float, n2: float, a: float) -> tuple[float, float]:
    """One-sided level-a Newcombe hybrid-score bounds for p1 - p2.

    Accepts fractional (design-effect deflated) counts. Same formula as the
    vendored `newcombe_diff_interval` (Newcombe 1998, method 10) with
    z = z_{1-a}.
    """
    z = _ND.inv_cdf(1 - a)
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = _wilson(p1, n1, z)
    l2, u2 = _wilson(p2, n2, z)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, lo), min(1.0, hi)


@dataclass(frozen=True)
class PooledComparison:
    """Arm T vs reference arm R on paired scenarios (per-scenario counts)."""

    vt: np.ndarray  # violations in T per scenario
    nt: np.ndarray  # valid trials in T per scenario
    vr: np.ndarray
    nr: np.ndarray
    deff: float
    boot: np.ndarray  # sorted bootstrap distribution of pooled diff

    @property
    def diff(self) -> float:
        return float(self.vt.sum() / self.nt.sum() - self.vr.sum() / self.nr.sum())

    def newcombe(self, a: float) -> tuple[float, float]:
        d = self.deff
        return newcombe_bounds(self.vt.sum() / d, self.nt.sum() / d, self.vr.sum() / d, self.nr.sum() / d, a)

    def boot_bounds(self, a: float) -> tuple[float, float]:
        B = len(self.boot)
        lo = self.boot[max(0, math.floor(a * B) - 1)]
        hi = self.boot[min(B - 1, math.ceil((1 - a) * B) - 1)]
        return float(lo), float(hi)

    def upper(self, a: float) -> float:
        return max(self.newcombe(a)[1], self.boot_bounds(a)[1])

    def lower(self, a: float) -> float:
        return min(self.newcombe(a)[0], self.boot_bounds(a)[0])


def pooled_comparison(vt, nt, vr, nr, *, B: int, seed: int) -> PooledComparison:
    vt, nt, vr, nr = (np.asarray(x, dtype=np.int64) for x in (vt, nt, vr, nr))
    n = len(vt)
    if n == 0 or nt.sum() == 0 or nr.sum() == 0:
        raise ValueError("no paired valid trials")
    idx = _boot_index(seed, B, n)
    sv_t, sn_t = vt[idx].sum(1), nt[idx].sum(1)
    sv_r, sn_r = vr[idx].sum(1), nr[idx].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = np.where((sn_t > 0) & (sn_r > 0), sv_t / np.maximum(sn_t, 1) - sv_r / np.maximum(sn_r, 1), 0.0)
    boot.sort()
    deff = max(design_effect(vt, nt), design_effect(vr, nr))
    return PooledComparison(vt, nt, vr, nr, deff, boot)


def ni_p_value(cmp: PooledComparison, delta: float) -> float:
    """Smallest one-sided level a at which upper(a) < delta (1.0 if none).

    upper(a) is non-increasing in a, so bisection finds the crossing; this is
    what lets Holm be applied to non-inferiority decisions.
    """
    hi_a = 0.5
    if cmp.upper(hi_a) >= delta:
        return 1.0
    lo_a = 1e-12
    if cmp.upper(lo_a) < delta:
        return lo_a
    for _ in range(60):
        mid = (lo_a + hi_a) / 2
        if cmp.upper(mid) < delta:
            hi_a = mid
        else:
            lo_a = mid
    return hi_a


# --------------------------------------------------------------------------
# Fisher exact


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]] (sum of tables as or less likely)."""
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2
    if n == 0:
        return 1.0
    lo, hi = max(0, c1 - r2), min(r1, c1)

    def pmf(x: int) -> Fraction:
        return Fraction(math.comb(r1, x) * math.comb(r2, c1 - x), math.comb(n, c1))

    p_obs = pmf(a)
    total = sum((pmf(x) for x in range(lo, hi + 1) if pmf(x) <= p_obs * (1 + Fraction(1, 10**9))), Fraction(0))
    return float(min(Fraction(1), total))


# --------------------------------------------------------------------------
# sample-size pricing for INCONCLUSIVE


def more_scenarios_needed(diffs, *, effect: float, alpha: float, power: float = 0.8, k: int = 5,
                          rate_floor: float = 0.05) -> int | None:
    """Paired scenarios needed for a one-sided level-alpha test to have
    `power` at mean difference `effect`, using the observed SD of per-scenario
    differences (floored by a binomial SD so a lucky all-zero pilot cannot
    price the follow-up at zero). Returns None if effect <= 0.
    """
    if effect <= 0:
        return None
    arr = np.asarray([float(x) for x in diffs], dtype=float)
    sd_obs = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    p = rate_floor
    sd_floor = math.sqrt(2 * p * (1 - p) / k)
    sd = max(sd_obs, sd_floor)
    z = _ND.inv_cdf(1 - alpha) + _ND.inv_cdf(power)
    return int(math.ceil((z * sd / effect) ** 2))
