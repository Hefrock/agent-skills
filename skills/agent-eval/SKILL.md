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
   - **Pairwise comparison**: judging which of two outputs is better, not scoring each in isolation. More reliable than absolute scoring for subjective quality, but watch for position bias — always run both orderings and average.
   - **Programmatic/structural**: format compliance, schema validation, code that actually executes, tool calls matching an expected sequence. Cheapest and most deterministic when applicable — prefer this over LLM-as-judge whenever the criterion is mechanically checkable.
   - **Trajectory evaluation (agents specifically)**: don't just grade the final output — check whether the agent picked the right tool, passed the right arguments, and used a reasonable sequence of steps. A correct final answer reached via a broken process is still a signal of an unreliable agent.
   - **Adversarial**: the correct outcome is refusal, hedging, or graceful degradation — the question is "did the agent handle this appropriately?" not "was the answer correct." Never blend adversarial scores with correctness scores; run them as a separate eval set and use `category: adversarial` in JSONL output. See `skills/agent-redteam/` for a dedicated case-generation skill.

3. **Build a small, reusable eval set, not a one-off.** Even 10-20 representative cases beats eyeballing a handful of outputs. Include a mix of: clear-pass cases, known-hard edge cases, and at least a few cases the current system is expected to fail — a sanity check that the eval can actually detect failure, not just confirm success.

4. **Score it.** For rubric/LLM-as-judge evals, use the judge prompt template and request structured JSON output (a score plus a one-line rationale per criterion) — never a vibe-based pass/fail. For programmatic evals, write the check directly.

   The judge prompt returns one nested object per case (per-criterion scores plus an `overall_score`) — that's a different shape from what `scripts/score_eval.py` reads. Flatten each case before saving, to this schema (one JSON object per line):
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

5. **Calibrate the judge periodically.** Every 25-50 judge calls (or whenever you revise the judge prompt), hand-score 5-10 cases yourself and compare to the judge's scores. If the mean delta exceeds 0.2, revise the judge prompt. Record the last calibration date in `references/llm-judge-prompt.md`.

6. **Aggregate and report using `scripts/score_eval.py`.** Don't manually tally pass rates — run the script against the results file:
   ```bash
   python scripts/score_eval.py results.jsonl
   python scripts/score_eval.py results.jsonl --baseline previous_results.jsonl
   ```
   It computes pass rate, mean score (overall and per-category), surfaces the lowest-scoring cases for review, and reports mean cost and latency per category when those fields are present. With `--baseline`, it flags regressions — cases that passed before and fail now.

   **As a CI gate**, add `--fail-under` and/or `--fail-on-regression` so the script exits non-zero (failing the build) when quality drops:
   ```bash
   python scripts/score_eval.py results.jsonl --fail-under 0.85
   python scripts/score_eval.py results.jsonl --baseline eval_set_v2.jsonl --fail-on-regression
   ```
   This is what makes an eval a gate rather than a report — the same run that scores your change also blocks it if it regressed.

7. **Be honest about sample size.** With under ~20 cases, a 2-3 case swing can look like a large percentage shift. Say so explicitly: "3/10 passed (30%) — too small a sample to call this a real regression yet" rather than presenting a precise-looking percentage as statistically solid.

8. **When re-evaluating after a change** (new prompt, new model, new tool definition), always run the *same* eval set as before and diff against the saved baseline. That's what catches regressions — a fresh set of cases each time doesn't.

   When the case set itself changes (new cases added, old ones removed), save it as a new versioned file (`eval_set_v2.jsonl`, `eval_set_v3.jsonl`) rather than overwriting. Before reporting a regression, confirm both runs used the identical case set — same filename, same line count, same case IDs. A score drop may just be a case-set change, not a model regression.

## Output discipline

- Never let an LLM-as-judge grade its own output unflagged — if the system under test and the judge share a model or prompt, say so as a caveat. Self-grading is a known source of inflated scores.
- Don't report an aggregate score without 2-3 concrete failure examples alongside it. A percentage with no examples isn't actionable.
- If the user hasn't defined success criteria and won't, don't silently invent a rubric and present results as objective — flag that the rubric is your best guess at their intent.
- Keep judge prompts in `references/llm-judge-prompt.md` versioned alongside the eval set, not rewritten ad hoc each run. Consistency between runs is what makes before/after comparisons valid.
- Report adversarial pass rates separately from correctness pass rates — never blend the two into a single aggregate.
