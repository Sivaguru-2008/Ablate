# Ablate explained: a plain-language guide

Nov 20, 2026 · @Sivaguru

Ablate checks whether a new version of an AI agent got worse, and names the change responsible only when the evidence holds both ways. This guide explains every feature in build spec v2.2 without assuming any statistics or coding background.

## 1. The problem and the idea

**The problem.** Companies now use AI agents that take real actions: issuing refunds, cancelling orders, changing bookings or accounts. These agents are steered by written instructions (the prompt), tool descriptions, a chosen AI model and some settings. Teams change these often, usually several at once in one release. When the agent starts misbehaving, nobody can easily say which change caused it. Models can also change under you: on 8 October Google rerouted two Gemini Flash model IDs to newer versions, so an agent can change even when its team ships nothing.

**Why it's hard.** AI agents are random. Run the same agent on the same request twice and you can get different behaviour. So one failed test proves little, and "it worked when I tried it" proves little too.

**What Ablate does.** It is a release gate: before a change is merged, it checks whether the agent got worse. If it did, it tries to name the change responsible, but only when the evidence holds in both directions. When the evidence is not strong enough, it says "I can't tell yet" and tells you what more testing would cost.

**Everyday analogy.** Your dish tastes wrong after you changed four things in the recipe. A careful cook doesn't guess. They cook many batches. They take out one change and see if the dish recovers. They add only that change to the old recipe and see if the dish goes wrong. Only if both happen do they blame that ingredient. If tasters disagree too much to tell, they say so, and say how many more batches would settle it.

**Pitch (exact wording).** "Your agent moves money. Before you merge, Ablate tells you whether this change made it worse and which change did it, shown both ways. When it can't tell yet, it says so and prices the next step."

**The line said in the first 30 seconds.** "Ablate names a culprit only when removing it fixes the regression and adding it alone causes it, and we publish how often it's wrong on a planted-fault benchmark."

**Who it's for.** A tech lead in a 2–20 person team, or an agency, running agents built with Google's ADK on Gemini, whose tools change real data. It runs when a pull request (PR) touches prompts, tool descriptions, settings or the model, or when Google deprecates, reroutes or forces a model migration.

**What it is not.** Not a monitoring tool, not a dashboard, not an AI that judges other AIs, not for multi-agent systems, not a general evaluation platform.

### Key words

| Word | Plain meaning |
| --- | --- |
| Agent | An AI program that talks to a customer and calls tools that change data |
| Base | The current, trusted version of the agent |
| Candidate | The new version you want to merge |
| Unit | One separate change between base and candidate: an instruction block, a tool description, the model ID or a setting |
| Scenario | One test conversation: scripted customer messages plus a fixed starting world. There are 24 |
| Trial | One run of the agent on one scenario. Each scenario is run 5 times per version |
| Arm | One version of the agent being tested (base, candidate, or a mix such as candidate with one change removed) |
| World | A fake, fully predictable shop (orders, users, refunds) the agent acts in, so tests never touch real customers |
| Effect log | The world's record of every action taken, including rupee amounts |
| Oracle / contracts | Plain code rules that decide if a run broke a rule. No AI judge |
| Violation | A broken rule: forbidden action, missing precondition, wrong state change, or false success claim |
| Culprit | The change blamed for the regression, named only when the two-way test passes |
| Planted | A fault we put in on purpose so we know the right answer; always labelled "planted" |

## 2. How one check runs

You type one command:

```
ablate check --base main --cand pr-123 --world worlds/orders --scenarios scenarios.yaml --budget 5
```

It compares the current version (main) with the pull request (pr-123), in the orders world, on the listed scenarios, and refuses to spend more than the budget. Then six steps run in order.

