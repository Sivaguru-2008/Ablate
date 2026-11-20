# Ablate

Release-gate culprit finder for ADK + Gemini agents (implements *Ablate build spec v2.2*).

Given a **base** and a **candidate** agent config (prompt blocks, tool descriptions, model id,
generation config), Ablate runs both on the same 24 scenarios in a deterministic fake retail world,
checks with paired statistics whether the candidate violates more contracts, and names a change
**X** only when all three hold:

1. **necessity**: reverting X from the candidate makes it non-inferior to base (NI bound < δ = 10 pp)
2. **sufficiency**: adding X alone to base makes it significantly worse (Holm across units)
3. the **A/A control** is clean (replicate base arm, constant returned model version)

Otherwise it abstains with a priced next step. There is no LLM in the oracle or the verdict path:
contracts are Python over world state, the effect log and the agent's structured output.

## Quick start

```bash
uv sync
uv run ablate simulate                      # operating characteristics of the design
uv run ablate preflight                     # lint, arms, cost, floor warning, MDE
uv run ablate check --out runs/demo         # scripted agent: no API key needed
uv run pytest                               # full suite (~3 min)
```

Live run on Gemini via ADK (needs `GOOGLE_API_KEY`; configs use `gemini-3.5-flash-lite`):

```bash
uv run ablate preflight --agent adk --probe
uv run ablate check --agent adk --rpm 14 --out runs/live     # free tier: 15 req/min, 500 req/day
```

Free tier: about 5,900 requests are needed for 1,080 trials, so a free key finishes in about 12
days. When the daily cap hits, the run stops cleanly (exit 7) and the same command resumes later.
`preflight.py` has no list price for `gemini-3.5-flash-lite`, so it prices it at the most
expensive known tier (≈ $42 estimate); add the real price to `PRICES_PER_M` before using `--budget`.

Local model through Ollama (free, no quota; `uv sync --extra local`):

```bash
uv run ablate check --agent adk --model ollama_chat/qwen2.5:7b --concurrency 6 --out runs/local
```

Run Ollama with `OLLAMA_NUM_PARALLEL=4 OLLAMA_CONTEXT_LENGTH=8192` at least: the prompt is about
2,000 tokens, and Ollama's default 4K context silently truncates longer conversations. A
`#nothink` suffix (e.g. `ollama_chat/qwen3:8b#nothink`) switches a model's thinking mode off.

Every model call goes through `.ablate_cache/`; kill a run at any point and rerun the same
command to resume with zero repeated API calls.

Sealed-envelope benchmark (seal right before the demo, reveal on stage):

```bash
uv run ablate envelopes seal --count 5      # commits SHA-256(label || salt) to envelopes/hashes.json
uv run ablate envelopes run                 # blind: never reads .sealed/
uv run ablate envelopes reveal              # recomputes every hash, scores the verdicts
```

Web console (Releases + Assurance / Trust center), served on localhost only:

```bash
uv run ablate console --open                # envelopes/ + .sealed/ + runs/ by default
uv run ablate console --dir runs/envelopes_demo --sealed-dir runs/.sealed_demo
```

**Releases** lists every finished `ablate check` under `runs/` with its decision, named change,
violation rates and links to `report.html` / `pr_comment.md`. **Assurance** shows the sealed
envelopes: commitments from `hashes.json`, the blind verdicts, and a **Reveal** button per envelope
that recomputes that envelope's hash against the commitment and scores it (nothing is written);
**Reveal all** runs `ablate envelopes reveal` and writes `reveal.json`. The false-attribution bound
is a one-sided 95% Clopper-Pearson upper bound over the opened envelopes.

Other commands: `ablate report --run DIR`, `ablate pytest-gen --run DIR --arm cand --arm base`,
`ablate fidelity --run DIR`, `ablate draft --run DIR --tool get_order_details` (live, Should tier).

Exit codes for `check`: 0 APPROVE, 1 BLOCK, 3 NEEDS MORE EVIDENCE, 4 over budget.

### Cloud Run Job (stretch)

