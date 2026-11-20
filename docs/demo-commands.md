# Ablate 3-Minute Demo Command Sequence

> Run all commands from `d:\google buildathon\ablate`.
> Where `uv` is on PATH, use `uv run ablate ...`; in PowerShell without `uv` on PATH, substitute `.\.venv\Scripts\ablate.exe ...`.

---

## Pre-demo setup (run yourself just before going on stage)

Seal fresh envelopes into `envelopes/` + `.sealed/` (do **not** commit `.sealed/`), run them blind, and keep the labels sealed until stage reveal:

```powershell
# 1. Seal 5 fresh constructed-fault envelopes (offline, writes envelopes/hashes.json + .sealed/labels.json)
.\.venv\Scripts\ablate.exe envelopes seal --count 5

# 2. Run the blind gate over all 5 envelopes (scripted agent — offline, zero API calls, ~5 s from cache)
.\.venv\Scripts\ablate.exe envelopes run

# 3. Optional: launch the local Trust Center web console (reads local disk only)
.\.venv\Scripts\ablate.exe console --dir envelopes --sealed-dir .sealed --runs runs --port 8765 --open
```

*(If `GOOGLE_API_KEY` is set with paid quota, you can also run `.\.venv\Scripts\ablate.exe check --agent adk --rpm 14 --out runs/live_v2` ahead of time so the 1,080 live Gemini trials populate `.ablate_cache/` before the demo.)*

---

## Live 3-minute stage sequence

| Time | Step | Mode (Cache / Live / Offline) | Command |
|---|---|---|---|
| **0:00 – 0:30** | **Preflight & frozen plan** | **Live probe** *(cached after initial probe if `GOOGLE_API_KEY` is set; offline lint without `--probe`)* | `.\.venv\Scripts\ablate.exe preflight --agent adk --probe --no-mde` |
| **0:30 – 1:10** | **Run release gate (`BLOCK`, `block.confirm` `CONFIRMED`, 0 decoys)** | **From `.ablate_cache/`** *(if `runs/live_v2` was pre-warmed with `GOOGLE_API_KEY`)* — or **offline scripted** fallback below | `.\.venv\Scripts\ablate.exe check --agent adk --out runs/live_v2` *(cached)*<br>or `.\.venv\Scripts\ablate.exe check --out runs/demo` *(scripted, instant from cache)* |
| **1:10 – 1:40** | **Fidelity replay + generated red/green pytest** | **Offline / local replay** *(reads recorded `results.jsonl`, zero API calls)* | `.\.venv\Scripts\ablate.exe fidelity --run runs/demo`<br>`.\.venv\Scripts\ablate.exe pytest-gen --run runs/demo --arm cand --arm base --out tests_generated`<br>`.\.venv\Scripts\python.exe -m pytest -q tests_generated/test_ablate_cand.py tests_generated/test_ablate_base.py` |
| **1:40 – 2:25** | **Sealed-envelope reveal on stage** | **Offline / disk** *(recomputes SHA-256 commitments against `envelopes/hashes.json`)* | `.\.venv\Scripts\ablate.exe envelopes reveal` *(or click **Reveal** per envelope in the console at `http://127.0.0.1:8765/#/assurance`)* |
| **2:25 – 3:00** | **Should-tier drafter + Cloud Run artifact** | **Live Gemini** *(1 call to `gemini-3.8-flash` for `ablate draft`; **from disk / GCS** for Cloud Run listing)* | `.\.venv\Scripts\ablate.exe draft --run runs/demo --tool get_order_details`<br>`type docs\cloud-run-proof.txt` |

### What runs live vs from cache
- **Live Gemini API calls** (require `GOOGLE_API_KEY` and quota):
  - `ablate preflight --agent adk --probe` (1 minimal ping per distinct `(model, generation_config)`, cached thereafter in `.ablate_cache/calls/`).
  - `ablate draft --run runs/demo --tool get_order_details` (1 Gemini call to draft the tool; writes `get_order_details.py` and `get_order_details.raw.json` with returned `model_version`; fidelity gate replays recorded runs locally).
  - `ablate check --agent adk --rpm 14 --out runs/live_v2` (uses `.ablate_cache/` for any finished call/trial; makes live calls only for uncached trials, logs every `model_version` to `model_versions.jsonl`, and stops immediately on `DRIFT` or daily quota cap).
- **From disk / cache (zero API calls, sub-second to a few seconds):**
  - `ablate report --run runs/demo` (re-renders `pr_comment.md` and `report.html`).
  - `ablate fidelity --run runs/demo` (prints `replays recorded runs exactly (1080/1080)`).
  - `ablate pytest-gen` + `pytest` red (`cand`) / green (`base`).
  - `ablate envelopes seal` / `run` (scripted) / `reveal` and `ablate console`.

---

## Fallback (100% offline simulator mode — zero network, zero API key)

If venue Wi-Fi drops or Gemini API quota is exhausted, run the complete demo offline with the scripted constructed-fault agent and design simulation:

```powershell
# 1. Preflight without live probe (offline lint, cost estimate, affected-scenario floor, plan hash)
.\.venv\Scripts\ablate.exe preflight --no-mde

# 2. Full 1,080-trial scripted constructed-fault gate (zero API calls, instant from cache)
.\.venv\Scripts\ablate.exe check --out runs/demo

# 3. Deterministic trace replay (outputs: replays recorded runs exactly (1080/1080))
.\.venv\Scripts\ablate.exe fidelity --run runs/demo

# 4. Generated Layer A + Layer B pytest (red on cand, green on base)
.\.venv\Scripts\ablate.exe pytest-gen --run runs/demo --arm cand --arm base --out tests_generated
.\.venv\Scripts\python.exe -m pytest -q tests_generated/test_ablate_cand.py tests_generated/test_ablate_base.py

# 5. Reveal sealed envelopes (recomputes SHA-256 commitments)
.\.venv\Scripts\ablate.exe envelopes reveal

# 6. Show pre-recorded design simulation numbers (only statistics numbers allowed on slides)
type docs\design-simulation.txt
```

*(Optionally run `.\.venv\Scripts\ablate.exe simulate --seed 7` offline to reproduce `docs/design-simulation.txt` live, ~1–2 minutes.)*