1. **Diff: split the change into pieces.** The difference between base and candidate is broken into typed units: the model ID, the generation settings, each instruction block and each tool description. Some units depend on others (a "requires" link). A test version is only built if all its dependencies come with it. A unit that can't be undone on its own is marked **NOT\_REVERTIBLE**.
2. **Preflight: check before spending money.** Lint rules catch settings the new model will ignore or reject (sampling settings ignored on Gemini 3.6+, thinking levels the model rejects, deprecated or rerouted model IDs). One probe call goes to each model. It prints how many trials and requests the check needs, what it will cost, and the smallest effect it can detect. It refuses to run over the budget. It then freezes the plan and fingerprints it (a hash). A config the new model rejects gets **INCOMPATIBLE**.
3. **Fidelity gate: is the fake world trustworthy?** Recorded real tool calls are replayed into the fake world. Its states and answers must match exactly. If not, **WORLD\_UNFAITHFUL** and no blame is assigned.
4. **Reproduce: is it really worse?** Base and candidate run on the same scenarios side by side, plus an **A/A arm** (base against itself) as a control. The model version the API reports is logged on every call. Possible outcomes: **NOT\_REPRODUCED** (no regression found), **DRIFT** (the control moved or the model version changed mid-run), **INFRA\_SUSPECTED** (errors or timeouts differ between versions).
5. **Two-sided check: which change did it?** For each unit X, two extra versions run: candidate minus X, and base plus X. Section 3 explains the logic. Outcomes: **CONFIRMED**, **MULTIPLE\_CONTRIBUTORS**, **INTERACTION\_SUSPECTED**, **INCONCLUSIVE**.
6. **Output.** A PR comment saying **APPROVE**, **BLOCK** or **NEEDS MORE EVIDENCE**. It names a culprit only if CONFIRMED, and shows effect sizes with intervals, the smallest detectable effect, money spent, the price of the next step, and a pytest file.

### Every verdict

| Verdict | When it appears | What you should do |
| --- | --- | --- |
| INCOMPATIBLE | The new model rejects a setting in the config | Fix the setting before testing anything |
| NOT\_REVERTIBLE | A change can't be undone on its own | Test it together with what it depends on |
| WORLD\_UNFAITHFUL | The fake world doesn't match recorded reality | Fix the world; no blame is given |
| NOT\_REPRODUCED | No regression detected at the stated effect size | Usually safe, within the limits printed |
| DRIFT | The control arm moved, or the model version changed mid-run | Rerun; nothing is blamed |
| INFRA\_SUSPECTED | Error or timeout rates differ between versions | Fix infrastructure; results may be biased |
| CONFIRMED | The change passes both directions of the test | This change is the culprit |
| MULTIPLE\_CONTRIBUTORS | Adding X alone breaks things, but removing X doesn't fix them | Other changes also contribute |
| INTERACTION\_SUSPECTED | Removing X fixes things, but X alone doesn't break them | X breaks things only together with another change; the set is named, no single culprit |
| INCONCLUSIVE | Not enough evidence either way | Read the printed price: "N more paired scenarios ≈ $X" |
| SPEC\_DISAGREEMENT | The agent passes a looser intent check but fails the strict one | A human reviews the rule |

### Hard verdict rules

- No culprit without reproduction, a passed fidelity gate, both directions and a clean A/A arm.
- Every INCONCLUSIVE prints the price of deciding.
- Runs that fail from API errors or timeouts are marked INVALID, left out of the statistics, and counted per version.
- The word "LIKELY" never appears in PR output.

## 3. The two-way test and the statistics, in plain words

### The two-way test

A change X is CONFIRMED as the culprit only when all three are true:

1. The candidate is worse than base.
2. **Necessity:** taking X out of the candidate brings it back to about as good as base ("non-inferior", within 10 percentage points).
3. **Sufficiency:** putting only X into base makes base worse.

Why both directions? Checking only "remove it and see" can blame an innocent change, or miss a change that only does damage together with another. Requiring both directions is what keeps decoys from being blamed.

### What gets measured

- **Main outcome:** in each trial, did the agent break a contract? The four types: a forbidden action, a missing precondition, a wrong state change, a false success claim.
- **Also recorded, for description only:** how the final world state differs, the sequence of tool calls, tokens, cost and latency. Cost counts as an outcome too.
- **Unit of comparison:** the scenario. With 24 scenarios and 5 trials each per version, each version gets a violation rate per scenario.

### How "worse" is decided