`python -m ablate.cloud_job` runs the gate once and uploads `pr_comment.md`, `report.html`,
`verdict.json`, `plan.json`, `preflight.json` and `results.jsonl` to
`gs://BUCKET/ablate-runs/<UTC time>-<plan hash>/`, plus `ablate-runs/LATEST` naming that run.
It is configured with `ABLATE_*` environment variables (see `ablate/cloud_job.py`) and exits 0
whatever the decision, so Cloud Run never retries a finished gate.

```bash
gcloud auth login
PROJECT=my-project BUCKET=my-ablate-reports bash deploy/cloud_run_job.sh
```

The script enables the APIs, creates the bucket, grants the job's service account write access,
builds the image remotely with Cloud Build (`--source .`, no local Docker), deploys the job,
executes it once and prints the uploaded PR comment. The project needs a billing account for
Cloud Run and Cloud Build; one scripted run fits in their free tiers. For a live Gemini run, store
the key in Secret Manager and add `AGENT=adk SECRET=<secret-name>`. Locally, point
`ABLATE_BUCKET` at a `file:///` URL to try the job without Cloud Storage.

## Design constants (all in the plan hash)

n = 24 scenarios, k = 5 trials per scenario per arm, δ = 10 pp, α = 0.05 familywise,
bootstrap B = 10,000, sign-flip permutations = 20,000, fixed seed. With 3 revertible units:
9 arms (base, cand, A/A, cand−X ×3, base+X ×3) and 1,080 trials.

## Verdict rules

| Condition | Result |
|---|---|
| A/A replicate differs from base (two-sided, unadjusted α) or returned model version changes | `DRIFT`, no attribution |
| INVALID rate of any arm differs from base (Fisher, α) | `INFRA_SUSPECTED` |
| cand not significantly worse than base (one-sided sign-flip) | `NOT_REPRODUCED` |
| nec ∧ suf | `CONFIRMED` |
| suf ∧ ¬nec | `MULTIPLE_CONTRIBUTORS` |
| nec ∧ ¬suf | `INTERACTION_SUSPECTED` |
| neither | `INCONCLUSIVE`, with "N more paired scenarios ≈ $X" |
| arm not dependency-closed / config rejected by API | `NOT_REVERTIBLE` / `INCOMPATIBLE` |

PR decision: **BLOCK** if reproduced and any unit is CONFIRMED or MULTIPLE_CONTRIBUTORS;
**APPROVE** if not reproduced and the equivalence interval sits inside ±δ; otherwise
**NEEDS MORE EVIDENCE**. The rupee impact line appears only with BLOCK or CONFIRMED and is
always labelled "under this scenario mix".

## Simulated operating characteristics (`ablate simulate`, 300 reps)

| Metric | low heterogeneity | high heterogeneity |
|---|---|---|
| Type I error, reproduction gate | 0.023 | 0.017 |
| False naming under the null | 0.000 | 0.003 |
| A/A false DRIFT | 0.010 | 0.013 |
| Power to reproduce (+40 pp on 12 of 24) | 0.993 | 0.993 |
| Power to CONFIRM the planted culprit | 0.710 | 0.627 |
| Decoy named | 0.017 | 0.007 |
| NI power | 0.757 | 0.673 |
| MDE, reproduction | 25 pp | 25 pp |
| Affected-scenario floor | 7 | 7 |

CONFIRM power is capped by NI power: the Newcombe bound deflated by the estimated design effect
is deliberately conservative (spec), so at k = 5 and base violation rates around 5 % roughly a
quarter of true culprits come back as MULTIPLE_CONTRIBUTORS or INCONCLUSIVE rather than CONFIRMED.
Ablate abstains in those cases; it does not guess.

## Build status

