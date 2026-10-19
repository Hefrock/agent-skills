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

## v3 (2026-10-18): Quick-section edit, skill arm rerun
`SKILL.md`'s Quick bullet was rewritten (checklist runs silently; reply sized by question type; premortems keep source-cited risks and the solo-review label). The skill arm was rerun on the same 9 cases with the same blind two-judge protocol and shuffle seed (`scripts/v3.py`, files in `v3/`). Control replies and the v2 baseline are unchanged. Replies were generated by fresh Sonnet agents that read a prompt file and wrote their reply to disk (v1/v2 used no-tools agents; this is a small deviation).

| category (n) | v2 skill A / B | v3 skill A / B | pass bar |
|---|---|---|---|
| length-fit (4: q3, q4, q8, r3) | 0.31 / 0.34 | 0.95 / 0.97 | >= 0.8 both: **met** |
| grounding (3: q1, q5, q7) | 0.95 / 0.92 | 0.95 / 0.87 | >= 0.9 both: **met for A, missed for B** |
| mode-selection (2: q2, q6) | 0.75 / 0.68 | 0.95 / 0.90 | (no bar) |

- No case dropped by more than 0.1 (largest: q7, judge B, 0.90 -> 0.80, a "minor inference" note). Judge A saw no grounding change.
- The grounding miss is one case on one judge at n=1 per case, within the noise seen between judges; it is reported as a miss, not re-tuned away. q5 and q7 replies are shorter (1157 -> 609 and 875 -> 588 words) yet still cite the plan.
- Same limits as v2: self-written cases, all-Claude judges and runners, no human calibration, text-only. The length-fit cases are the shape the skill was known to lose on.

### v3 grounding replicates (q1, q5, q7)
Three extra skill-arm replies per case (same prompts, fresh agents), graded blind together with the original v3 reply (12 items, grounding only, `scripts/v3_rep.py`, files in `v3/rep/`).

| case | judge A (4 replies) | judge B (4 replies) |
|---|---|---|
| q1 | 0.85 | 0.84 |
| q5 | 0.89 | 0.87 |
| q7 | 0.90 | 0.90 |
| all 12 | 0.879 | 0.868 |

- **The grounding miss did not wash out.** Both judges now land below the 0.9 bar (v2 skill: 0.95 / 0.92). Typical deductions: general-knowledge risks listed under "from the plan text" without a label, and a few claims asserted rather than hedged.
- **Judge noise is large.** Judge A scored the same original q1 reply 0.95 in the first batch and 0.80 here; treat differences under ~0.1 as noise.
- **Not yet a clean comparison.** The v2 (old SKILL.md) arm was not re-graded in this batch, so "v3 is worse on grounding" versus "this batch is stricter" is unresolved. The decisive control is replicates of the old SKILL.md graded in the same blind batch.

### v3 old-SKILL.md control (same blind batch)
Four replies per case from the pre-edit `SKILL.md` (git HEAD) were graded in one shuffled 24-item batch with the 12 edited-skill replies (`scripts/v3_ctl.py`, files in `v3/ctl/`). One old-skill reply (q5 run 3) was truncated by an interrupted write, deleted and regenerated.

| arm | judge A (q1 / q5 / q7, mean) | judge B (q1 / q5 / q7, mean) |
|---|---|---|
| old SKILL.md | 0.86 / 0.91 / 0.92, **0.900** | 0.87 / 0.89 / 0.91, **0.889** |
| edited SKILL.md | 0.88 / 0.88 / 0.93, **0.892** | 0.89 / 0.85 / 0.89, **0.875** |

- **The edit did not measurably cost grounding.** The arms differ by 0.008 (A) and 0.014 (B), far inside the ~0.1 judge noise seen earlier. The earlier "0.95 / 0.92 to 0.87" drop came from comparing across batches; this batch grades both arms lower.
- **The absolute 0.9 bar is batch-dependent.** In a stricter batch even the old skill scores 0.889 on judge B. Read grounding as a relative check (edited vs old, same batch), which passes.
- q5 is the weakest case for the edited skill on both judges (0.88 / 0.85 vs 0.91 / 0.89), still within noise at n=4.
- Same limits as before: self-written cases, all-Claude judges and runners, no human calibration, text-only.
