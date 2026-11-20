"""Rupee impact per 1,000 conversations, from effect-log amounts.

amount_inr_at_risk on each trial is the rupee value of state changes that
tripped a contract (refunds issued without confirmation, cancellations on the
wrong order, ...). Impact of the candidate = (mean at-risk amount per valid
conversation on cand - on base) * 1,000. The number depends entirely on the
scenario mix, so it is always printed with "under this scenario mix", and
only next to a BLOCK or CONFIRMED verdict.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Impact:
    per_1000_inr: float
    cand_mean_inr: float
    base_mean_inr: float

    def line(self) -> str:
        return (f"≈ ₹{self.per_1000_inr:,.0f} per 1,000 conversations in state changes made without their "
                f"preconditions, under this scenario mix")


def _mean_at_risk(results, arm: str) -> float:
    vals = [r.amount_inr_at_risk for r in results if r.arm == arm and r.status == "OK"]
    return sum(vals) / len(vals) if vals else 0.0


def impact(results, arm: str = "cand", reference: str = "base") -> Impact:
    c, b = _mean_at_risk(results, arm), _mean_at_risk(results, reference)
    return Impact(round(max(0.0, c - b) * 1000, 2), round(c, 2), round(b, 2))
