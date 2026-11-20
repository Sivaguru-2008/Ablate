# Vendored from HZDF-2026/evalsig
#   source repo: https://github.com/HZDF-2026/evalsig
#   commit:      8bb9b78c6163ec03626239e451574107554fe230
#   licence:     MIT, Copyright (c) 2026 Shijing Ge (see THIRD_PARTY.md)
#   files:       evalsig/intervals.py (wilson_interval, newcombe_diff_interval,
#                cluster_bootstrap_ci, z_two_sided, binom_cdf),
#                evalsig/decide.py (_sign_flip_p, holm),
#                evalsig/power.py (n_paired)
# Local changes: functions not listed above were dropped; the pieces from
# decide.py / power.py were inlined here so the stats core is one file.
# `_sign_flip_p` is kept verbatim for reference and cross-checks only. Ablate's
# own one-sided test lives in ablate/stats/tests.py (exact for n <= 20,
# 20,000 seeded flips above that, as the spec requires; upstream uses a normal
# approximation above n = 20).
"""Confidence intervals and exact tests for benchmark proportions.

All estimators here are chosen for validity at small n and near boundary
success rates — the regime where agent benchmarks actually live. Wald
intervals (the default in most eval scripts) are wrong exactly there.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction
from statistics import NormalDist, StatisticsError, mean, stdev

__all__ = [
    "Interval",
    "z_two_sided",
    "wilson_interval",
    "newcombe_diff_interval",
    "binom_cdf",
    "cluster_bootstrap_ci",
    "holm",
    "n_paired",
    "PowerPlan",
]


@dataclass(frozen=True)
class Interval:
    low: float
    high: float
    alpha: float
    method: str

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def half(self) -> float:
        return self.width / 2

    @property
    def center(self) -> float:
        return (self.low + self.high) / 2

    def excludes(self, value: float) -> bool:
        return value < self.low or value > self.high

    def __str__(self) -> str:
        return f"[{self.low:+.4f}, {self.high:+.4f}] ({self.method}, {1 - self.alpha:.0%})"


def z_two_sided(alpha: float) -> float:
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    return NormalDist().inv_cdf(1 - alpha / 2)


def wilson_interval(successes: int, n: int, alpha: float = 0.05) -> Interval:
    """Wilson score interval for a binomial proportion.

    Valid at n as small as 1 and for p near 0 or 1, where the Wald interval
    collapses (e.g. 0/50 -> Wald [-0.0, 0.0], Wilson [0.0, 0.072]).
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in [0, n], got {successes}/{n}")
    z = z_two_sided(alpha)
    z2 = z * z
    p = successes / n
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return Interval(max(0.0, center - half), min(1.0, center + half), alpha, "wilson")


def newcombe_diff_interval(p1: float, n1: int, p2: float, n2: int, alpha: float = 0.05) -> Interval:
    """Newcombe hybrid-score CI for p1 - p2 (unpaired).

    Composes the two Wilson intervals; keeps near-boundary validity where the
    Wald CI for a difference overstates certainty. Newcombe, Stat. Med. 17
    (1998), method 10.
    """
    for p, n in ((p1, n1), (p2, n2)):
        if not 0 <= p <= 1:
            raise ValueError(f"proportion out of [0,1]: {p}")
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
    s1, s2 = round(p1 * n1), round(p2 * n2)
    l1, u1 = wilson_interval(s1, n1, alpha).low, wilson_interval(s1, n1, alpha).high
    l2, u2 = wilson_interval(s2, n2, alpha).low, wilson_interval(s2, n2, alpha).high
    d = p1 - p2
    lower = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    upper = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return Interval(max(-1.0, lower), min(1.0, upper), alpha, "newcombe")


def binom_cdf(k: int, n: int, p: float) -> float:
    """Exact binomial CDF via exact rational arithmetic.

    p is converted to its exact binary rational, the pmf recurrence is
    carried out in exact fractions, and the sum is rounded to a double once.
    """
    if not 0 <= k or k > n:
        raise ValueError(f"k must be in [0, n], got {k}/{n}")
    if p <= 0:
        return 1.0
    if p >= 1:
        return 0.0 if k < n else 1.0
    pf = Fraction(p)
    if pf == Fraction(1, 2):
        s = sum(math.comb(n, i) for i in range(k + 1))
        r = Fraction(s, 1 << n)
    else:
        qf = 1 - pf
        pmf = qf**n
        total = pmf
        for i in range(1, k + 1):
            pmf = pmf * (n - i + 1) * pf / (i * qf)
            total += pmf
        r = total
    if r > 1 - Fraction(1, 10**12):
        return 1.0
    return float(min(r, Fraction(1)))


def _exact_scaled_ints(values: list) -> tuple[int, list[int]] | None:
    """Exact dyadic representation of finite floats under a common scale."""
    ratios = []
    e_max = 0
    for v in values:
        if type(v) is not float or not math.isfinite(v):
            return None
        m, d = v.as_integer_ratio()
        e = d.bit_length() - 1
        if e > e_max:
            e_max = e
        ratios.append((m, e))
    scale = 1 << e_max
    ints = [m << (e_max - e) for m, e in ratios]
    return scale, ints


