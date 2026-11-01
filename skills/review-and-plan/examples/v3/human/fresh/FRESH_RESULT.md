# Fresh length-fit validation: result (2026-10-26)

Pre-registered in FRESH_PROTOCOL.md; cases frozen (FRESH_FREEZE.txt); human first-pass frozen before
any judge scored (FRESH_HUMAN_FREEZE.txt). Judge prompt unchanged (built via v2.py judge()).
Judge A = Opus, judge B = Sonnet, one arm-blind shuffled batch of 8.

| item | human (first pass) | judge A | judge B |
|---|---|---|---|
| f1:control | 0.60 | 0.20 | 0.20 |
| f1:skill | 1.00 | 0.95 | 1.00 |
| f2:control | 0.30 | 0.35 | 0.30 |
| f2:skill | 0.80 | 0.80 | 0.80 |
| f3:control | 0.50 | 0.35 | 0.35 |
| f3:skill | 0.80 | 0.80 | 0.70 |
| f4:control | 0.10 | 0.35 | 0.20 |
| f4:skill | 0.10 | 0.25 | 0.25 |

Mean |human - judge A| = 0.131 (rule: hold if <= 0.15). Max item delta = 0.40 (f1:control; rule: hold
if no item > 0.4, so it passes with zero margin). Judge B: mean 0.113, max 0.40.

**Verdict under the pre-registered rules: anchors HOLD.** No rewrite of the anchors; no judge-prompt change
needed for length-fit.

Caveats recorded with the verdict:
- Margin is thin on the max-item rule: f1:control sits exactly at 0.40.
- f1:control (269 words) was scored 0.6 by the human; the strict anchors put >~220 words at 0.3. The
  human did not rescore. If rescored to 0.3 the delta would be +0.10 and the mean 0.106. The headline
  stays the first-pass figure.
- Judge A's rationale for f1:control cites substance (builds on one occurrence, invented numbers) as well
  as length: judges may let mode/grounding failures bleed into length_fit.
- f4 (depth expected): human 0.1 vs judges 0.20-0.35 for both arms (replies ~550 words vs a 120-250
  band). Direction is consistent, not over threshold.
- n=8, one rater, same model family for runners and judges. A hold shows the anchors are stable, not
  that the judge is calibrated.

Skill finding (separate from calibration): on the three short-answer cases the skill arm is 73-107 words
vs 172-269 for control, and human and both judges agree it fits better. On the depth case f4 the skill arm
is NOT shorter (564 vs 547 words).

## Revision: f1:control rescored (2026-10-26)

- f1:control (blind id b301517): 0.6 -> 0.3, appended via `rescore` (flagged `revised`; the first-pass
  line stays in fresh/real/scores.jsonl). The rescore note field was left blank.
- Rater's stated reason: "New rubric. changes applied."
- Made AFTER the judge scores had been shown to the rater, so it is NOT blind and is not validation
  evidence. The headline stays the first-pass figure (mean |d| vs judge A 0.131).
- Effect (fresh/human_latest.jsonl): vs judge A mean |d| 0.131 -> 0.094, largest item 0.40 -> 0.25
  (f4:control); vs judge B 0.113 -> 0.075, largest 0.40 -> 0.15.
- human_first.jsonl is unchanged (hash matches FRESH_HUMAN_FREEZE.txt).
