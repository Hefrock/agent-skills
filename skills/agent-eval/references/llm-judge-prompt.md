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
| 2026-10-26 | 17 | 0.203 | At threshold (0.203 vs 0.2; blind first-pass, n=17, 1 data-entry typo excluded, 1 rater, review-and-plan v3 judge A). Gap is concentrated in control-arm length-fit (4 items, human 0.8-0.9 vs judge 0.30-0.50); after 5 judgment revisions the mean delta on the same 17 was 0.132 (corrected 2026-10-26 from 0.131, which was the 18-item figure including the typo fix). Data: `skills/review-and-plan/examples/v3/human/`. Next: add anchors to the length-fit rubric, not a judge-prompt rewrite. Pending: 3 rescores, Gemini judge. |
| 2026-10-26 | 8 | 0.131 | Anchors HOLD on a fresh held-out set (pre-registered: hold if mean <= 0.15 and no item > 0.4; max item 0.40 exactly, thin margin). Length-fit only, human first-pass, judge A (Opus), judge prompt unchanged; f1 control the outlier (human 0.6 vs judge 0.2). n=8, one rater. No judge-prompt change. |

## Known biases to guard against

- **Position bias** (pairwise comparisons): the judge tends to favor whichever option is presented first. Run both orderings and average, or randomize order across the eval set.
- **Verbosity bias**: judges (and humans) tend to rate longer answers as more thorough even when they're not more correct. Don't let "comprehensive-sounding" substitute for "accurate" in the rubric.
- **Leniency drift**: judge behavior can drift across a long run. For eval sets over ~30 cases, periodically re-grade a few earlier cases to check consistency.
- **Self-grading**: never use the same model/prompt as both the system under test and the judge without flagging it as a caveat — it's a known source of inflated scores.
