"""`ablate check` end to end: preflight -> freeze plan -> run -> verdict -> reports."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import PROJECT_DIR, preflight, wording
from .plan import freeze
from .report import html as html_report
from .report import pr_comment
from .results import write_jsonl
from .runner.cache import DiskCache, assert_not_sealed
from .runner.scheduler import RateLimiter, run_plan
from .scenarios import load_scenarios
from .verdict import Verdict, verdict

DEFAULT_CACHE = PROJECT_DIR / ".ablate_cache"


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CheckOutput:
    pf: preflight.Preflight
    results: list
    verdict: Verdict
    run_dir: Path
    pr_markdown: str


def make_agent(name: str, cache: DiskCache, *, rpm: int | None = None, timeout_s: float = 120.0,
               version_log: Path | None = None, strict_model_pin: bool = False):
    if name == "scripted":
        from .agents.scripted_agent import ScriptedAgent
        return ScriptedAgent()
    if name == "adk":
        from .agents.adk_agent import AdkAgent
        return AdkAgent(cache=cache, limiter=RateLimiter(rpm), timeout_s=timeout_s,
                        version_log=version_log, strict_model_pin=strict_model_pin)
    raise ValueError(f"unknown agent {name!r} (scripted | adk)")


def run_check(base: dict, cand: dict, scenarios_path: Path, run_dir: Path, *, agent: str = "scripted",
              k: int | None = None, budget_usd: float | None = None, concurrency: int = 4, rpm: int | None = None,
              cache_dir: Path | None = None, progress=None, probe=None) -> CheckOutput:
    assert_not_sealed(run_dir)
    scenarios = load_scenarios(scenarios_path)
    kw = {"k": k} if k else {}
    pf = preflight.build(base, cand, scenarios, agent=agent, budget_usd=budget_usd, probe=probe, **kw)
    if preflight.over_budget(pf, budget_usd):
        raise BudgetExceeded(f"estimated ${pf.est_cost_usd:,.2f} > budget ${budget_usd:,.2f}; nothing was run")
    plan = pf.plan
    freeze(plan, run_dir)
    (run_dir / "arms").mkdir(exist_ok=True)
    for arm, cfg in pf.configs.items():
        safe = arm.replace("+", "_plus_").replace("/", "_")
        (run_dir / "arms" / f"{safe}.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    shutil.copyfile(scenarios_path, run_dir / "scenarios.yaml")
    (run_dir / "preflight.json").write_text(json.dumps({
        "issues": [i.__dict__ for i in pf.issues], "warnings": pf.warnings, "est_requests": pf.est_requests,
        "est_cost_usd": pf.est_cost_usd, "floor": pf.floor, "estimate": preflight.estimate_line(pf),
    }, indent=2), encoding="utf-8")

    results: list = []
    if plan.release_status == "OK":
        cache = DiskCache(cache_dir or DEFAULT_CACHE)
        ag = make_agent(agent, cache, rpm=rpm, version_log=run_dir / "model_versions.jsonl",
                        strict_model_pin=(agent == "adk"))
        results = asyncio.run(run_plan(plan, pf.configs, {s["id"]: s for s in scenarios}, ag, cache,
                                       concurrency=concurrency, progress=progress))
        write_jsonl(run_dir / "results.jsonl", results)
    else:
        write_jsonl(run_dir / "results.jsonl", [])
    v = verdict(plan, results)
    return finish(run_dir, plan, v, results, pf)


def finish(run_dir: Path, plan, v: Verdict, results, pf=None) -> CheckOutput:
    (run_dir / "verdict.json").write_text(json.dumps(v.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    md = pr_comment.render(plan, v, results)
    (run_dir / "pr_comment.md").write_text(md, encoding="utf-8")
    (run_dir / "report.html").write_text(html_report.render(plan, v, results), encoding="utf-8")
    wording.check(md, pr=True)
    return CheckOutput(pf, results, v, run_dir, md)
