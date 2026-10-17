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

## Next
Add harder cases, including ones the skill is expected to fail, then rerun against these results as the baseline. Shorten Quick-mode answers (r3).
