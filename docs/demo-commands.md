# Ablate 3-Minute Demo Command Sequence

> Run all commands from `d:\google buildathon\ablate`.
> Where `uv` is on PATH, use `uv run ablate ...`; in PowerShell without `uv` on PATH, substitute `.\.venv\Scripts\ablate.exe ...`.
> The CLI loads `GOOGLE_API_KEY` (and `GOOGLE_GENAI_USE_VERTEXAI=true` for Vertex AI Express keys) automatically from `.env`.

---

## Pre-demo setup (run yourself just before going on stage)

Seal fresh **scripted, constructed fault** envelopes into `envelopes/` + `.sealed/` (do **not** commit `.sealed/`), run them blind, and keep the labels sealed until stage reveal:

```powershell
# 1. Seal 5 fresh scripted, constructed-fault envelopes (offline, writes envelopes/hashes.json + .sealed/labels.json)
.\.venv\Scripts\ablate.exe envelopes seal --count 5

# 2. Run the blind gate over all 5 envelopes (scripted, constructed fault — zero API calls, ~5 s)
.\.venv\Scripts\ablate.exe envelopes run

# 3. Optional: launch the local Trust Center web console (reads local disk only)
.\.venv\Scripts\ablate.exe console --dir envelopes --sealed-dir .sealed --runs runs --port 8765 --open
```

*(The full 1,080-trial live Gemini run `runs/live_v2` is already recorded in `.ablate_cache/` and summarized in `docs/live-result.md`.)*

---

## Live 3-minute stage sequence

| Time | Step | Mode (Cache / Live / Offline) | Command |
|---|---|---|---|
| **0:00 – 0:30** | **Preflight & live probe (`gemini-3.5-flash-lite`, `≈ $6.80`)** | **From `.ablate_cache/`** *(after initial live probe recorded in `docs/probe-output.txt`; live on uncached config)* | `.\.venv\Scripts\ablate.exe preflight --agent adk --probe --budget 15 --no-mde` |
| **0:30 – 1:10** | **Show real 1,080-trial live Gemini gate (`BLOCK`, `block.confirm` named, 0 decoys, `$4.36` actual cost)** | **From disk (`runs/live_v2`, `docs/live-result.md`)** *(re-renders `pr_comment.md` + `report.html` from the completed live Gemini `results.jsonl`, zero new API calls)* | `.\.venv\Scripts\ablate.exe report --run runs/live_v2` |
| **1:10 – 1:40** | **Fidelity replay + generated pytest on the live Gemini run** | **Offline / local replay** *(replays all `1,078` OK live Gemini trials deterministically, zero API calls)* | `.\.venv\Scripts\ablate.exe fidelity --run runs/live_v2`<br>`.\.venv\Scripts\ablate.exe pytest-gen --run runs/live_v2 --arm cand --arm base --out tests_generated`<br>`.\.venv\Scripts\python.exe -m pytest -q tests_generated/test_ablate_cand.py tests_generated/test_ablate_base.py` |
| **1:40 – 2:25** | **Sealed-envelope reveal on stage (scripted, constructed fault)** | **Offline / disk** *(recomputes SHA-256 commitments against `envelopes/hashes.json`)* | `.\.venv\Scripts\ablate.exe envelopes reveal` *(or click **Reveal** per envelope in the console at `http://127.0.0.1:8765/#/assurance`)* |
| **2:25 – 3:00** | **Should-tier live drafter (`gemini-3.8-flash`) + Cloud Run artifact** | **Live Gemini** *(1 call to `gemini-3.8-flash` for `ablate draft`; **from disk** for Cloud Run listing)* | `.\.venv\Scripts\ablate.exe draft --run runs/demo --tool get_order_details`<br>`type docs\cloud-run-proof.txt` |

### What runs live vs from cache
- **Completed live Gemini evidence (already cached in `.ablate_cache/` & `runs/live_v2/`):**
  - `ablate check --agent adk --rpm 60 --budget 15 --out runs/live_v2` ran all `1,080` trials on live `gemini-3.5-flash-lite` (`1,078` OK, `2` transient `ServerDisconnectedError` INVALIDs, `6,918/6,918` calls returning `model_version: gemini-3.5-flash-lite`, `$4.36` actual cost) → **`BLOCK`**, naming `block.confirm` (`MULTIPLE_CONTRIBUTORS`), zero decoys named, A/A `p = 0.3213` (see `docs/live-result.md`).
  - `ablate preflight --agent adk --probe --budget 15` (`docs/probe-output.txt`): cached after initial live probe on `gemini-3.5-flash-lite` (`base` and `cand`, exit `0`).
  - `ablate draft --run runs/demo --tool get_order_details`: 1 live Gemini call (`gemini-3.8-flash`, writes `get_order_details.py` + `get_order_details.raw.json`), fidelity gate replays `1,064/1,064` recorded runs locally.
- **From disk / offline (zero API calls, sub-second to a few seconds):**
  - `ablate report --run runs/live_v2` (re-renders live Gemini `pr_comment.md` + `report.html`).
  - `ablate fidelity --run runs/live_v2` (outputs `replays recorded runs exactly (1078/1078)`).
  - `ablate pytest-gen` + `pytest` (`cand` RED `19 failed / 6 passed`; `base` `4 failed / 21 passed` surfacing baseline injection/delivered-cancel failures).
  - `ablate envelopes seal` / `run` / `reveal` (scripted, constructed fault) and `ablate console`.

---

## Fallback (100% offline simulator mode — scripted, constructed fault, zero network, zero API key)

If venue Wi-Fi drops or live credentials are unavailable, run the complete demo offline using the **scripted, constructed fault** agent and the pre-recorded live result + design simulation:

```powershell
# 1. Preflight without live probe (offline lint, cost estimate, affected-scenario floor, plan hash)
.\.venv\Scripts\ablate.exe preflight --no-mde

# 2. Full 1,080-trial scripted, constructed-fault gate (zero API calls, instant from cache)
.\.venv\Scripts\ablate.exe check --out runs/demo

# 3. Deterministic trace replay on the scripted, constructed-fault run (1080/1080)
.\.venv\Scripts\ablate.exe fidelity --run runs/demo

# 4. Generated Layer A + Layer B pytest on scripted, constructed-fault run (cand red, base green)
.\.venv\Scripts\ablate.exe pytest-gen --run runs/demo --arm cand --arm base --out tests_generated
.\.venv\Scripts\python.exe -m pytest -q tests_generated/test_ablate_cand.py tests_generated/test_ablate_base.py

# 5. Reveal sealed envelopes (scripted, constructed fault; recomputes SHA-256 commitments)
.\.venv\Scripts\ablate.exe envelopes reveal

# 6. Show recorded live Gemini result + design simulation numbers (only statistics numbers allowed on slides)
type docs\live-result.md
type docs\design-simulation.txt
```

*(Optionally run `.\.venv\Scripts\ablate.exe simulate --seed 7` offline to reproduce `docs/design-simulation.txt`, ~1–2 minutes.)*
