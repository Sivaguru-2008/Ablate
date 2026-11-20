# Third-party material

Every vendored or adapted file carries a header comment naming its source
repository, commit and licence. This file collects those sources and their
licence notices.

| Source | Commit | Licence | Used in |
|---|---|---|---|
| [HZDF-2026/evalsig](https://github.com/HZDF-2026/evalsig) | `8bb9b78c6163ec03626239e451574107554fe230` | MIT | `ablate/stats/intervals.py` (vendored: `wilson_interval`, `newcombe_diff_interval`, `cluster_bootstrap_ci`, `z_two_sided`, `binom_cdf`, `_sign_flip_p`, `holm`, `n_paired`) |
| [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) | `c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4` | MIT | `ablate/world/retail/` (data model, tool bodies, policy text), `ablate/world/retail/seed_db.json` (small subset of `data/tau2/domains/retail/db.json`, extracted by `scripts/extract_seed_db.py`) |
| [google/adk-samples](https://github.com/google/adk-samples) | `ead8d7f74d5b1def48a5a7451aed8106aae8d2e6` | Apache-2.0 | Pattern only (plugin wiring in `core/python/safety-plugins/`); no code copied |
| [alepot55/agentrial](https://github.com/alepot55/agentrial) | `0c8ef0366c7a8f7732413f11763889b5358dbabc` | MIT | Pattern only (pytest plugin layout, GitHub Action inputs/outputs); no code copied |
| [hauntsaninja/git_bayesect](https://github.com/hauntsaninja/git_bayesect) | — | MIT | Reference reading only; no code copied |

## evalsig — MIT License

```
MIT License

Copyright (c) 2026 Shijing Ge

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## tau2-bench — MIT License

```
MIT License

Copyright (c) 2025 Sierra Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
