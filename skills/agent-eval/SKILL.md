---
name: agent-eval
description: Designs and runs evaluations for LLM or agent outputs — builds rubrics, sets up LLM-as-judge scoring, creates regression test sets, and reports pass rates with concrete failure examples. Use this skill whenever the user wants to evaluate, test, grade, or score an agent's or LLM's outputs; asks "how do I know if this is working," "is this any good," "set up an eval," or "did this prompt change make things worse"; needs a rubric for judging quality; wants to compare two prompts, models, or outputs; or wants to catch regressions before shipping a change. Also trigger when reviewing agent trajectories specifically — did the agent pick the right tool, the right arguments, the right sequence — not just the final output.
---

# Agent Eval

Turns "does this actually work" into a repeatable, evidence-based answer instead of a gut feeling.

## How this works

1. **Figure out what "good" means first.** Before writing any eval, get a concrete definition of success from the user, or infer it from context and confirm it back to them. What does a correct/good output look like? What does a clearly bad one look like? Is there a reference answer, or is this judgment-based?

2. **Pick the eval type** — don't default to one without considering the fit:
   - **Reference-based**: there's a known correct answer (exact or fuzzy/semantic match). Cheapest and most reliable, but only works when "correct" is well-defined.
   - **Rubric-based (LLM-as-judge)**: quality is graded against explicit criteria (e.g. "factually grounded," "follows the required format," "appropriately concise"). Use `references/llm-judge-prompt.md` as the starting template — don't write a judge prompt from scratch each time.
   - **Pairwise comparison**: judging which of two outputs is better, not scoring each in isolation. More reliable than absolute scoring for subjective quality, but watch for position bias — always run both orderings and average. Use `scripts/run_pairwise.py` for this — it runs both orderings itself and reconciles them (see `references/pairwise-comparison.md`), rather than leaving "remember to run it twice" as a step to repeat by hand each time.
   - **Programmatic/structural**: format compliance, schema validation, code that actually executes, tool calls matching an expected sequence. Cheapest and most deterministic when applicable — prefer this over LLM-as-judge whenever the criterion is mechanically checkable.
   - **Trajectory evaluation (agents specifically)**: don't just grade the final output — check whether the agent picked the right tool, passed the right arguments, and used a reasonable sequence of steps. A correct final answer reached via a broken process is still a signal of an unreliable agent. Use `references/trajectory-eval.md` for the case shape, the judge-rubric addendum, and the flattening convention — don't invent one from scratch each time.
   - **Adversarial**: the correct outcome is refusal, hedging, or graceful degradation — the question is "did the agent handle this appropriately?" not "was the answer correct." Never blend adversarial scores with correctness scores; run them as a separate eval set, in their own results file. `category` should be the specific failure mode under test (`prompt-injection`, `jailbreak`, `over-refusal`, ...), not a single flat `adversarial` bucket — that's what lets `score_eval.py`'s per-category breakdown show pass rate *per attack type*, not just one undifferentiated adversarial score. See `skills/agent-redteam/examples/adversarial_results.jsonl` for the real convention, and `skills/agent-redteam/` for a dedicated case-generation skill.
   - **Multi-turn**: not a distinct scoring type so much as a case-shape variant of the ones above — use when the conversation *leading up to* a response is itself part of what makes it right or wrong (a frame established earlier, an incremental escalation), not just the final message in isolation. Use `references/multi-turn-eval.md` for the case shape (a `turns` field, rendered into the judge prompt as `{transcript}` by `scripts/run_judge.py`) — don't collapse a real conversation into one flattened string.

3. **Build a small, reusable eval set, not a one-off.** Even 10-20 representative cases beats eyeballing a handful of outputs. Include a mix of: clear-pass cases, known-hard edge cases, and at least a few cases the current system is expected to fail — a sanity check that the eval can actually detect failure, not just confirm success.

