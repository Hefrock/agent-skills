# Human check of the v3 judges (2026-10-19 to 2026-10-26)

One rater (the repo owner) scored review-and-plan replies blind, using `skills/agent-eval/scripts/human_score.py`, and the result was compared with the LLM judges. This folder holds the data behind the two rows added to the calibration log in `skills/agent-eval/references/llm-judge-prompt.md`, so they can be re-checked from the repo.

Limits: one rater; the replies and both judges are from the same model family; the cases and reference notes were written by the same author as the skill; small n. This shows how a person's scoring and the judges' line up, not that either is right.

## Re-check the two calibration rows

From the repo root:

```bash
H=skills/review-and-plan/examples/v3/human
# Row 1: 17 blind first-pass scores (one typing slip excluded), judge A -> mean delta 0.203, exit 1
python skills/agent-eval/scripts/calibrate_judge.py $H/run1/judge_A.jsonl $H/run1/human_first_excl.jsonl
# Row 2: 8 fresh held-out replies, first-pass scores, judge A -> mean delta 0.131
python skills/agent-eval/scripts/calibrate_judge.py $H/fresh/judge_A_length_fit.jsonl $H/fresh/human_first.jsonl
# After the one non-blind rescore (f1:control 0.6 -> 0.3) -> 0.094
python skills/agent-eval/scripts/calibrate_judge.py $H/fresh/judge_A_length_fit.jsonl $H/fresh/human_latest.jsonl
```

Check the freeze hashes (from `fresh/`): `sha256sum -c FRESH_FREEZE.txt` and, for the human first pass, `sha256sum human_first.jsonl` and `head -8 real/scores.jsonl | sha256sum` against `FRESH_HUMAN_FREEZE.txt`. The file has one comment line, so `sha256sum -c` prints a harmless "improperly formatted" warning, and `real/scores.jsonl` fails a whole-file check on purpose: its 9th line is the rescore, added after the hash was taken (the first 8 lines match).

## Contents

| Path | What it is |
|---|---|
| `rubric.txt` | The rubric the rater saw. Its length-fit criterion has human-only anchors (strict reading: a visible checklist, headed sections or more than about 220 words scores 0.3). The judge prompt was not changed. |
| `REPORTING_RULE.md` | Written before the figure was logged: the headline is the blind first-pass score; known typing slips are excluded, not replaced; changes of mind are revisions and reported separately. |
| `LENGTH_FIT_ANCHORS.md` | How the anchors were drafted, from which replies, and the cautions (they were built from the disputed replies, so they needed testing on new ones). |
| `run1/` | First run: 18 replies (9 cases, skill and control). `cases.jsonl` has the cases and replies; `judge_A.jsonl` is judge A; `human_first.jsonl` is the blind first pass; `human_first_excl.jsonl` drops `skill:q2` (a typed 0.0 meant as about 0.9); `human_final.jsonl` is after 6 revisions. |
| `fresh/` | Validation on 4 new cases (8 replies), with rules fixed before any reply existed: `FRESH_PROTOCOL.md`, `fresh_cases.json` (frozen, see `FRESH_FREEZE.txt`), `outputs/` (the 8 replies), `judge/judge_input.txt` and `judge_map.json` (arm-blind, shuffled), `judge_A.json` (Opus) and `judge_B.json` (Sonnet), the human scores, and `FRESH_RESULT.md`. `real/` holds the blind queue, key and score log. |

The scripts used to build prompts, assemble the queue and the judge batch are not included; they hardcode local paths. The generation prompt was the `INSTR` preamble in `examples/scripts/v2.py`, the case material and user request, and, for the skill arm, the current `SKILL.md`.

## Results

| | Mean difference vs judge A | Largest single item |
|---|---|---|
| Run 1, blind first pass, 17 items | 0.203 | 0.50 |
| Run 1, after 5 judgment revisions (same 17) | 0.132 | |
| Fresh set, blind first pass, 8 items (pre-registered: hold if <= 0.15 and no item > 0.4) | **0.131** | 0.40 |
| Fresh set, after one non-blind rescore | 0.094 | 0.25 |

Verdict on the fresh set under the pre-registered rules: the anchors hold, with no margin on the largest-item rule (exactly 0.40). Judge B gives 0.113 on the first pass.

On the three short-answer fresh cases the skill's replies were 73 to 107 words against 172 to 269 for control, and the rater and both judges agreed the skill fits better. On the depth case (f4) both arms were about 550 words against a reference of 120 to 250, so the v3 edit did not shorten that kind of reply.

## Not done yet

- Three scores in run 1 are still to be redone (`control:q1`, `control:q6`, `skill:q5`), then three length-fit scores under the new anchors (`control:q3`, `r3`, `q8`). The run-1 queue, key and score log are not included for that reason.
- A Gemini judge as a third, cross-family check, waiting on an API key.
