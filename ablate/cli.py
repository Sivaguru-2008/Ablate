"""Command line: ablate simulate | preflight | check | report | pytest-gen | fidelity | draft | envelopes | console."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import PROJECT_DIR, differ, preflight, wording

console = Console(highlight=False)
EXIT = {"APPROVE": 0, "BLOCK": 1, "NEEDS MORE EVIDENCE": 3}


def say(text: str = "", **kw) -> None:
    console.print(wording.check(str(text)), **kw)


# ---------------------------------------------------------------------------
# simulate


def cmd_simulate(a) -> int:
    from .stats.simulate import design_plan, simulate

    plan = design_plan()
    levels = ["low", "high"] if a.heterogeneity == "both" else [a.heterogeneity]
    say(f"[bold]Design[/bold]: n = {plan.n} scenarios, k = {plan.k}, δ = {plan.delta:.0%}, α = {plan.alpha} familywise, "
        f"B = {plan.bootstrap_B:,}, flips = {plan.flips:,}, {len(plan.revertible)} units → {len(plan.arms)} arms, "
        f"{plan.n_trials:,} trials")
    say(f"Planted culprit: +{a.effect:.0%} violation rate on {a.affected} of {plan.n} scenarios; "
        f"{a.reps} reps per setting (grid {a.grid_reps}).")
    outs = {}
    with console.status("simulating…") as status:
        for h in levels:
            outs[h] = simulate(plan, reps=a.reps, grid_reps=a.grid_reps, m_affected=a.affected, planted_effect=a.effect,
                               heterogeneity=h, seed=a.seed, progress=lambda m, h=h: status.update(f"{h}: {m}"))
    t = Table(title="Operating characteristics (this design)")
    t.add_column("Metric")
    for h in levels:
        t.add_column(f"{h} heterogeneity", justify="right")

    def row(label, fn):
        t.add_row(label, *[fn(outs[h]) for h in levels])

    row("Type I error, reproduction gate (cand = base)", lambda o: f"{o['type1_repro']:.3f}")
    row("False naming under the null (any unit named)", lambda o: f"{o['false_named_null']:.3f}")
    row("A/A false DRIFT rate", lambda o: f"{o['aa_false_drift']:.3f}")
    row("Power to reproduce the planted regression", lambda o: f"{o['power_repro']:.3f}")
    row("Power to CONFIRM the planted culprit", lambda o: f"{o['power_confirm']:.3f}")
    row("Decoy named when a culprit is planted", lambda o: f"{o['decoy_named']:.3f}")
    row("NI power (reverting the culprit restores base)", lambda o: f"{o['ni_power']:.3f}")
    row("MDE, reproduction (≥ 80% power)", lambda o: f"{o['mde_repro']:.0%}" if o["mde_repro"] else "> 60%")
    row("MDE, CONFIRMED (≥ 80% power)", lambda o: f"{o['mde_confirm']:.0%}" if o["mde_confirm"] else "not reached (NI-limited)")
    row("Affected-scenario floor", lambda o: str(o["floor"]))
    console.print(t)
    o = outs[levels[0]]
    say(f"[dim]Affected-scenario floor: with fewer than {o['floor']} scenarios moving, CONFIRMED is impossible at "
        f"Holm α = {plan.alpha}/{len(plan.revertible)}.[/dim]")
    if a.json:
        Path(a.json).write_text(json.dumps(outs, indent=2), encoding="utf-8")
    return 0


# ---------------------------------------------------------------------------
# preflight / check


def _units_table(pf) -> Table:
    t = Table(title="Changed units")
    t.add_column("Unit")
    t.add_column("Status")
    t.add_column("Reason")
    for u in pf.plan.units:
        t.add_row(u.id, u.status, u.reason)
    return t


def _print_preflight(pf, show_mde: bool) -> None:
    console.print(_units_table(pf))
    for i in pf.issues:
        style = "red" if i.severity == "INCOMPATIBLE" else "yellow"
        say(f"[{style}]{i.severity}[/{style}] {i.arm or '-'}: {i.rule}: {i.message}")
    if pf.plan.release_status != "OK":
        say("[red bold]Release INCOMPATIBLE: base or candidate config is rejected by the model API.[/red bold]")
    say(preflight.estimate_line(pf))
    for w in pf.warnings:
        say(f"[yellow]warning[/yellow] {w}")
    for n in pf.plan.notes:
        say(f"[yellow]note[/yellow] {n}")
    if show_mde:
        from .stats.simulate import simulate
        with console.status("estimating MDE…"):
            o = simulate(reps=80, grid_reps=60, heterogeneity="low")
        say(f"MDE (≥ 80% power to reproduce, culprit moving half the scenarios): "
            f"{o['mde_repro']:.0%}" if o["mde_repro"] else "MDE: > 60%")
    say(f"Plan hash {pf.plan.plan_hash}")


def _load_pair(a):
    base, cand = differ.load_config(a.base), differ.load_config(a.cand)
    if getattr(a, "model", None):
        if base.get("model") != cand.get("model"):
            say("[yellow]warning[/yellow] --model replaces a model change between base and cand; that unit disappears")
        base["model"] = cand["model"] = a.model
    return base, cand


def _probe(a):
    if not getattr(a, "probe", False):
        return None
    from .agents.adk_agent import make_probe
    from .pipeline import DEFAULT_CACHE
    from .runner.cache import DiskCache
    return make_probe(DiskCache(a.cache_dir or DEFAULT_CACHE))


def cmd_preflight(a) -> int:
    from .agents.base import QuotaExhausted
    from .scenarios import load_scenarios
    base, cand = _load_pair(a)
    kw = {"k": a.k} if a.k else {}
    try:
        pf = preflight.build(base, cand, load_scenarios(a.scenarios), agent=a.agent, budget_usd=a.budget, probe=_probe(a), **kw)
    except QuotaExhausted as e:
        say(f"[yellow]Stopped: {e}[/yellow]")
        return 7
    _print_preflight(pf, show_mde=not a.no_mde)
    if preflight.over_budget(pf, a.budget):
        say("[red]Over budget: `ablate check` would refuse to run.[/red]")
        return 4
    return 0 if pf.plan.release_status == "OK" else 5


def _progress_factory(total_label: str):
    prog = Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                    TextColumn("{task.fields[stat]}"), TimeElapsedColumn(), console=console, transient=False)
    state = {"api": 0, "cached": 0, "invalid": 0}

    def make(desc: str, total: int):
        task = prog.add_task(desc, total=total, stat="")

        def cb(r, from_cache):
            state["api"] += r.api_calls
            state["cached"] += r.cached_calls
            state["invalid"] += r.status == "INVALID"
            prog.update(task, advance=1, stat=(f"api {state['api']} · cached {state['cached']} · INVALID {state['invalid']}"
                                               + (" · resumed" if from_cache else "")))
        return cb
    return prog, make, state


def cmd_check(a) -> int:
    from .agents.base import MissingCredentials, ModelVersionDrift, QuotaExhausted
    from .pipeline import BudgetExceeded, run_check
    from .scenarios import load_scenarios

    base, cand = _load_pair(a)
    out = Path(a.out)
    n_trials_hint = None
    try:
        kw = {"k": a.k} if a.k else {}
        pf = preflight.build(base, cand, load_scenarios(a.scenarios), agent=a.agent, budget_usd=a.budget, **kw)
        _print_preflight(pf, show_mde=False)
        n_trials_hint = pf.plan.n_trials
        prog, make, state = _progress_factory("trials")
        with prog:
            cb = make(f"{a.agent} trials", n_trials_hint)
            res = run_check(base, cand, Path(a.scenarios), out, agent=a.agent, k=a.k, budget_usd=a.budget,
                            concurrency=a.concurrency, rpm=a.rpm, cache_dir=Path(a.cache_dir) if a.cache_dir else None,
                            progress=cb, probe=_probe(a))
    except BudgetExceeded as e:
        say(f"[red]{e}[/red]")
        return 4
    except ModelVersionDrift as e:
        say(f"[red bold]Stopped — {e}[/red bold]")
        say("Treating this run as DRIFT; no attribution while the returned model version differs from the pinned id.")
        return 6
    except QuotaExhausted as e:
        say(f"[yellow]Stopped: {e}[/yellow]")
        say("Finished trials and model calls are cached. Rerun the same command after the quota resets to resume.")
        return 7
    except MissingCredentials as e:
        say(f"[red]{e}[/red]. Set GOOGLE_API_KEY (or use --agent scripted). Finished trials stay cached; rerun to resume.")
        return 2
    v = res.verdict
    say("")
    console.print(res.pr_markdown, markup=False)
    say(f"[dim]API calls this run: {state['api']} · served from cache: {state['cached']}[/dim]")
    say(f"Wrote {out / 'pr_comment.md'}, {out / 'report.html'}, {out / 'verdict.json'}, {out / 'results.jsonl'}")
    return EXIT.get(v.decision, 3)


def cmd_report(a) -> int:
    from .pipeline import finish
    from .plan import load
    from .results import read_jsonl
    from .verdict import verdict

    run = Path(a.run)
    plan = load(run)
    results = read_jsonl(run / "results.jsonl")
    out = finish(run, plan, verdict(plan, results), results)
    console.print(out.pr_markdown, markup=False)
    return EXIT.get(out.verdict.decision, 3)


def cmd_pytest_gen(a) -> int:
    from .report.pytest_gen import generate
    for arm in a.arm:
        p = generate(Path(a.run), arm, Path(a.out))
        say(f"wrote {p}")
    return 0


def cmd_fidelity(a) -> int:
    from .fidelity import check_results
    from .results import read_jsonl
    results = read_jsonl(Path(a.run) / "results.jsonl")
    if a.arm:
        results = [r for r in results if r.arm == a.arm]
    rep = check_results(results)
    say(f"replayed {rep['checked']} trials: {rep['passed']} exact matches")
    for f in rep["failures"]:
        say(f"  [red]mismatch[/red] {f['trial']}: {f['why']}")
    return 0 if rep["ok"] else 1


def cmd_draft(a) -> int:
    from .agents.base import MissingCredentials, QuotaExhausted
    from .drafter import draft_and_gate
    from .results import read_jsonl
    results = read_jsonl(Path(a.run) / "results.jsonl")
    try:
        out = draft_and_gate(results, tool=a.tool, model=a.model, out_dir=Path(a.run) / "drafts")
    except MissingCredentials as e:
        say(f"[red]{e}[/red]. Set GOOGLE_API_KEY to run a live draft.")
        return 2
    except QuotaExhausted as e:
        say(f"[yellow]Stopped: {e}[/yellow]")
        return 7
    say(f"draft for {a.tool}: {'ACCEPTED' if out['accepted'] else 'REJECTED'} by the fidelity gate "
        f"(replays recorded runs exactly ({out['gate']['passed']}/{out['gate']['checked']}))")
    if out.get("model_version"):
        say(f"returned model version: {out['model_version']}")
    say(f"code: {out['path']}")
    if out.get("raw_path"):
        say(f"raw response: {out['raw_path']}")
    return 0 if out["accepted"] else 1


# ---------------------------------------------------------------------------
# envelopes


def cmd_envelopes(a) -> int:
    from . import envelopes
    env = Path(a.dir)
    sealed = Path(a.sealed_dir)
    if a.action == "seal":
        meta = envelopes.seal(env, sealed, count=a.count)
        t = Table(title=f"Sealed {len(meta['envelopes'])} envelopes at {meta['sealed_at']}")
        t.add_column("Envelope")
        t.add_column("SHA-256(label ‖ salt)")
        for rid, h in meta["envelopes"].items():
            t.add_row(rid, h)
        console.print(t)
        say(f"Commitments in {env / 'hashes.json'}; labels and salts in {sealed} (outside the run directory).")
        return 0
    if a.action == "run":
        prog, make, state = _progress_factory("envelopes")
        n = 0
        with prog:
            def factory(rid):
                return make(rid, 1080)
            idx = envelopes.run(env, Path(a.scenarios), agent=a.agent, budget_usd=a.budget,
                                concurrency=a.concurrency, rpm=a.rpm, progress=factory)
        t = Table(title="Blind verdicts (labels not read)")
        for c in ("Envelope", "Decision", "Status", "Named"):
            t.add_column(c)
        for rid, r in idx["releases"].items():
            t.add_row(rid, r["decision"], r["status"], ", ".join(r["named"]) or "—")
            n += 1
        console.print(t)
        return 0
    if a.action == "reveal":
        out = envelopes.reveal(env, sealed)
        t = Table(title="Reveal")
        for c in ("Envelope", "Hash", "Planted", "Expected", "Ablate said", "Correct"):
            t.add_column(c)
        for r in out["rows"]:
            exp = f"{r['expected_decision']} {', '.join(r['expected_named'] or []) or ''}".strip()
            got = f"{r['decision']} {', '.join(r['named'] or []) or ''}".strip() if r["decision"] else "not run"
            t.add_row(r["release"], "match" if r["hash_ok"] else "[red]MISMATCH[/red]", str(r["kind"]), exp, got,
                      "[green]yes[/green]" if r["correct"] else "[red]no[/red]")
        console.print(t)
        say(f"Hashes recomputed: {'all match hashes.json' if out['hashes_ok'] else 'MISMATCH'} · "
            f"sealed {out['sealed_at']} · run {out['run_at']} ({'run after seal' if out['ordered'] else 'order not verified'})")
        say(f"Score: {out['correct']}/{out['total']} envelopes answered correctly")
        return 0 if out["hashes_ok"] else 6
    raise ValueError(a.action)


# ---------------------------------------------------------------------------
# console


def cmd_console(a) -> int:
    from .console.server import Console, make_server
    srv = make_server(Console(Path(a.dir), Path(a.sealed_dir), Path(a.runs)), port=a.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    say(f"Ablate console on [bold]{url}[/bold] (envelopes {a.dir}, runs {a.runs}). Ctrl+C stops it.")
    if a.open:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ablate", description="Release-gate culprit finder for ADK + Gemini agents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("simulate", help="operating characteristics of the design (planted-fault simulation)")
    s.add_argument("--reps", type=int, default=300)
    s.add_argument("--grid-reps", type=int, default=120)
    s.add_argument("--affected", type=int, default=12, help="scenarios the planted culprit moves")
    s.add_argument("--effect", type=float, default=0.40, help="planted violation-rate increase on affected scenarios")
    s.add_argument("--heterogeneity", choices=["low", "high", "both"], default="both")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--json", help="also write results to this JSON file")
    s.set_defaults(fn=cmd_simulate)

    def common(sp):
        sp.add_argument("--base", default=str(PROJECT_DIR / "configs" / "base.yaml"))
        sp.add_argument("--cand", default=str(PROJECT_DIR / "configs" / "cand.yaml"))
        sp.add_argument("--scenarios", default=str(PROJECT_DIR / "scenarios" / "retail.yaml"))
        sp.add_argument("--agent", choices=["scripted", "adk"], default="scripted")
        sp.add_argument("--budget", type=float, default=None, help="hard USD cap on the estimated cost")
        sp.add_argument("--k", type=int, default=None, help="trials per scenario per arm (default 5; changes the plan)")
        sp.add_argument("--probe", action="store_true", help="send one live probe request per distinct model config")
        sp.add_argument("--cache-dir", default=None)
        sp.add_argument("--model", default=None,
                        help="use this model in both configs, e.g. ollama_chat/qwen2.5:7b (local, no quota)")

    s = sub.add_parser("preflight", help="lint, arms, cost, budget and floor warnings")
    common(s)
    s.add_argument("--no-mde", action="store_true")
    s.set_defaults(fn=cmd_preflight)

    s = sub.add_parser("check", help="run the release gate end to end")
    common(s)
    s.add_argument("--out", default=str(PROJECT_DIR / "runs" / "latest"))
    s.add_argument("--concurrency", type=int, default=4)
    s.add_argument("--rpm", type=int, default=None, help="max model requests per minute (live agent)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("report", help="re-render reports from a finished run")
    s.add_argument("--run", required=True)
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("pytest-gen", help="emit Layer A + Layer B pytest files for arms of a run")
    s.add_argument("--run", required=True)
    s.add_argument("--arm", action="append", required=True)
    s.add_argument("--out", default=str(PROJECT_DIR / "tests_generated"))
    s.set_defaults(fn=cmd_pytest_gen)

    s = sub.add_parser("fidelity", help="replay recorded tool sequences; require exact state + observation match")
    s.add_argument("--run", required=True)
    s.add_argument("--arm", default=None)
    s.set_defaults(fn=cmd_fidelity)

    s = sub.add_parser("draft", help="(Should tier) Gemini drafts a tool's world function from traces; fidelity gate decides")
    s.add_argument("--run", required=True)
    s.add_argument("--tool", default="get_order_details")
    s.add_argument("--model", default="gemini-3.8-flash")
    s.set_defaults(fn=cmd_draft)

    s = sub.add_parser("envelopes", help="sealed-envelope benchmark: seal | run | reveal")
    s.add_argument("action", choices=["seal", "run", "reveal"])
    s.add_argument("--dir", default=str(PROJECT_DIR / "envelopes"))
    s.add_argument("--sealed-dir", default=str(PROJECT_DIR / ".sealed"))
    s.add_argument("--count", type=int, default=5)
    s.add_argument("--scenarios", default=str(PROJECT_DIR / "scenarios" / "retail.yaml"))
    s.add_argument("--agent", choices=["scripted", "adk"], default="scripted")
    s.add_argument("--budget", type=float, default=None)
    s.add_argument("--concurrency", type=int, default=4)
    s.add_argument("--rpm", type=int, default=None)
    s.set_defaults(fn=cmd_envelopes)

    s = sub.add_parser("console", help="local web console: releases and the sealed-envelope assurance page")
    s.add_argument("--dir", default=str(PROJECT_DIR / "envelopes"), help="envelope directory (hashes.json)")
    s.add_argument("--sealed-dir", default=str(PROJECT_DIR / ".sealed"))
    s.add_argument("--runs", default=str(PROJECT_DIR / "runs"), help="directory of `ablate check` outputs")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="open the console in the default browser")
    s.set_defaults(fn=cmd_console)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
