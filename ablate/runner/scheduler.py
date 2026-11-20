"""Scheduler: every (scenario, arm, trial) job, interleaved in a seeded random
order so arms share the same wall-clock conditions; async with a concurrency
cap, a requests-per-minute limiter, and exponential backoff with full jitter
when the agent reports an infrastructure error (429, 5xx, timeout).

A finished trial is written to the trial cache before the next one starts,
so a killed run resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from typing import Callable

from ..agents.base import AgentInfraError
from ..oracle import contracts
from ..plan import Plan, trial_seed
from ..results import TrialResult
from ..world.retail.tools import RetailWorld
from .cache import DiskCache


class RateLimiter:
    """Sliding-window limiter: at most `rpm` acquisitions per 60 s."""

    def __init__(self, rpm: int | None) -> None:
        self.rpm = rpm
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self.rpm:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] >= 60.0:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                await asyncio.sleep(60.0 - (now - self._times[0]) + 0.01)


async def run_one(plan: Plan, cfg: dict, scenario: dict, arm: str, trial: int, agent, cache: DiskCache,
                  *, max_retries: int = 6, base_delay: float = 2.0, sleep=asyncio.sleep) -> TrialResult:
    key = DiskCache.trial_key(scenario["id"], arm, trial, plan.plan_hash)
    seed = trial_seed(plan.seed, scenario["id"], arm, trial)
    world = RetailWorld(seed)
    last_err = ""
    for attempt in range(max_retries + 1):
        world.reset(seed)
        initial = world.snapshot()
        try:
            run = await agent.run_trial(cfg, scenario, world, seed=seed, cache_key=key)
        except AgentInfraError as e:
            last_err = str(e)
            if attempt == max_retries or not getattr(e, "retryable", True):
                break
            delay = random.uniform(0, base_delay * (2 ** attempt))  # full jitter
            await sleep(delay)
            continue
        final = world.snapshot()
        h = world.state_hash()
        findings = contracts.evaluate(scenario, initial=initial, final=final, final_hash=h,
                                      call_log=world.call_log, effect_log=world.effect_log,
                                      structured_output=run.structured_output)
        return TrialResult(
            plan_hash=plan.plan_hash, scenario=scenario["id"], arm=arm, trial=trial, status="OK",
            violated=bool(findings), contracts=sorted({x.contract for x in findings}),
            model_requested=str(cfg.get("model") or ""), model_version=run.model_version, state_hash=h,
            effects=world.effect_log, trace=world.call_log, structured_output=run.structured_output,
            amount_inr_at_risk=contracts.amount_at_risk(findings), api_calls=run.api_calls,
            cached_calls=run.cached_calls, error=None,
        )
    return TrialResult(plan_hash=plan.plan_hash, scenario=scenario["id"], arm=arm, trial=trial, status="INVALID",
                       model_requested=str(cfg.get("model") or ""), error=last_err[:500])


def jobs_for(plan: Plan) -> list[tuple[str, str, int]]:
    jobs = [(s, a, t) for a in plan.arm_names for s in plan.scenarios for t in range(plan.k)]
    random.Random(plan.seed).shuffle(jobs)
    return jobs


async def run_plan(plan: Plan, configs: dict[str, dict], scenarios: dict[str, dict], agent, cache: DiskCache,
                   *, concurrency: int = 4, progress: Callable[[TrialResult, bool], None] | None = None,
                   max_retries: int = 6) -> list[TrialResult]:
    jobs = jobs_for(plan)
    sem = asyncio.Semaphore(concurrency)
    results: dict[tuple, TrialResult] = {}

    async def work(s: str, a: str, t: int) -> None:
        key = DiskCache.trial_key(s, a, t, plan.plan_hash)
        hit = cache.get_trial(key)
        if hit is not None:
            r = TrialResult.from_dict(hit)
            results[r.key] = r
            if progress:
                progress(r, True)
            return
        async with sem:
            r = await run_one(plan, configs[a], scenarios[s], a, t, agent, cache, max_retries=max_retries)
        if r.status == "OK":
            cache.put_trial(key, r.to_dict())  # INVALID trials are retried on resume
        results[r.key] = r
        if progress:
            progress(r, False)

    await asyncio.gather(*(work(*j) for j in jobs))
    return [results[(s, a, t)] for (s, a, t) in jobs]
