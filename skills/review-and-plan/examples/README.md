# review-and-plan eval (2026-10-17)

A first agent-eval pass on this skill. Treat it as a smoke test, not a benchmark: 10 cases, self-written, all-Claude judges. Raw material is kept so a later, harder run can be diffed against it.

## Method
- 10 cases (`cases.json`), each run twice by a fresh no-tools Sonnet subagent: once with `SKILL.md` loaded (`skill`), once without (`control`). Outputs are in `outputs/`.
- Cases: 3 should-Review (r1-r3 incl. one needed-twice check), 2 Quick (q1-q2), 3 should-skip (s1-s3), 2 traps (t1 outward action, t2 stale remote state).
- Two judges (Opus = A, Sonnet = B) graded all 20 replies blind to arm and case id (`judge_map.json` is the un-blinding key). Judges saw the per-case reference notes in `cases.json`.
- Each case is scored on one primary criterion matching its `category` (mode-selection, grounding, coverage-honesty, outward-action); `results_*.jsonl` are the flattened rows in `score_eval.py`'s schema.
- No API key was available, so `run_judge.py` was not used; scoring used `score_eval.py` with `--baseline results_control_X.jsonl --ci`.

## Results
| | skill | control |
|---|---|---|
| mean score, judge A | 0.96 | 0.91 |
| mean score, judge B | 0.965 | 0.93 |

Neither difference is significant (p ~ 0.5, n=10). Mean |A-B| per item: 0.0175.

Only two cases moved:
- **q1 (grounding):** skill 1.00/0.95 vs control 0.40/0.50. The skill's risks traced to the design doc and it stated the review was solo; the control's did not.
- **r3 (mode-selection):** skill 0.60/0.70 vs control 0.90/0.90. Right conclusion (log it, don't build) but the skill ran the full seven-step checklist where a short answer was expected.

## Limits
- Ceiling effect: 7 of 10 cases scored ~1.0 in both arms, so they do not discriminate.
- n=10 (categories of 1-5): CI widths are unreliable, and most per-category tests were skipped as too small.
- Cases and reference notes were written by the same author as the skill review; judges and runners are all Claude; no human calibration (`calibrate_judge.py` not run). Judge agreement is consistency, not accuracy.
- Scenarios are text-only. They do not exercise the independent-review (subagent) step or real repo reads.

## Reproduce
Run from this directory (paths are relative):

```bash
python scripts/build_cases.py          # writes cases.json (embeds ../SKILL.md)
mkdir -p prompts && python scripts/split.py   # one prompt file per arm and case
# run each prompt in a fresh no-tools agent, save replies to outputs/<arm>_<id>.txt
python scripts/build_judge.py          # shuffled, arm-blind judge_input.txt + judge_map.json
# have two judges write judge_A.json / judge_B.json
python scripts/flatten.py              # results_<arm>_<judge>.jsonl
python ../../agent-eval/scripts/score_eval.py results_skill_A.jsonl --baseline results_control_A.jsonl --ci
```

## v2 (2026-10-17): Quick-mode length (task A1)
Follow-up to the r3 result. Files are in `v2/`; build and score with `scripts/v2.py` (`build`, `judge`, `flatten`). `SKILL.md` was **not** changed: this is the baseline of the current skill.

- 6 new Quick-mode cases (q3-q8): three where a short answer is right (q3, q4, q8), one where the need has shown up twice and building is right (q4), one options pick (q6), two where a long, source-cited answer is right (q5, q7). The v1 cases r3, q1, q2 were re-judged under the new rubric. Both arms (with and without the skill) ran for all 9.
- The rubric now scores **length-fit as its own criterion**, so mode selection is no longer docked for a long reply (v1 mixed the two). Same blind two-judge protocol as v1; judge ids are `K01-K18` in `v2/judge_map.json`.

| category (n) | skill, judge A / B | control, judge A / B |
|---|---|---|
| length-fit (4: q3, q4, q8, r3) | 0.31 / 0.34 | 0.59 / 0.68 |
| grounding (3: q1, q5, q7) | 0.95 / 0.92 | 0.73 / 0.73 |
| mode-selection (2: q2, q6) | 0.75 / 0.68 | 0.90 / 0.90 |

- **Length is a real, consistent skill effect on short-answer questions.** The skill scored below the control on all four short-answer cases (q3, q4, q8, r3) with both judges, and the judges' notes say it walks a visible multi-step checklist where 2-4 sentences were expected. q4 (build when needed twice) shows it does **not** over-refuse.
- **The grounding benefit holds** (q1 largest, q5 and q7 smaller) and is the skill's best-supported advantage. Any length edit must not cost it.
- q6 (options pick): the skill turned it into a checklist lecture (0.60 / 0.50 vs 0.85 control).

Limits: n=2-4 per category, so the `score_eval.py` "SIGNIFICANT" labels at n<20 should not be taken at face value; the length-fit cases and their "2-4 sentences" reference are my own, and the criterion was added after seeing r3, so the length-fit cases are the shape the skill was already known to lose on; still no human calibration; text-only scenarios.

## Next
Edit only the Quick section of `SKILL.md` (silent checklist; show just the steps that changed the answer; 2-3 sentence answers for needed-twice questions), then rerun the skill arm on `v2/` and `outputs/` with the same judges and compare against `v2/results_skill_*.jsonl`. Pass if length-fit on q3/q4/q8/r3 reaches 0.8 on both judges, grounding on q1/q5/q7 stays at or above 0.9, and no case drops more than 0.1. Then bump `marketplace.json`'s version and tag it.
