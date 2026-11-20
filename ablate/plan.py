"""The experiment plan: frozen and hashed before the first trial.

Everything that can change a verdict (n, k, delta, alpha, seeds, B, flips,
scenarios, units, arms and the arm config hashes) is in the hash. Results
carry the plan hash, and the verdict engine refuses results from another plan.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Design constants (spec v2.2). All of them go into the plan hash.
N_SCENARIOS = 24
K_TRIALS = 5
DELTA = 0.10
ALPHA = 0.05
BOOTSTRAP_B = 10_000
FLIPS = 20_000
SEED = 20_261_120

ARM_ROLES = ("base", "cand", "aa", "cand_minus", "base_plus")


@dataclass(frozen=True)
class UnitSpec:
    id: str
    status: str = "REVERTIBLE"  # REVERTIBLE | NOT_REVERTIBLE | INCOMPATIBLE
    reason: str = ""


@dataclass(frozen=True)
class ArmSpec:
    name: str
    role: str  # one of ARM_ROLES
    unit: str | None
    config_hash: str


@dataclass(frozen=True)
class Plan:
    scenarios: tuple[str, ...]
    units: tuple[UnitSpec, ...]
    arms: tuple[ArmSpec, ...]
    k: int = K_TRIALS
    delta: float = DELTA
    alpha: float = ALPHA
    bootstrap_B: int = BOOTSTRAP_B
    flips: int = FLIPS
    seed: int = SEED
    agent: str = "scripted"
    model: str = ""
    cost_per_trial_usd: float = 0.0
    release_status: str = "OK"  # OK | INCOMPATIBLE (base/cand rejected at preflight)
    notes: tuple[str, ...] = field(default_factory=tuple)
    # SHA-256 of the scenario definitions (turn texts, expectations), so an
    # edited scenario can never reuse trials cached under the old wording.
    scenarios_hash: str = ""

    # -- derived ----------------------------------------------------------
    @property
    def n(self) -> int:
        return len(self.scenarios)

    @property
    def revertible(self) -> tuple[str, ...]:
        return tuple(u.id for u in self.units if u.status == "REVERTIBLE")

    @property
    def arm_names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.arms)

    def arm(self, role: str, unit: str | None = None) -> ArmSpec:
        for a in self.arms:
            if a.role == role and a.unit == unit:
                return a
        raise KeyError((role, unit))

    @property
    def n_trials(self) -> int:
        return len(self.arms) * self.n * self.k

    # -- freezing -----------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["scenarios"] = list(self.scenarios)
        d["units"] = [asdict(u) for u in self.units]
        d["arms"] = [asdict(a) for a in self.arms]
        d["notes"] = list(self.notes)
        if not self.scenarios_hash:
            d.pop("scenarios_hash")  # plans frozen before this field keep their hash
        return d

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        d = dict(d)
        d.pop("plan_hash", None)
        d["scenarios"] = tuple(d["scenarios"])
        d["units"] = tuple(UnitSpec(**u) for u in d["units"])
        d["arms"] = tuple(ArmSpec(**a) for a in d["arms"])
        d["notes"] = tuple(d.get("notes", ()))
        return cls(**d)


def arm_name(role: str, unit: str | None) -> str:
    if role in ("base", "cand", "aa"):
        return role
    if role == "cand_minus":
        return f"cand-{unit}"
    if role == "base_plus":
        return f"base+{unit}"
    raise ValueError(role)


def freeze(plan: Plan, run_dir: Path) -> str:
    """Write plan.json (with its hash) and refuse to overwrite a different plan."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "plan.json"
    h = plan.plan_hash
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("plan_hash") != h:
            raise RuntimeError(
                f"{path} holds a different frozen plan ({old.get('plan_hash', '?')[:12]}); "
                "use a new run directory instead of changing a frozen plan"
            )
        return h
    payload = plan.to_dict()
    payload["plan_hash"] = h
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return h


def load(run_dir: Path) -> Plan:
    d = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    plan = Plan.from_dict(d)
    if d.get("plan_hash") and d["plan_hash"] != plan.plan_hash:
        raise RuntimeError("plan.json hash mismatch: the frozen plan was edited")
    return plan


def trial_seed(plan_seed: int, scenario: str, arm: str, trial: int) -> int:
    """Per-trial seed. Arm-specific, so the A/A replicate is an independent draw."""
    h = hashlib.sha256(f"{plan_seed}|{scenario}|{arm}|{trial}".encode()).digest()
    return int.from_bytes(h[:8], "big")
