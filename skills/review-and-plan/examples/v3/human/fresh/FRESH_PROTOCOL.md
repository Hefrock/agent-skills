# Fresh length-fit validation: pre-registration (written 2026-10-26, BEFORE any reply exists)

Purpose: test whether the human length-fit anchors (rubric.txt, stricter reading) agree with the UNCHANGED
judges on cases the anchors were not built from. The anchors were drafted from q3/q4/q8/r3, so those
four cannot validate them.

Cases: fresh_cases.json (f1-f4). f1-f3 expect a short answer; f4 expects depth (tests the "too thin /
too long" side so the anchors are not just "shorter is better"). All four were written without
looking at any reply to them. Do not edit the file after the freeze hash below; if a case needs fixing,
add f5 and say why.

Freeze: sha256 of fresh_cases.json is recorded in FRESH_FREEZE.txt.

## Steps (order matters)
1. Generate one reply per case per arm (skill = current review-and-plan SKILL.md; control = no skill),
   same runner and fresh agents as v3. 8 replies total. Check for cut-off replies by word count.
2. Human blind-scores all 8 with human_score.py (prepare -> score) using rubric.txt, BEFORE any judge
   score is viewed. Report the first-pass score; revisions only via rescore, reported separately.
3. Judges A and B (prompts unchanged) score the 8 in one arm-blind shuffled batch.
4. Compare, length-fit only.

## Decision rules (fixed now)
- Anchors "hold" if mean |human - judge A| <= 0.15 on the 8 first-pass items AND no single item differs
  by more than 0.4. Judge A is the comparison because the earlier calibration used it.
- Anchors "fail" if mean |delta| > 0.2, or two or more items differ by > 0.4 in the same direction.
- In between (0.15-0.2): report as inconclusive; do not rewrite the anchors again from these 8 items.
- n=8 and one rater: a "hold" is evidence the anchors are stable, not proof the judge is calibrated.
- Known data-entry errors are excluded under the same rule as REPORTING_RULE.md; judgment revisions are not.

## What a result would mean
- Hold: the disagreement was rubric ambiguity; no judge-prompt change is needed for length-fit.
- Fail on the control arm again: the judge and the rater read "proportionate" differently; consider
  adding the same anchors to the judge prompt (a v4 re-judge of both arms in one blind batch).
- Fail on f4 specifically: anchors are over-penalizing depth; fix the "expects depth" anchor.

Limits: the human rater sees reply length, so length-fit blinding is weak; all replies come from the
same model family as the judges.