4. **Score it.** For rubric/LLM-as-judge evals, use the judge prompt template and request structured JSON output (a score plus a one-line rationale per criterion) — never a vibe-based pass/fail. For programmatic evals, write the check directly.

   For rubric/LLM-as-judge evals specifically, **use `scripts/run_judge.py`** rather than calling the judge and flattening its response by hand each time — it fills the template, calls the judge, parses the structured JSON, and writes already-flattened rows in the exact schema `score_eval.py` reads:
   ```bash
   export ANTHROPIC_API_KEY=...
   python scripts/run_judge.py cases.jsonl --template references/llm-judge-prompt.md --out results.jsonl --category accuracy

   # Or judge with Gemini instead of Claude — same output, same schema:
   export GEMINI_API_KEY=...
   python scripts/run_judge.py cases.jsonl --template references/llm-judge-prompt.md --out results.jsonl --provider gemini
   ```
   `--provider` (default: `anthropic`; `gemini` also supported) picks which judge API gets called — `scripts/run_pairwise.py` takes the same flag. `--model` defaults to the chosen provider's own default model if not given. See `call_judge()`'s own docstring in `run_judge.py` for one honest caveat: Gemini's token-usage field (which `cost_usd` depends on) isn't independently live-verified in this repo the way Anthropic's is, so treat `cost_usd` from a Gemini-judged run as lower-confidence than from an Anthropic one.

   Cases are graded concurrently (`--concurrency`, default 5) rather than one judge call at a time — bounded, not unlimited, since this is a real (possibly rate-limited) external API. Raise it for a large case set against a generous rate limit; lower it if you start seeing failed cases from 429s. `scripts/run_pairwise.py` applies the same concurrency to its own two calls per case, unconditionally (position-bias reconciliation needs both orderings regardless of case-set size, so there's no separate flag for it there).

   `cases.jsonl` is one row per case (`id`, plus whatever fields the template's `{placeholder}` tokens need — typically `input`/`output`, or `input`/`trajectory`/`final_output` for a trajectory case). It's generic over criterion names, so it handles both the plain rubric shape and `references/trajectory-eval.md`'s shape without any mode flag. A per-case failure (judge call error, unparseable response, or a malformed case that fails template-filling) is reported to stderr and that case is skipped — never given a fabricated score. `cost_usd` is only included when both `--input-price-per-mtok`/`--output-price-per-mtok` are given, computed from the API's own real token counts.

   Before any judge call, both scripts run a **preflight check**: every case is verified against the template's own `{placeholder}` tokens, and against each other for duplicate `id`s. A systemic mistake — case file built against the wrong template, a field typo'd across the whole set — is reported as one upfront summary and aborts, instead of surfacing as a string of individually-quiet per-case skips after budget has already been spent on whichever cases happened to still pass. Pass `--skip-invalid` to grade just the valid subset instead of aborting.

   The judge prompt returns one nested object per case (per-criterion scores plus an `overall_score`) — that's a different shape from what `scripts/score_eval.py` reads. If scoring by some other means than `run_judge.py` (a different judge model/provider, a notebook), flatten each case before saving, to this schema (one JSON object per line):
   ```json
   {"id": "case_001", "score": 0.83, "category": "accuracy", "rationale": "...", "cost_usd": 0.003, "latency_ms": 1240}
   ```
   - `id` — a stable identifier for the eval case, assigned when you build the eval set (not produced by the judge).
   - `score` — the judge's `overall_score` for LLM-as-judge evals, or 1.0/0.0 for a programmatic pass/fail check.
   - `category` — the criterion group or failure mode you're tracking (e.g. `accuracy`, `format`, `tool_use`), assigned by you, not read from the judge's per-criterion keys — this is what `score_eval.py` breaks results down by.
   - `rationale` — a one-line reason for the score. For a multi-criterion judge response, use the rationale from the lowest-scoring criterion, since that's the one explaining the failure.
   - `cost_usd` (optional) — API cost for generating the output under test.
   - `latency_ms` (optional) — wall-clock time to generate the output, in milliseconds.

   Save the flattened lines to a JSON/JSONL file, not just a summary — the failure examples are what make this actionable.

5. **Calibrate the judge periodically.** Every 25-50 judge calls (or whenever you revise the judge prompt), hand-score 5-10 cases yourself and compare to the judge's scores. Use `scripts/calibrate_judge.py` rather than eyeballing the delta — it computes the mean delta, flags whether it exceeds the threshold (default 0.2), and can log the result for you:
   ```bash
   python scripts/calibrate_judge.py judge_results.jsonl human_scores.jsonl --update-log references/llm-judge-prompt.md
   ```
   `human_scores.jsonl` only needs the 5-10 IDs you actually hand-scored (same JSONL schema as any `score_eval.py` results file) — it compares whichever IDs appear in both files. See [`examples/README.md`](./examples/README.md)'s calibration example for a worked case where this catches a judge fooled by confident, verbose wrong answers. `--update-log` appends a row to `references/llm-judge-prompt.md`'s calibration table automatically, replacing the "not yet calibrated" placeholder on the first real run — don't edit that table by hand.

6. **Aggregate and report using `scripts/score_eval.py`.** Don't manually tally pass rates — run the script against the results file:
   ```bash
   python scripts/score_eval.py results.jsonl
   python scripts/score_eval.py results.jsonl --baseline previous_results.jsonl
   python scripts/score_eval.py results.jsonl --threshold 0.8
   python scripts/score_eval.py results.jsonl --json-out summary.json
   ```
   It computes pass rate, mean score (overall and per-category), surfaces the lowest-scoring cases for review, and reports mean cost and latency per category when those fields are present. With `--baseline`, it flags regressions — cases that passed before and fail now. `--threshold` sets the score-≥-this-counts-as-a-pass cutoff (default `0.7`) used everywhere in the report and every gate below — set it once per eval set rather than eyeballing which scores "feel like" a pass. `--json-out` writes the same summary `summarize()` computes (including `by_category` and any regressions found) as JSON, for a dashboard or a second script to consume instead of scraping the printed report.

   Category grouping is case/whitespace-insensitive — `accuracy`, `Accuracy`, and ` ACCURACY ` all land in the same row rather than silently fragmenting one category's stats across several rows, a real risk since `category` is typed per-run, not drawn from a fixed enum anywhere in this pipeline. A category name that's merely *similar* to another (a likely typo, not an exact match after normalizing) is never auto-merged — that would risk silently combining two genuinely different categories — but `score_eval.py` prints a warning naming the suspect pair so you can fix the eval set's spelling.

   **As a CI gate**, add `--fail-under` and/or `--fail-on-regression` so the script exits non-zero (failing the build) when quality drops:
   ```bash
   python scripts/score_eval.py results.jsonl --fail-under 0.85
   python scripts/score_eval.py results.jsonl --baseline eval_set_v2.jsonl --fail-on-regression
   ```
   This is what makes an eval a gate rather than a report — the same run that scores your change also blocks it if it regressed. See [`examples/`](./examples/) for a worked before/after where a change looks like a win on cost, latency, and format but the gate catches three silent accuracy regressions.

   The reverse gap has a gate too: a change that holds accuracy perfectly steady while cost or latency quietly balloons was previously invisible to every flag above. `--fail-on-cost-regression`/`--fail-on-latency-regression` (needs `--baseline`; tolerance via `--cost-regression-tolerance`/`--latency-regression-tolerance`, default 20%) catch that directly; `--fail-if-mean-cost-above`/`--fail-if-mean-latency-above` set an absolute ceiling with no baseline needed:
   ```bash
   python scripts/score_eval.py results.jsonl --baseline previous_results.jsonl --fail-on-cost-regression --fail-on-latency-regression
   python scripts/score_eval.py results.jsonl --fail-if-mean-cost-above 0.01 --fail-if-mean-latency-above 2000
   ```
   See [`examples/README.md`](./examples/README.md)'s cost/latency regression example for a worked case where accuracy is unchanged (same 90% pass rate) but cost/latency both roughly triple, and the gate catches it.

7. **Be honest about sample size — and don't just say so, measure it.** With under ~20 cases, a 2-3 case swing can look like a large percentage shift. `--ci` reports a 95% bootstrap confidence interval on pass rate and mean score, and — with `--baseline` — a paired significance test on both the pass-rate delta and the raw mean-score delta between the two runs:
   ```bash
   python scripts/score_eval.py results.jsonl --ci
   python scripts/score_eval.py results.jsonl --baseline previous_results.jsonl --ci
   ```
   `--fail-on-significant-regression` gates on those tests instead of `--fail-on-regression`'s bare threshold-crossing count — it only fails the build when a 95% CI excludes zero, not whenever any single case wobbles past `--threshold` (real LLM-judge noise, not necessarily a real regression). Checking both pass-rate and mean-score matters because binarizing to pass/fail throws away magnitude — a case that drops from 0.9 to 0.4 counts exactly the same as one that drops from 0.71 to 0.69 once both are "fail," so a real, consistent quality drop that never crosses `--threshold` is invisible to the pass-rate signal but not the raw-score one. Still say the plain-language version too ("3/10 passed (30%) — too small a sample to call this a real regression yet") — the CI number backs up the sentence, it doesn't replace it.

   Both of those are still *run-wide* aggregates, which have their own blind spot: a real regression concentrated in one category can get diluted away against unaffected cases from every other category. `--ci` (with `--baseline`) also runs both paired tests once **per category** — skipping any category under 3 matched cases, too few for a bootstrap to say anything meaningful, and skipping a *sole* category spanning every matched case (no `category` field anywhere, or every row sharing one label) since that's testing the identical data the run-wide diffs already cover, not a second opinion — and `--fail-on-significant-regression` gates on those too.

   Running that many tests together has its own cost: at a plain alpha=0.05 each, the chance that at least one comes back "significant" purely by chance climbs well past 5% as more tests are added — confirmed empirically at ~27.5% on genuinely stable data across 4 categories, not a theoretical worry. Every verdict — run-wide and per-category — is Bonferroni-corrected for however many tests actually ran together, bringing that back to ~6.5%, close to the nominal target. See [`bootstrap_stats.py`](./scripts/bootstrap_stats.py) and `apply_multiple_comparisons_correction()`'s docstring in `score_eval.py` for the confirmed numbers and why Bonferroni (simple, auditable, correct under any dependence between tests) was chosen over the less-conservative Benjamini-Hochberg procedure.

   See [`examples/README.md`](./examples/README.md)'s statistical-confidence section for a real worked case, including an honest consequence: the same 8-case category regression that used to trip the gate before correction existed (p=0.043, uncorrected) no longer does once 10 simultaneous tests are properly accounted for (corrected alpha=0.005) — the correction working as intended, not a capability regression. A second, stronger worked example there shows the gate still firing correctly once the per-category evidence is actually strong enough to survive correction. Three gates now cover three different questions — `--fail-on-regression` (any named case flipped), `--fail-on-significant-regression`'s run-wide half (is the topline number moving for a real reason), and its per-category half (is a *properly corrected* regression hiding inside one slice) — meant to be used **together**, since none of them subsumes the others.

8. **When re-evaluating after a change** (new prompt, new model, new tool definition), always run the *same* eval set as before and diff against the saved baseline. That's what catches regressions — a fresh set of cases each time doesn't.

   When the case set itself changes (new cases added, old ones removed), save it as a new versioned file (`eval_set_v2.jsonl`, `eval_set_v3.jsonl`) rather than overwriting. Before reporting a regression, confirm both runs used the identical case set — same filename, same line count, same case IDs. A score drop may just be a case-set change, not a model regression.

## Output discipline

- Never let an LLM-as-judge grade its own output unflagged — if the system under test and the judge share a model or prompt, say so as a caveat. Self-grading is a known source of inflated scores.
- Don't report an aggregate score without 2-3 concrete failure examples alongside it. A percentage with no examples isn't actionable.
- If the user hasn't defined success criteria and won't, don't silently invent a rubric and present results as objective — flag that the rubric is your best guess at their intent.
- Keep judge prompts in `references/llm-judge-prompt.md` versioned alongside the eval set, not rewritten ad hoc each run. Consistency between runs is what makes before/after comparisons valid.
- Report adversarial pass rates separately from correctness pass rates — never blend the two into a single aggregate.