- **Fixed in advance.** The number of scenarios, trials, margins and random seeds are fixed and fingerprinted before the first trial, with no peeking partway through. This stops anyone from stopping the test when it happens to look good.
- **Paired comparison.** Both versions face the same scenarios, so we compare them scenario by scenario. This removes the noise of some scenarios simply being harder.
- **Sign-flip test.** For each scenario we take the difference in violation rate. If the change did nothing, each difference would be equally likely to point either way. The test flips signs at random many times (every possible flip when there are 20 or fewer scenarios, otherwise 20,000 flips with a fixed seed) and asks how often chance alone gives a result as large as ours. Rarely means a real effect.
- **Interval.** The size of the effect comes with a range. The range is the wider of two methods: a Wilson/Newcombe bound on the pooled counts (adjusted for scenarios behaving in clusters), and a bootstrap that resamples scenarios 10,000 times. The bootstrap alone is never used, because when both versions show zero violations it would claim the range is exactly zero, which is false certainty.
- **Holm correction.** Testing several changes at once raises the chance of a lucky false alarm. Holm tightens the bar for each test so the overall false-alarm rate stays at 5%.
- **Non-inferiority.** "As good as base" means the upper end of the range for (candidate minus X) minus base is below 10 points. If the plan can't reach that, preflight says so, and the best possible verdict is INCONCLUSIVE.
- **Equivalence.** "No effect" is claimed only when the whole range sits inside plus or minus 10 points. Otherwise the answer is INCONCLUSIVE.
- **Always reported:** effect size, interval, smallest detectable effect for the run actually done, and cost.
- **Interactions:** only an additivity check (do the single-change effects add up to the total?). A dedicated 2×2 test is planned.

### The affected-scenario floor

The sign-flip test only learns from scenarios where the versions differ. With m such scenarios, the smallest possible p-value is 0.5 to the power m: 0.031 at 5, 0.016 at 6, 0.008 at 7. Holm's strictest bar here is about 0.017. So a culprit that affects fewer than 7 scenarios can never be CONFIRMED. The planted fault must affect at least 8 of the 24 scenarios, checked before the live run.

### Power: what the test can and can't see

A deleted precondition is a large effect and should be caught. A model upgrade that moves violations by 5–10 points will usually come back INCONCLUSIVE with 24 scenarios and 5 trials. That is the honest answer, and the demo shows its price. A rough check at 120 trials per version gives an upper bound of 3.6 points at a 0% base violation rate, 6.7 at 5% and 8.6 at 10%. So the 10-point margin is reachable if the base rate is about 10% or lower. Exact numbers come only from the simulator, run first. If it falls short, raise trials to 10 for the demo release, or widen the margin and say so.

### Budget

The demo release has three changes that can be undone (the prompt line, the decoy, the model upgrade); the thinking setting stops at preflight. That gives 9 versions: base, candidate, A/A, and two per change. 9 × 24 × 5 = 1,080 trials, about 3,240 requests at 3 calls per trial. The 10 scripted envelopes cost nothing. The 3 live envelopes (the demo release, a decoy-only and a null release) add about 2,160 trials, roughly 6,500 requests, run overnight.

## 4. Safety nets: how Ablate avoids fooling itself

| Safety net | What it guards against | How it works |
| --- | --- | --- |
| Preflight | Wasting money on a config that can't work, or a test too weak to decide | Lint rules, one probe call per model, printed cost and smallest detectable effect, a hard budget cap, a frozen and fingerprinted plan |
| Fidelity gate | A fake world that behaves differently from reality | Replays recorded tool calls; states and answers must match exactly, or no blame is given |
| A/A arm | Hidden drift in the model or environment | Runs base against itself. It is checked at an uncorrected 5% (stricter than the main tests). If it moves, DRIFT |
| Returned model version | Google swapping the model mid-run | Logged on every call. Any change gives DRIFT. At near-zero violation rates the A/A test rarely fires, so this is the real drift guard and is shown first on stage |
| INVALID runs | API errors and timeouts distorting results | Left out and counted per version. Different INVALID rates between versions give INFRA\_SUSPECTED |
| Fail-closed tools | The agent calling a tool the world doesn't have | Unknown tools are blocked |
| No AI judge | An AI grader being as random as the agent | Every rule is plain code over the world state, the effect log and structured output |
| Pre-registration | Moving the goalposts after seeing results | Sample sizes, margins and seeds go into the plan fingerprint before the first trial |
| Strict and loose checks | Rules that are wrong rather than the agent | Passing a looser intent check but failing the strict one gives SPEC\_DISAGREEMENT, for human review |

