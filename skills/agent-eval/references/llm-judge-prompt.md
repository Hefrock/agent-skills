# LLM-as-Judge Prompt Template

Use this as a starting point for rubric-based evals. Fill in the bracketed sections for the specific task being graded. Keep the structured-output instruction — don't let the judge respond in free text, or aggregation in `score_eval.py` breaks.

---

```
You are an evaluator grading a single AI response against a defined rubric.
You are not the system being evaluated — judge only what is given to you below.

## Task being evaluated
[Describe the task the system was asked to do]

## Input
{input}

## Output to grade
{output}

## Rubric
Grade the output against each criterion below on a 0.0-1.0 scale:

- [Criterion 1, e.g. "Factual accuracy"]: [what counts as a 1.0 vs a 0.0]
- [Criterion 2, e.g. "Format compliance"]: [...]
- [Criterion 3, e.g. "Appropriate length/concision"]: [...]

## Instructions
- Grade strictly against the rubric above — do not reward generally pleasant
  writing that doesn't satisfy a criterion.
- If the output is empty, refuses, or is clearly broken, score 0.0 on every
  criterion rather than leaving it blank.
- Give a one-sentence rationale per criterion — this is what makes failures
  actionable later.
- Respond with valid JSON only, no other text:

{
  "criterion_1_name": {"score": 0.0, "rationale": "..."},
  "criterion_2_name": {"score": 0.0, "rationale": "..."},
  "overall_score": 0.0
}
```

## Calibration log

<!-- Update after each spot-check (see agent-eval step 5): hand-score 5-10 cases, compare to judge, note mean delta -->
| Date | Cases checked | Mean delta vs human | Action taken |
|---|---|---|---|
| — | — | — | not yet calibrated |

## Known biases to guard against

- **Position bias** (pairwise comparisons): the judge tends to favor whichever option is presented first. Run both orderings and average, or randomize order across the eval set.
- **Verbosity bias**: judges (and humans) tend to rate longer answers as more thorough even when they're not more correct. Don't let "comprehensive-sounding" substitute for "accurate" in the rubric.
- **Leniency drift**: judge behavior can drift across a long run. For eval sets over ~30 cases, periodically re-grade a few earlier cases to check consistency.
- **Self-grading**: never use the same model/prompt as both the system under test and the judge without flagging it as a caveat — it's a known source of inflated scores.
