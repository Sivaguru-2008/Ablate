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

Live run on Gemini via ADK (reads `GOOGLE_API_KEY` from `.env` or environment; configs use `gemini-3.5-flash-lite`):

```bash
uv run ablate preflight --agent adk --probe --budget 15
uv run ablate check --agent adk --rpm 60 --budget 15 --out runs/live_v2
```

At the official Standard Paid Tier list price for `gemini-3.5-flash-lite` (`$0.30` input / `$2.50`
output per 1M tokens in `PRICES_PER_M`), preflight estimates `~6,480` requests and `≈ $6.80` for
1,080 trials; the completed live run (`runs/live_v2`, `docs/live-result.md`) spent `$4.36` across
`12,496,579` tokens. On a free-tier key (15 req/min, 500 req/day), the run stops cleanly when the
daily cap hits (exit 7) and the same command resumes later.

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

## Live findings

- **Live Gemini (`gemini-3.5-flash-lite`), full 1,080-trial run (`runs/live_v2`, `docs/live-result.md`):**
  **`BLOCK`** (`REPRODUCED`: candidate violated contracts in **`75.8%`** of trials vs **`12.5%`**
  on base, paired sign-flip one-sided `p < 0.0001`). Named **`block.confirm`**
  (`MULTIPLE_CONTRIBUTORS`: `+60.8 pp` alone, `17/24` scenarios worse, Holm `p = 0.0004`; reverting
  it brought violations down to `15.0%` / `+2.5 pp`, NI upper bound `+23.5 pp` > `δ = 10 pp` at
  `k = 5`). Both decoys (`block.role`, `gen.temperature`) stayed `INCONCLUSIVE` (zero decoys named).
  A/A control was clean (`p = 0.3213`, non-vacuous); all `6,918/6,918` logged calls returned
  `model_version: gemini-3.5-flash-lite` (zero drift); `1,078/1,080` OK (`2` transient
  `ServerDisconnectedError` INVALIDs, Fisher min `p = 1.0000`); fidelity check `replays recorded
  runs exactly (1078/1078)`. Actual token cost: **`$4.36`** (`12.22M` input, `0.28M` output +
  thinking). Generated pytest: `cand` RED (`19 failed, 6 passed`); `base` surfaced 3 baseline
  scenario failures including prompt injection `5/5` (`4 failed, 21 passed`).
- **Live preflight `--probe` (`docs/probe-output.txt`):** clean (`exit 0`, zero issues) on
  `gemini-3.5-flash-lite` for both `base` (`temp=0.0`) and `cand` (`temp=0.3`); on retired
  `gemini-2.5-flash-lite` the API rejected every arm as `INCOMPATIBLE` before any trial ran.
- **Live drafter (`ablate draft`, Step 8):** `gemini-3.8-flash` (`runs/demo/drafts/get_order_details.raw.json`)
  drafted `get_order_details`; fidelity gate accepted it (`replays recorded runs exactly (1064/1064)`).
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
- **Live model pair actually used & trials actually run:**
  - **Live Gemini (`gemini-3.5-flash-lite` base → `gemini-3.5-flash-lite` cand, `docs/live-result.md`):**
    **1,080/1,080 trials** completed on the frozen 3-turn plan (`e27914156d7a`, `1,078` OK + `2`
    transient INVALIDs, `6,918/6,918` calls returning `model_version: gemini-3.5-flash-lite`,
    `$4.36` actual token cost) → **`BLOCK`**, naming `block.confirm` (`MULTIPLE_CONTRIBUTORS`,
    zero decoys named; A/A `p = 0.3213`).
  - **Local ADK + Ollama (`ollama_chat/qwen2.5:7b` base → `ollama_chat/qwen2.5:7b` cand):**
    **1,080/1,080 trials** (2-turn scenarios, `runs/local`) and **432/432 trials** (`k = 2`,
    3-turn scenarios, `runs/local_k2`) ran end to end (`NEEDS MORE EVIDENCE`).
- **Scripted, constructed fault runs (not live Gemini):** `--agent scripted` runs a deterministic
  feature-driven fake agent (`1,080` trials in `runs/demo`, `5` sealed envelopes in
  `runs/envelopes_demo`, and Cloud Run Job `ablate-gate-wnhw8`); every scripted run is labelled
  **scripted, constructed fault** and says nothing about Gemini.
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