| Step | Exit test | Status |
|---|---|---|
| 1 stats + simulate | `ablate simulate` prints type I error, power, MDE, NI power, floor | passes |
| 2 verdict + scripted agent + seal | culprit CONFIRMED, decoys not named, nulls abstain, no API key; hashes.json written | passes |
| 3 differ + preflight lint | thinking-level rule → INCOMPATIBLE; non-closed arms → NOT_REVERTIBLE | passes |
| 4 world + oracle + ADK agent + recorder | 20 live trials on Flash-Lite, state-hashed | passes live on `gemini-3.5-flash-lite` (`gemini-2.5-flash-lite` is retired for new keys); offline it runs against a fake `BaseLlm` |
| 5 runner, cache, rate limit | kill mid-run, rerun, zero repeated API calls | passes (fake LLM, real ADK stack) |
| 6 fidelity + impact + reports | PR comment BLOCK with ₹ per 1,000; pytest red on cand, green on base | passes |
| 7 envelopes run/reveal | reveal recomputes every hash and matches hashes.json | passes (5/5 correct in a scripted run) |
| 8 drafter (Should) | Gemini drafts `get_order_details`; fidelity gate accepts/rejects | passes live: `gemini-3.8-flash` (`runs/demo/drafts/get_order_details.raw.json`, returned `model_version: gemini-3.8-flash`, `response_id: SzoAa8_AMIGh7PkPrMz1yQ4`) drafted `get_order_details` and the fidelity gate accepted it (replays recorded runs exactly (1,064/1,064)); wrong and unsafe drafts are rejected in tests |
| Stretch: Cloud Run Job | one execution writes the report to Cloud Storage | passes: job `ablate-gate` (asia-south1, remote Cloud Build) execution `ablate-gate-wnhw8` ran the 1,080-trial scripted gate and uploaded 6 objects (`BLOCK (scripted)` / `block.confirm` CONFIRMED) to `gs://ablate-reports-applied-range-496518-v2/ablate-runs/20261120T114036Z-889d8f9b7d8c/` (see `docs/cloud-run-proof.txt`) |

## Layout

```
ablate/
  cli.py  plan.py  differ.py  preflight.py  verdict.py  impact.py  envelopes.py
  drafter.py  fidelity.py  pipeline.py  results.py  scenarios.py  wording.py
  world/base.py  world/retail/{data_model.py, tools.py, policy.md, seed_db.json}
  oracle/contracts.py
  agents/{adk_agent.py, recorder_plugin.py, scripted_agent.py, base.py}
  runner/{scheduler.py, cache.py}
  stats/{intervals.py (vendored evalsig), tests.py, simulate.py}
  report/{pr_comment.py, html.py, pytest_gen.py}
configs/base.yaml  configs/cand.yaml  scenarios/retail.yaml  scripts/extract_seed_db.py
action.yml (GitHub Action stub)  THIRD_PARTY.md  tests/
```

## Decisions worth knowing (`# DECISION:` in code)

- **numpy** is a dependency beyond the spec list: the simulator runs the sign-flip and bootstrap
  thousands of times.
- **Affected-scenario floor = 7**: at n = 24 the sign-flip p-value is a Monte Carlo estimate; at
  m = 6 the best-case p (≈ 0.0157) is within about one Monte Carlo SD of α/3, so a 3-SD margin is
  required. Sufficiency additionally requires at least the floor of scenarios to move the bad way.
- **Holm for non-inferiority** uses the NI p-value: the smallest one-sided level at which the
  combined (Newcombe ∨ bootstrap) upper bound falls below δ, found by bisection.
- **DRIFT** compares returned model versions per *requested* model, so a model-id change unit is
  not mistaken for drift.
- **INFRA_SUSPECTED** tests every arm against base, unadjusted (more abstention, not less).
- Live-stack errors other than 429/5xx/timeouts make a trial INVALID, never a violation; the
  Fisher check turns a change that breaks the stack into INFRA_SUSPECTED.
- **amount_inr** uses a fixed USD→INR rate of 84.0 (`world/retail/tools.py`).
- Prices in `preflight.py` are list-price estimates for the budget cap; unknown models are priced
  at the most expensive known tier. Edit `PRICES_PER_M` if prices change.
- The default model is `gemini-2.5-flash-lite`; change `model:` in the configs if that id is retired.

## Live findings so far

- **Gemini (`gemini-3.5-flash-lite`), 12-trial pilot:** with the weakened confirm block the
  candidate cancelled or returned before the user's "yes" in 4 of 4 cancel/return scenarios;
  base waited for confirmation in 2 of 3. Both fell for the prompt-injection scenario. The full
  check is 50 of 1,080 trials in, paused by the free tier's 500-requests/day cap.
