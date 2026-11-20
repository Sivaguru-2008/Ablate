"""Markdown PR comment: APPROVE / BLOCK / NEEDS MORE EVIDENCE."""

from __future__ import annotations

from .. import wording
from ..impact import impact
from ..plan import Plan
from ..verdict import BLOCK, CONFIRMED, NAMED, Verdict

ICON = {"APPROVE": "✅", "BLOCK": "⛔", "NEEDS MORE EVIDENCE": "🟡"}
STATUS_TEXT = {
    "REPRODUCED": "Regression reproduced",
    "NOT_REPRODUCED": "No regression reproduced",
    "DRIFT": "A/A control failed (DRIFT)",
    "INFRA_SUSPECTED": "Infrastructure errors differ between arms (INFRA_SUSPECTED)",
    "INCOMPATIBLE": "Config rejected by the model API (INCOMPATIBLE)",
}


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.1f} pp"


def _p(x: float | None) -> str:
    """'= 0.0123' or '< 0.0001' (use as f"p {_p(x)}")."""
    if x is None:
        return "= —"
    return f"= {x:.4f}" if x >= 1e-4 else "< 0.0001"


def unit_rows(plan: Plan, v: Verdict) -> list[str]:
    rows = ["| Change | Verdict | Revert it: cand−X vs base (NI bound < δ?) | Add it alone: base+X vs base (Holm p) | Scenarios worse | Next step |",
            "|---|---|---|---|---|---|"]
    order = {s: i for i, s in enumerate((CONFIRMED, "MULTIPLE_CONTRIBUTORS", "INTERACTION_SUSPECTED", "INCONCLUSIVE",
                                         "NOT_TESTED", "NOT_REVERTIBLE", "INCOMPATIBLE"))}
    for u in sorted(v.units, key=lambda u: (order.get(u.status, 99), u.unit)):
        status = f"**{u.status}**" if u.status in NAMED else u.status
        if u.nec is None:
            nec = suf = aff = "—"
        else:
            nec = (f"{'yes' if u.nec else 'no'}: {_pp(u.ni_diff)}, bound {_pp(u.ni_upper)}"
                   f" (one-sided α = {u.ni_level:.4f})")
            suf = f"{'yes' if u.suf else 'no'}: {_pp(u.suf_diff)}, p {_p(u.suf_p_holm)}"
            aff = f"{u.affected}/{plan.n}"
        rows.append(f"| `{u.unit}` | {status} | {nec} | {suf} | {aff} | {u.note or ''} |")
    return rows


def render(plan: Plan, v: Verdict, results=None) -> str:
    tag = " (scripted)" if plan.agent == "scripted" else ""
    lines = [f"## {ICON.get(v.decision, '')} Ablate release gate: {v.decision}{tag}", ""]
    head = STATUS_TEXT.get(v.status, v.status)
    if v.status in ("REPRODUCED", "NOT_REPRODUCED") and v.base_rate is not None:
        head += (f": candidate violates a contract in {v.cand_rate:.1%} of trials vs {v.base_rate:.1%} on base "
                 f"(paired sign-flip, one-sided p {_p(v.repro_p)}).")
    else:
        head += "."
    lines.append(head)
    if plan.agent == "scripted":
        lines.append("- **Scripted demo (constructed fault):** results come from the deterministic scripted agent, not live Gemini.")
    for r in v.reasons:
        lines.append(f"- {r}")
    if v.equivalence:
        lo, hi = v.equivalence
        inside = -plan.delta < lo and hi < plan.delta
        lines.append(f"- Equivalence interval for cand − base: [{_pp(lo)}, {_pp(hi)}] "
                     f"({'inside' if inside else 'not inside'} ±{plan.delta * 100:.0f} pp).")
    if v.base_rate is not None and v.base_rate > 0.5:
        lines.append(f"- ⚠ Base itself violates a contract in {v.base_rate:.0%} of trials: the base agent fails most "
                     "scenarios, so this gate says little about the release. Check the model and scenarios before relying on it.")
    if v.aa_identical and v.status != "DRIFT":
        lines.append("- Note: the A/A replicate matched base exactly (deterministic decoding), so the A/A control "
                     "carries no information about run-to-run drift here.")
    lines.append("")
    if v.named:
        names = ", ".join(f"`{u}`" for u in v.named)
        lines.append(f"**Named change(s):** {names}")
        lines.append("")
    if v.units:
        lines += unit_rows(plan, v)
        lines.append("")
    show_impact = results is not None and (v.decision == BLOCK or any(u.status == CONFIRMED for u in v.units))
    if show_impact:
        lines.append(f"**Impact:** {impact(results).line()}.")
        lines.append("")
    lines.append(f"**Next step:** {v.next_step}")
    lines.append("")
    ctrl = []
    if v.aa_p is not None:
        ctrl.append(f"A/A two-sided p {_p(v.aa_p)} ({'clean' if v.aa_p >= plan.alpha else 'failed'})")
    if v.model_versions:
        mv = "; ".join(f"{k} → {', '.join(vs)}" for k, vs in v.model_versions.items())
        ctrl.append(f"model versions: {mv}")
    if v.infra_p:
        ctrl.append(f"INVALID-rate Fisher min p {_p(min(v.infra_p.values()))}")
    if v.affected_floor:
        ctrl.append(f"affected-scenario floor = {v.affected_floor}")
    lines.append("<sub>Controls: " + " · ".join(ctrl) + "</sub>")
    lines.append("")
    lines.append(f"<sub>Plan `{plan.plan_hash[:12]}` · n = {plan.n} scenarios · k = {plan.k} · δ = {plan.delta * 100:.0f} pp · "
                 f"α = {plan.alpha} familywise (Holm) · {len(plan.arms)} arms · {plan.n_trials:,} trials · agent `{plan.agent}` · "
                 f"oracle: code contracts, no LLM judge</sub>")
    return wording.check("\n".join(lines) + "\n", pr=True)
