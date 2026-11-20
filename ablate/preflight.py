"""Preflight: lint every arm config, build the frozen plan, price the run.

* Lint rules mark a config INCOMPATIBLE when the model API would reject it
  (e.g. `thinking_level` on a Gemini 2.x model). An INCOMPATIBLE base or
  candidate stops the release; an INCOMPATIBLE cand-X / base+X arm makes unit
  X INCOMPATIBLE.
* Arms that are not dependency-closed make their unit NOT_REVERTIBLE.
* Probe calls (optional, live): one minimal request per distinct
  (model, generation_config); a 400 marks the config INCOMPATIBLE.
* Trials / requests / tokens / cost, the affected-scenario floor warning,
  and a hard --budget cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import differ
from .plan import ALPHA, BOOTSTRAP_B, DELTA, FLIPS, K_TRIALS, N_SCENARIOS, SEED, ArmSpec, Plan, UnitSpec, arm_name
from .stats import tests as st
from .world.retail.tools import TOOL_SPECS

GEN_KEYS = {"temperature", "top_p", "top_k", "max_output_tokens", "thinking_budget", "thinking_level",
            "seed", "presence_penalty", "frequency_penalty"}
THINKING_LEVELS = {"minimal", "low", "medium", "high"}

# DECISION: list prices (USD per 1M tokens, input/output) as known when this
# was written. They are estimates for the budget cap, not billing; override
# with --price-in/--price-out. Unknown models are priced at the most
# expensive known tier so the cap errs on the safe side.
PRICES_PER_M = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3-pro-preview": (2.00, 12.00),
}
EST_CALLS_PER_TRIAL = 6
EST_IN_TOKENS_PER_CALL = 2_500
EST_OUT_TOKENS_PER_CALL = 120


@dataclass
class Issue:
    rule: str
    severity: str  # INCOMPATIBLE | WARN
    message: str
    arm: str = ""


@dataclass
class Preflight:
    plan: Plan
    configs: dict[str, dict]
    issues: list[Issue] = field(default_factory=list)
    est_requests: int = 0
    est_cost_usd: float = 0.0
    floor: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.plan.release_status == "OK"


# ---------------------------------------------------------------------------
# lint rules


def _family(model: str) -> str:
    m = (model or "").lower()
    for fam in ("gemini-3", "gemini-2.5", "gemini-2.0", "gemini-1.5"):
        if m.startswith(fam):
            return fam
    return "unknown"


def lint(cfg: dict, arm: str = "") -> list[Issue]:
    out: list[Issue] = []
    model = str(cfg.get("model") or "")
    gen = cfg.get("generation_config") or {}
    fam = _family(model)

    def bad(rule, msg):
        out.append(Issue(rule, "INCOMPATIBLE", msg, arm))

    def warn(rule, msg):
        out.append(Issue(rule, "WARN", msg, arm))

    if not model:
        bad("model-missing", "no model id")
    elif fam == "unknown":
        warn("model-unknown", f"unrecognised model id {model!r}; lint coverage is partial")
    for k in gen:
        if k not in GEN_KEYS:
            bad("gen-unknown-key", f"generation_config.{k} is not a GenerateContentConfig field")
    if "thinking_level" in gen:
        if fam != "gemini-3":
            bad("thinking-level-unsupported", f"thinking_level is a Gemini 3 setting; {model} rejects it")
        if str(gen["thinking_level"]).lower() not in THINKING_LEVELS:
            bad("thinking-level-value", f"thinking_level={gen['thinking_level']!r} not in {sorted(THINKING_LEVELS)}")
        if "thinking_budget" in gen:
            bad("thinking-both", "thinking_level and thinking_budget cannot both be set")
    if "thinking_budget" in gen:
        tb = gen["thinking_budget"]
        if not isinstance(tb, int):
            bad("thinking-budget-type", "thinking_budget must be an integer")
        elif tb != -1:
            m = model.lower()
            if "pro" in m and fam == "gemini-2.5":
                if tb == 0:
                    bad("thinking-budget-pro-off", f"{model} cannot disable thinking (thinking_budget=0)")
                elif not 128 <= tb <= 32_768:
                    bad("thinking-budget-range", f"{model} thinking_budget must be 128..32768")
            elif "flash-lite" in m and fam == "gemini-2.5":
                if tb != 0 and not 512 <= tb <= 24_576:
                    bad("thinking-budget-range", f"{model} thinking_budget must be 0 or 512..24576")
            elif fam == "gemini-2.5" and not 0 <= tb <= 24_576:
                bad("thinking-budget-range", f"{model} thinking_budget must be 0..24576")
    t = gen.get("temperature")
    if t is not None and not (isinstance(t, (int, float)) and 0 <= t <= 2):
        bad("temperature-range", f"temperature={t!r} outside [0, 2]")
    tp = gen.get("top_p")
    if tp is not None and not (isinstance(tp, (int, float)) and 0 <= tp <= 1):
        bad("top-p-range", f"top_p={tp!r} outside [0, 1]")
    tk = gen.get("top_k")
    if tk is not None and not (isinstance(tk, int) and tk >= 1):
        bad("top-k-range", f"top_k={tk!r} must be an integer >= 1")
    for name, tool in (cfg.get("tools") or {}).items():
        if name not in TOOL_SPECS:
            warn("tool-not-in-world", f"tool {name} has no world implementation; calls will fail closed")
        if not tool.get("description"):
            warn("tool-no-description", f"tool {name} has an empty description")
    for b in cfg.get("blocks") or []:
        if not b.get("text"):
            warn("block-empty", f"block {b['id']} is empty")
    return out


# ---------------------------------------------------------------------------
# cost


def price(model: str, price_in: float | None = None, price_out: float | None = None) -> tuple[float, float]:
    if price_in is not None and price_out is not None:
        return price_in, price_out
    if model in PRICES_PER_M:
        return PRICES_PER_M[model]
    return max(PRICES_PER_M.values())


def cost_per_trial(model: str, price_in=None, price_out=None) -> float:
    pin, pout = price(model, price_in, price_out)
    return EST_CALLS_PER_TRIAL * (EST_IN_TOKENS_PER_CALL * pin + EST_OUT_TOKENS_PER_CALL * pout) / 1e6


# ---------------------------------------------------------------------------
# plan construction


def build(base: dict, cand: dict, scenarios: list[dict], *, agent: str = "scripted", k: int = K_TRIALS,
          seed: int = SEED, delta: float = DELTA, alpha: float = ALPHA, B: int = BOOTSTRAP_B, flips: int = FLIPS,
          budget_usd: float | None = None, price_in: float | None = None, price_out: float | None = None,
          probe=None) -> Preflight:
    base, cand = differ.normalize(base), differ.normalize(cand)
    all_ids = set(differ.unit_table(base)) | set(differ.unit_table(cand))
    configs: dict[str, dict] = {"base": differ.compose(base, cand, set()), "cand": differ.compose(base, cand, all_ids)}
    issues: list[Issue] = []
    for arm in ("base", "cand"):
        issues += lint(configs[arm], arm)
        if probe:
            issues += probe(configs[arm], arm)
    release_status = "INCOMPATIBLE" if any(i.severity == "INCOMPATIBLE" for i in issues) else "OK"

    units: list[UnitSpec] = []
    unit_cfgs: dict[str, tuple[dict, dict]] = {}
    for u in differ.changed_units(base, cand):
        cm_from = all_ids - {u.id}
        cm = differ.compose(base, cand, cm_from)
        bp = differ.compose(base, cand, {u.id})
        problems = differ.closure_violations(base, cand, cm_from) + differ.closure_violations(base, cand, {u.id})
        if problems:
            units.append(UnitSpec(u.id, "NOT_REVERTIBLE", "; ".join(sorted(set(problems)))))
            continue
        arm_issues = []
        for name, cfg in ((arm_name("cand_minus", u.id), cm), (arm_name("base_plus", u.id), bp)):
            arm_issues += lint(cfg, name)
            if probe:
                arm_issues += probe(cfg, name)
        issues += arm_issues
        hard = [i for i in arm_issues if i.severity == "INCOMPATIBLE"]
        if hard:
            units.append(UnitSpec(u.id, "INCOMPATIBLE", "; ".join(f"{i.arm}: {i.message}" for i in hard)))
            continue
        units.append(UnitSpec(u.id, "REVERTIBLE"))
        unit_cfgs[u.id] = (cm, bp)

    configs["aa"] = configs["base"]
    arms = [ArmSpec("base", "base", None, differ.config_hash(configs["base"])),
            ArmSpec("cand", "cand", None, differ.config_hash(configs["cand"])),
            ArmSpec("aa", "aa", None, differ.config_hash(configs["base"]))]
    for uid, (cm, bp) in unit_cfgs.items():
        for role, cfg in (("cand_minus", cm), ("base_plus", bp)):
            name = arm_name(role, uid)
            configs[name] = cfg
            arms.append(ArmSpec(name, role, uid, differ.config_hash(cfg)))

    model = configs["cand"].get("model") or ""
    # Priced as if live even for the scripted agent, so INCONCLUSIVE next steps
    # quote what the follow-up would cost on the real model.
    cpt = cost_per_trial(model, price_in, price_out)
    notes = []
    if len(scenarios) != N_SCENARIOS:
        notes.append(f"n = {len(scenarios)} differs from the design constant {N_SCENARIOS}; simulated error rates do not apply")
    plan = Plan(scenarios=tuple(s["id"] for s in scenarios), units=tuple(units), arms=tuple(arms), k=k,
                delta=delta, alpha=alpha, bootstrap_B=B, flips=flips, seed=seed, agent=agent, model=model,
                cost_per_trial_usd=round(cpt, 6), release_status=release_status, notes=tuple(notes))
    pf = Preflight(plan=plan, configs=configs, issues=issues)
    n_rev = max(1, len(plan.revertible))
    pf.floor = st.affected_scenario_floor(plan.n, n_rev, alpha, flips)
    pf.est_requests = plan.n_trials * EST_CALLS_PER_TRIAL if agent != "scripted" else 0
    pf.est_cost_usd = round(plan.n_trials * cpt, 2) if agent != "scripted" else 0.0
    pf.warnings.append(
        f"Affected-scenario floor: CONFIRMED needs at least {pf.floor} of {plan.n} scenarios to get worse when a "
        f"unit is added to base (Holm alpha = {alpha}/{n_rev}). A change that can move fewer scenarios cannot be CONFIRMED.")
    if budget_usd is not None and pf.est_cost_usd > budget_usd:
        pf.warnings.append(f"Estimated cost ${pf.est_cost_usd:,.2f} exceeds --budget ${budget_usd:,.2f}")
    return pf


def over_budget(pf: Preflight, budget_usd: float | None) -> bool:
    return budget_usd is not None and pf.est_cost_usd > budget_usd


def estimate_line(pf: Preflight) -> str:
    p = pf.plan
    live = pf.est_requests > 0
    cost = f"≈ ${pf.est_cost_usd:,.2f}" if live else f"$0 (scripted; live ≈ ${p.n_trials * p.cost_per_trial_usd:,.2f})"
    return (f"{len(p.arms)} arms × {p.n} scenarios × {p.k} trials = {p.n_trials:,} trials; "
            f"~{pf.est_requests:,} model requests; cost {cost}")

