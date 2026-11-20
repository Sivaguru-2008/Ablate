"""Self-contained HTML report (no external assets)."""

from __future__ import annotations

import html
from collections import defaultdict

from .. import wording
from ..impact import impact
from ..plan import Plan
from ..verdict import BLOCK, CONFIRMED, NAMED, Verdict

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--bad:#b42318;--ok:#1a7f37;--warn:#9a6700;--chip:#f1f0ec}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--line:#2d2d2a;--bad:#ff7b72;--ok:#56d364;--warn:#e3b341;--chip:#22221f}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:24px 16px}
main{max-width:1040px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 8px}
.decision{display:inline-block;padding:4px 10px;border-radius:6px;font-weight:600;background:var(--chip)}
.BLOCK{color:var(--bad)} .APPROVE{color:var(--ok)} .NEEDS{color:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:14px} th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:500} code{font-size:13px} .muted{color:var(--muted)} .wrap{overflow-x:auto}
.bar{height:8px;background:var(--bad);border-radius:4px;min-width:1px}
"""


def _pct(x):
    return "—" if x is None else f"{x:.1%}"


def render(plan: Plan, v: Verdict, results) -> str:
    rates: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    per_scn: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        a = rates[r.arm]
        if r.status == "INVALID":
            a[2] += 1
            continue
        a[0] += int(r.violated)
        a[1] += 1
        s = per_scn[(r.arm, r.scenario)]
        s[0] += int(r.violated)
        s[1] += 1
    cls = v.decision.split()[0]
    tag = " <span class='muted'>(scripted)</span>" if plan.agent == "scripted" else ""
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Ablate report</title><style>{CSS}</style></head><body><main>",
           f"<h1>Ablate release gate <span class='decision {cls}'>{html.escape(v.decision)}</span>{tag}</h1>",
           f"<p class='muted'>Status {html.escape(v.status)} · plan <code>{plan.plan_hash[:12]}</code> · "
           f"n = {plan.n} · k = {plan.k} · δ = {plan.delta:.0%} · α = {plan.alpha} · agent {html.escape(plan.agent)}</p>"]
    if plan.agent == "scripted":
        out.append("<p><b>Scripted demo (constructed fault):</b> results come from the deterministic scripted agent, not live Gemini.</p>")
    if v.reasons:
        out.append("<ul>" + "".join(f"<li>{html.escape(r)}</li>" for r in v.reasons) + "</ul>")
    if v.decision == BLOCK or any(u.status == CONFIRMED for u in v.units):
        out.append(f"<p><b>Impact:</b> {html.escape(impact(results).line())}.</p>")
    out.append(f"<p><b>Next step:</b> {html.escape(v.next_step)}</p>")
    out.append("<h2>Units</h2><div class='wrap'><table><tr><th>Unit</th><th>Verdict</th><th>nec (cand−X NI bound)</th>"
               "<th>suf (base+X Holm p)</th><th>Affected</th><th>Note</th></tr>")
    for u in v.units:
        b = "<b>" if u.status in NAMED else ""
        be = "</b>" if b else ""
        nec = "—" if u.nec is None else f"{'yes' if u.nec else 'no'} · {u.ni_upper:+.3f}"
        suf = "—" if u.suf is None else f"{'yes' if u.suf else 'no'} · {u.suf_p_holm:.4g}"
        out.append(f"<tr><td><code>{html.escape(u.unit)}</code></td><td>{b}{html.escape(u.status)}{be}</td><td>{nec}</td>"
                   f"<td>{suf}</td><td>{'—' if u.affected is None else u.affected}</td><td>{html.escape(u.note)}</td></tr>")
    out.append("</table></div><h2>Arms</h2><div class='wrap'><table><tr><th>Arm</th><th>Violation rate</th><th></th><th>Valid</th><th>INVALID</th></tr>")
    for a in plan.arm_names:
        viol, val, inv = rates[a]
        r = viol / val if val else 0.0
        out.append(f"<tr><td><code>{html.escape(a)}</code></td><td>{_pct(r)}</td><td style='width:40%'><div class='bar' style='width:{r * 100:.1f}%'></div></td>"
                   f"<td>{val}</td><td>{inv}</td></tr>")
    out.append("</table></div><h2>Per-scenario violations (base vs cand)</h2><div class='wrap'><table><tr><th>Scenario</th><th>base</th><th>cand</th></tr>")
    for s in plan.scenarios:
        bb, cc = per_scn[("base", s)], per_scn[("cand", s)]
        out.append(f"<tr><td><code>{html.escape(s)}</code></td><td>{bb[0]}/{bb[1]}</td><td>{cc[0]}/{cc[1]}</td></tr>")
    out.append("</table></div><p class='muted'>Oracle: Python contracts over world state, effect log and structured output. "
               "No LLM judge in the oracle or verdict path.</p></main></body></html>")
    return wording.check("".join(out), pr=True)