## 5. What you get out, and the two wow features

### Outputs

- **PR comment.** APPROVE, BLOCK or NEEDS MORE EVIDENCE, written into the pull request. It names a culprit only when CONFIRMED, and shows effect sizes with intervals, the smallest detectable effect, money spent and the price of the next step.
- **pytest file.** Two layers: a deterministic layer (fixed checks) and a statistical layer. In the demo it fails on the candidate and passes on base.
- **HTML report and JSON**, written to the run folder.

### Wow 1: the cost in rupees

The fake world logs the money involved in every action (refunds issued, shipped orders cancelled). For each kind of violation, Ablate multiplies the increase in its rate by the average amount and reports something like "about ₹X in wrongful refunds per 1,000 conversations", with a range. It is always labelled "under this scenario mix, not production traffic", and it only appears with BLOCK or CONFIRMED, never with INCONCLUSIVE. Why it matters: it turns statistics into money, which judges and buyers understand at once.

### Wow 2: the sealed-envelope challenge

The problem it solves: every demo is a story the team chose. This one is a test a judge chooses.

1. **Envelopes.** 13 broken releases with known answers. 10 run on a scripted fake agent: single culprit, decoy-only, 2 with no culprit, 1 interaction, 1 drift case, and 4 more single-culprit variants. 3 run on the live Gemini agent: one with a culprit, one decoy-only, one with no culprit.
2. **Sealing.** For each envelope we publish a fingerprint, SHA-256 of (answer + a 32-byte random secret), in a public, timestamped git commit before any test runs. The answers and secrets are kept outside the run folder, and the test runner never reads them.
3. **On stage.** A judge picks a number. Ablate shows the verdict it computed blind. A reveal script prints the answer and secret and recomputes the fingerprint next to the published one, so anyone can check it.
4. **Scoreboard.** All 13 results: correct culprits, abstentions and wrong culprits. It is shown whatever it says; one wrong culprit shown honestly beats a hidden one.

How to describe it: "pre-registered, blind, checkable here". Never "first".

### Gemini world drafter

Gemini reads recorded traces for one tool (get\_order) and drafts the code for that tool's fake world plus a state schema. A person reviews it, and the fidelity gate accepts or rejects it. The rule: Gemini drafts, the gate decides. In the demo this beat is pre-recorded and re-run live only if there's time. Long term, the drafter makes "world packs" for new domains faster.

## 6. Under the hood

Everything runs as one Python program that writes files into a run folder. There is no server.

| Component | What it does, plainly |
| --- | --- |
| Differ | Reads the agent config (YAML) and splits the change into typed units with dependency links |
| Preflight | Lint rules, probe calls, smallest-detectable-effect and cost estimate, budget cap, plan fingerprint |
| World | The fake shop: predictable, reset and fingerprinted before every trial, with an append-only effect log. Unknown tools are refused |
| World drafter | Gemini drafts one tool's world from recorded traces; a person reviews it; the fidelity gate decides |
| Fidelity checker | Replays recorded tool sequences into the world and reports coverage and mismatches |
| ADK adapter | A plugin that records every model request and response and the returned model version, and routes tool calls to the fake world while blocking unknown ones |
| Scripted adapter | A fake agent with planted behaviours, so the verdict engine and benchmark run with no API calls |
| Scheduler | Runs versions in a shuffled, interleaved order, slows down and retries with random waits when rate-limited, caches results on disk, and resumes after a crash without re-spending quota |
| Oracle | Code rules over state, effects and output. No AI judge |
| Stats and verdict engine | A pure function: plan plus results in, verdict out |
| Reporter | PR comment, HTML report, JSON and pytest files |

### Google technology

| Technology | Role | Priority |
| --- | --- | --- |
| Gemini 3.6 / 3.8 Flash | The agent being tested | Must |
| Gemini (world drafter) | Drafts one tool's world, checked by the fidelity gate | Should; demoed on one tool |
| ADK (Agent Development Kit) | The agent framework; plugin callbacks for recording and blocking tools | Must |
| Cloud Run Jobs | Runs test versions in parallel; one real run in the demo | Should |
| Cloud Storage | Stores run files and reports | Should, with Cloud Run |
| AI Studio | Keys, quota, prompt testing | Use |