- **Local `qwen2.5:7b`, full 1,080 trials (2-turn scenarios):** NEEDS MORE EVIDENCE. Base broke a
  contract in 72.5% of trials, cand in 42.5%. Base mostly cancelled without the required status
  check, and in 5 scenarios it re-confirmed after the user's "yes", which the 2-turn scripted users
  could not answer. That second case was a scenario defect: cancel/return scenarios now have a
  third "go ahead" turn, and scenario text is part of the plan hash.
- **Local `qwen2.5:7b`, reduced run (k = 2, 432 trials, 3-turn scenarios):** NEEDS MORE
  EVIDENCE, no regression: cand 52.1% vs base 54.2% (one-sided p = 0.63). The third turn removed
  most unfinished tasks, but base still skips the required status check, so it fails half its
  scenarios and the gate flags that. On this model the weakened confirm block makes no measurable
  difference. k = 2 departs from the design constants; the report says so.
- **Local `qwen2.5-coder-tools:7b` (pilot):** ignored the confirm rule in base too, so unusable.
- **Preflight `--probe`, live:** clean on `gemini-3.5-flash`; on the retired `gemini-2.5-flash-lite` the API rejected every arm and preflight marked the release INCOMPATIBLE before any trial ran.
- **Gemma 4 31B on the free API:** tool calling works and the limit is 30 requests/minute, but
  the endpoint returned 503 "high demand" for most calls when tried.

The report warns when base itself fails most scenarios, and notes when the A/A control is
vacuous because decoding is deterministic.

## What this does and does not show

- **Constructed-fault caveat:** `configs/base.yaml` → `configs/cand.yaml` and the sealed envelopes
  use **constructed faults** (deliberately weakened policy blocks) inside a deterministic retail
  simulator extracted from `tau2-bench`. This tests whether Ablate's paired statistics and
  two-sided (necessity + sufficiency) gate separate a constructed culprit from decoys and abstain
  on nulls; it does not establish how often real production regressions are single-unit or how
  closely the simulator mirrors live traffic. Fidelity checks report that the world **replays
  recorded runs exactly (N/N)** — deterministic trace replay, not production equivalence.
- **Scripted vs live runs:** `--agent scripted` runs a deterministic feature-driven fake agent
  (1,080 trials in `runs/demo`, 5 sealed envelopes in `runs/envelopes_demo`, and Cloud Run Job
  `ablate-gate-wnhw8`); scripted outputs are labelled `scripted` and `constructed fault` and say
  nothing about Gemini.
- **Live model pair actually used & trials actually run:**
  - **Live Gemini (`gemini-3.5-flash-lite` base → `gemini-3.5-flash-lite` cand):** 20/20 trials
    passed in `test_live_flash_lite_20_trials`, a 12-trial pilot ran, and **50/1,080 trials**
    completed on the earlier 2-turn plan (`94452ca956aa`; 277 cached calls, all returning
    `model_version: gemini-3.5-flash-lite`) before Free Tier's 500 req/day limit paused the run
    (exit 7). On the current 3-turn plan (`533dc15fabcb`), **0/1,080 live Gemini trials** have
    completed yet (awaiting a funded key / quota).
  - **Local ADK + Ollama (`ollama_chat/qwen2.5:7b` base → `ollama_chat/qwen2.5:7b` cand):**
    **1,080/1,080 trials** (2-turn scenarios, `runs/local`) and **432/432 trials** (`k = 2`,
    3-turn scenarios, `runs/local_k2`) ran end to end.
- **Related tools (named honestly):**
  - **Shadow** — diagnoses PR regressions and checks proposed fixes, without paired two-sided
    (necessity + sufficiency) per-unit ablation arms or a published false-attribution control.
  - **ablate-cli** — performs section-level prompt ablation, without paired non-inferiority
    bounds, A/A drift checks, or priced abstention.
  - **git_bayesect** — a related Bayesian bisect tool for locating regressions across git commit
    history with noisy tests, rather than ablating individual config units inside a single release
    in both directions.

## Limits

- The scripted agent's behaviour is driven by config text features; it exists so the statistics
  and the envelope benchmark can be exercised without quota. It says nothing about Gemini.
- Contracts read structured output from a `submit_outcome` tool; a model that never calls it
  makes no claim, so `false_success_claim` cannot fire for that trial.
- The drafter's AST screen and reduced builtins guard against accidents; they are not a sandbox.
