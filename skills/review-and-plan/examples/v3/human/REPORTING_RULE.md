# Reporting rule for this run (written 2026-10-26, before logging calibration)

Decided BEFORE recording the calibration figure, so the choice cannot follow the result.

1. **Headline calibration figure = blind first-pass scores** (`first.jsonl`), not the revised ones.
2. **Known data-entry errors are excluded; judgment revisions are not.**
   - Excluded: `skill:q2` (typed 0.0 by mistake; user confirmed 2026-10-26 it was a typing error).
   - Not excluded: the 5 length-fit revisions (control:q3, q4, q8, r3; skill:r3). Those are
     reconsiderations of a judgment, so they stay out of the headline and are reported separately
     as "after reconsideration".
3. **Exclude, do not impute.** Replacing the 0.0 with 0.9 would be a judgment about what was meant.
4. `first.jsonl` and `final.jsonl` are never edited. `first_excl.jsonl` is a derived copy
   (first.jsonl minus the excluded ids) and is regenerated, not hand-edited.
5. Report as "at threshold", not pass/fail, if the figure lands within 0.01 of 0.2: the threshold
   test is strict (>), and the verdict flips between exclusion (0.203, n=17) and imputation
   (0.197, n=18).
6. Pending: rescore control:q1, control:q6, skill:q5 before any Gemini scores are viewed. They do
   not affect the blind figure.

Known limits: one rater, n=17; length-fit blinding is weak (reply length reveals the arm).