Antigravity and Agent Skills are not shown unless built.

**Long-term moat (planned, say so):** reusable world packs per domain, built faster by the drafter, and a history of regressions across model versions.

## 7. The demo, the schedule and what to cut

### Demo setup

- **Agent:** one ADK agent with 4–6 named instruction blocks and 3 tools (get\_order, verify\_identity, and cancel\_order or issue\_refund).
- **World:** a predictable orders and refunds shop. get\_order is the tool the Gemini drafter rebuilds; the others are hand-written.
- **Scenarios:** 24, including tricky ones ("I was already verified", cancelling a shipped order).
- **Planted release, four changes:** a deleted precondition line in the prompt (the true culprit), a harmless rewording of a tool description (the decoy), a model upgrade from 3.6 to 3.8 Flash, and a thinking-level setting that preflight rejects. Labelled "planted" from the first frame.

### Three-minute script

| Time | What happens |
| --- | --- |
| 0:00 | "AI agents now move money, and when a release breaks one, nobody can say which change did it." One PR, four changes; the agent now cancels shipped orders. Then Google's changelog showing the 8 October reroute |
| 0:20 | The novelty line, crediting Google's flake-aware culprit finding |
| 0:35 | ablate check: preflight rejects the thinking setting; prints trials, cost, smallest detectable effect |
| 0:55 | Pre-recorded: Gemini drafts the get\_order world; the fidelity gate checks it |
| 1:15 | The regression reproduces; model version logged; A/A clean |
| 1:35 | Two-sided check: prompt line CONFIRMED, decoy cleared, model upgrade INCONCLUSIVE with its price |
| 2:00 | PR comment says BLOCK with the ₹ cost; pytest fails on candidate, passes on base |
| 2:20 | A judge picks an envelope; blind verdict; the fingerprint is recomputed live |
| 2:40 | Scoreboard for all 13 envelopes, interview quotes, the ask. Done by 2:55 |

**Backups:** a cached run, a scripted-agent mode with no API, and a recorded video. A rate-limit error on stage is shown as INVALID handling, not hidden.

### Schedule

| When | Work |
| --- | --- |
| Before the event (if allowed) | Verdict engine, statistics, scripted adapter, envelope generator, simulator; judging rubric; rename |
| Day 1, first 30 min | Generate 13 envelopes; publish fingerprints in a public timestamped commit; move answers out of the run folder |
| Day 1, block 1 | Simulator first; verdict engine and two-sided check on the scripted agent; differ and lint; run the 10 scripted envelopes blind |
| Day 1, block 2 | World with money amounts, oracle, ADK agent on both models; probe calls. One teammate also runs 60–90 minutes of interviews |
| Day 1, block 3 | Scheduler, cache, A/A arm; hand-check the get\_order world; start the 3 live envelopes overnight |
| Day 2, block 1 | Fidelity gate; live results into the verdict engine; rupee cost |
| Day 2, block 2 | Reveal script; world drafter (pre-recorded); PR comment; pytest |
| Day 2, block 3 | One Cloud Run Job, report to Cloud Storage |
| Day 2, last 3–4 h | Freeze, precompute, backup video, rubric slides, interview quotes, rehearse three times under 2:50 |

**Cut order if late:** Cloud Run, then the world drafter, then live envelopes down to one (the demo release), then the rupee figure, then scripted envelopes down to 5, then the fidelity gate on one tool only. **Never cut:** preflight, the A/A arm, the two-sided check, abstention, or the fingerprint commitment. Wow features never delay the core verdict engine.

## 8. Business, claims, prior work and the checklist

### Business (all hypotheses, not facts)

| Tier | Includes | Price to test |
| --- | --- | --- |
| Free | Open-source command-line tool and GitHub Action; customers use their own Gemini key | $0 |
| Team (planned) | Hosted GitHub check, world packs, record-to-world, deprecation watcher, history, signed reports | \~$199 or \~₹4,999 a month per agent repo |
| Migration sprint (planned) | One-off check before a model shutdown | \~$499 or \~₹19,999 |