def _percentile_interval(stats: list, B: int, alpha: float, method: str) -> Interval:
    stats.sort()
    lo = stats[max(0, math.floor(alpha / 2 * B) - 1)]
    hi = stats[min(B - 1, math.ceil((1 - alpha / 2) * B) - 1)]
    return Interval(lo, hi, alpha, method)


def _exact_cluster_sums(clusters: list) -> tuple[int, list[int], list[int]] | None:
    """Exact per-cluster sums as integers on a common power-of-two scale."""
    flat = [v for cl in clusters for v in cl]
    rep = _exact_scaled_ints(flat)
    if rep is None:
        return None
    scale, ints = rep
    sums = []
    counts = []
    pos = 0
    for cl in clusters:
        n = len(cl)
        sums.append(sum(ints[pos:pos + n]))
        counts.append(n)
        pos += n
    return scale, sums, counts


def cluster_bootstrap_ci(
    clusters: list[list[float]],
    stat=None,
    B: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Interval:
    """Percentile bootstrap CI that resamples clusters (tasks), not runs.

    Deterministic given `seed`. Ablate's hot path uses the vectorised
    equivalent in ablate/stats/tests.py; this pure-Python version is kept as
    the reference implementation and is cross-checked in tests.
    """
    if B < 2:
        raise ValueError(f"B must be >= 2, got {B}")
    if not clusters:
        raise ValueError("no clusters given")
    rng = random.Random(seed)
    k = len(clusters)
    if stat is None:
        rep = _exact_cluster_sums(clusters)
        if rep is not None:
            scale, sums, counts = rep
            randrange = rng.randrange
            stats = []
            append = stats.append
            for _ in range(B):
                total = 0
                cnt = 0
                for _ in range(k):
                    i = randrange(k)
                    total += sums[i]
                    cnt += counts[i]
                if cnt == 0:
                    raise StatisticsError("mean requires at least one data point")
                append(total / (scale * cnt))
            return _percentile_interval(stats, B, alpha, "cluster-bootstrap")
        stat = mean
    stats = []
    for _ in range(B):
        sample = [clusters[rng.randrange(k)] for _ in range(k)]
        flat = [v for cl in sample for v in cl]
        stats.append(stat(flat))
    return _percentile_interval(stats, B, alpha, "cluster-bootstrap")


# --- from evalsig/decide.py -------------------------------------------------

def _sign_flip_p(diffs: list) -> float:
    """Two-sided sign-flip permutation p-value for the mean of paired diffs.

    Upstream behaviour, kept verbatim: exact enumeration for n <= 20 (checked:
    it enumerates all 2^n masks), normal approximation above. Ablate does not
    call this in the verdict path; see ablate/stats/tests.py.
    """
    if not diffs:
        return 1.0
    obs = abs(mean(diffs))
    n = len(diffs)
    if n > 20:
        se = stdev(diffs) / math.sqrt(n)
        if se == 0:
            return 1.0
        return min(1.0, 2 * (1 - NormalDist().cdf(obs / se)))
    count = 0
    for mask in range(1 << n):
        s = 0.0
        for i, d in enumerate(diffs):
            s += d if (mask >> i) & 1 else -d
        if abs(s / n) >= obs - 1e-12:
            count += 1
    return count / (1 << n)


def holm(pvalues: dict, alpha: float = 0.05) -> dict:
    """Holm-Bonferroni step-down: adjusted p-values and reject decisions.

    Returns {name: (adjusted_p, reject)}.
    """
    items = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    adjusted = {}
    running = 0.0
    m = len(items)
    for i, (name, p) in enumerate(items):
        running = max(running, p * (m - i))
        adj = min(1.0, running)
        adjusted[name] = (adj, adj < alpha)
    return adjusted


# --- from evalsig/power.py --------------------------------------------------

@dataclass(frozen=True)
class PowerPlan:
    n_per_version: int
    alpha: float
    power: float
    method: str
    note: str = ""


def n_paired(
    expected_discordance: float,
    delta: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> PowerPlan:
    """Minimum total N (paired, fixed task set) for McNemar. Cross-check only.

    N = (z_{a/2} + z_b)^2 * psi / delta^2 with psi = discordance.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0,1), got {alpha}")
    if not 0 < power < 1:
        raise ValueError(f"power must be in (0,1), got {power}")
    if not 0 < expected_discordance <= 1:
        raise ValueError(f"discordance must be in (0, 1], got {expected_discordance}")
    if delta <= 0:
        raise ValueError("delta must be > 0")
    z_a = z_two_sided(alpha)
    z_b = NormalDist().inv_cdf(power)
    n = math.ceil((z_a + z_b) ** 2 * expected_discordance / (delta * delta))
    return PowerPlan(
        max(1, n), alpha, power, "mcnemar",
        note="paired on the same task set; multiply-unpaired designs need far more",
    )
