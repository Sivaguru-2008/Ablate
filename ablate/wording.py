"""Wording guard for everything Ablate prints (rule 6).

Never print "proven", "proof", "first", "only", "certified" or "root cause";
never print "LIKELY" in PR output.
"""

from __future__ import annotations

import re

BANNED = re.compile(r"\b(proven|proof|first|only|certified|root\s+cause)\b", re.IGNORECASE)
BANNED_PR = re.compile(r"\bLIKELY\b")


def violations(text: str, *, pr: bool = False) -> list[str]:
    hits = [m.group(0) for m in BANNED.finditer(text)]
    if pr:
        hits += [m.group(0) for m in BANNED_PR.finditer(text)]
    return hits


def check(text: str, *, pr: bool = False) -> str:
    bad = violations(text, pr=pr)
    if bad:
        raise ValueError(f"banned wording in output: {sorted(set(bad))}")
    return text