The customer's own key pays for the AI calls, so margins don't depend on model prices.

**Price test on Day 1.** Interview 5–10 teams; half hear the dollar price, half the rupee price. Ask three questions and write the answers down word for word:

1. Has a prompt, config or model change ever broken your agent? What did it cost you?
2. How did you find which change did it, and how long did that take?
3. Would you pay \[price\] a month for a check that names the culprit only when it holds both ways?

The counts and two direct quotes go on the customer slide. No market-size (TAM) claims. If asked "who in India pays this?": "That's what the two-price test is for. Here's what \[N\] teams told us yesterday."

### Novelty, honestly

**Novelty line:** "Noisy bisection exists; Google's own flake-aware culprit finding does it for commits. We extend it to agent releases: revert and apply-alone tests, abstention on interactions, and an error rate you can check, sealed before the run."

**Prior work to name:** Google's Flake-Aware Culprit Finding (ICST 2023: noisy bisection over commits, single culprit, can return "none", no revert or apply-alone test); git bayesect (Bayesian git bisect); stochastic delta debugging for cyber-physical systems (Valle, Arrieta and Ali, 2026: repeated runs with Fisher tests, no abstention); Delta Debugging for LLM-integrated Systems (AWS, 2026: threshold over repeated runs, within one input, not between releases); Causal Agent Replay (CMU, 2026: blames steps within one run); Groce and Holmes (2020: delta debugging for random faults).

**Competitors to name:** Shadow (PR diagnosis, fix verification), agent-attrib (factorial attribution, no execution), ablate-cli (section ablation), EvalShift (model migration), agentrial and AgentAssay (statistical gates), LangSmith Engine (fix validation), Raindrop (production replay), ADK eval (AI-simulated tools). The comparison slide has four columns: requires both directions, publishes a false-attribution rate, actually runs the agent, abstains with a price. Check each claim before the slide goes in.

### What we say and never say

**Say:** "we couldn't find a tool that requires evidence in both directions and publishes its false-attribution rate"; "planted-fault benchmark"; "shown both ways"; "no regression detected at the stated effect size"; "Gemini drafts, the fidelity gate decides"; "pre-registered, blind, checkable here"; "we extend Google's flake-aware culprit finding to agent releases".

**Never say:** prove, proof, proves, proven; first; only; certified safe; root cause (below CONFIRMED); deterministic agent; VERIFIED simulation; \~58% fewer runs; four Flash changes in 2026 (unless verified tonight on Google's deprecations page); works with any framework; detects interactions (without the power to). Don't use another company's incident as the hook; Anthropic's postmortem can go on the problem slide or stay ready for Q&A once its date and details are checked.

### Not in this build

Factorial interaction search, sequential looks, auto-fix, fragility map, mechanism localisation, multi-agent, other framework adapters, dashboards, AI judges, investigation memory, and generating whole worlds (one tool only).

### Before the event: checklist

- [ ] **Rename.** "ablate" clashes with ablate-cli, an unrelated PyPI package and an abliteration toolkit. Culprit is reported taken and Converse is too generic. Check PyPI, GitHub and the .dev domain.
- [ ] **Rules.** Ask the organisers what pre-work is allowed.
- [ ] **Quota and billing.** Check limits in AI Studio; enable billing (Tier 1); develop on Flash-Lite.
- [ ] **Models.** Confirm 3.6 and 3.8 Flash answer and return their version; confirm the 3.8 thinking-level rejection on Google's official page, or label that rule "planted".
- [ ] **Pre-build if allowed:** verdict engine, stats, scripted adapter, benchmark generator, simulator.
- [ ] **Numbers.** Use only the simulator's output; remove "VERIFIED", "\~58%" and "four Flash changes" from the deck.
- [ ] **Interviews.** List 10 teams to approach on Day 1.
- [ ] **Rubric.** Get the official judging criteria; one slide per criterion in their words.
- [ ] **Envelopes.** Generate them and commit the fingerprints, or do it in the first 30 minutes of Day 1 before any trial.
- [ ] **One-line problem for non-engineers:** "AI agents now move money, and when a release breaks one, nobody can say which change did it."
