# v4 result (2026-11-03)

Pre-registered in `PROTOCOL.md` (committed in 6613327 before any reply existed). One blind batch,
48 replies (old SKILL.md at 5e222c6 / edited SKILL.md / no skill x 16 cases), judge A Opus-family,
judge B Sonnet-family, runners Sonnet-family. Full tables: `SUMMARY.md`. Per-item rows: `results_*.jsonl`.

| category (n) | old A / B | new A / B | control A / B |
|---|---|---|---|
| length-fit, short (7: q3 q4 q8 r3 f1 f2 f3) | 0.76 / 0.84 | **0.53 / 0.64** | 0.38 / 0.51 |
| length-fit, depth (f4) | 0.50 / 0.40 | 0.55 / 0.45 | 0.35 / 0.35 |
| grounding (3: q1 q5 q7) | 0.88 / 0.82 | 0.88 / 0.87 | 0.62 / 0.73 |
| mode-selection (2: q2 q6) | 0.90 / 0.95 | 0.93 / 0.90 | 0.82 / 0.85 |
| a1-a3 flags existing tool / risk (both judges) | 3 / 3 | 3 / 3 | 3 / 3 |

## Decision rules, applied as written
1. **Revert: FIRES.** Short-case length-fit for the edit is below the old skill on both judges
   (A -0.23, B -0.20, about twice the ~0.1 single-item judge noise seen before). Grounding does not
   fall (A equal, B +0.05).
2. **Not working: does not fire, but the test does not discriminate.** The edited skill flags the
   existing tool or risk in 3 of 3, but so do the old skill and no skill at all. With the excerpts
   embedded in the prompt, every arm saw the answer. a1-a3 as built cannot show the edit helps.
3. **Stop and ask: FIRES.** All seven short cases score lower for the edit than for the old skill on
   both judges (largest: q8 -0.45 / -0.40, q3 -0.35 / -0.30, f3 -0.30 / -0.20).

## Why the edit lengthens short answers (from the replies, not tuned against)
- The step-7 reversal condition leaks into "not yet" answers even though the wording scopes it to
  build or adopt decisions: 6 of 7 short edited replies carry a dated condition (e.g. new_q3
  "Reversal condition: if the log shows another manual rename batch by 2026-12-03...").
- Step 2's "already exists / by hand" options add a paragraph (new_q3: "check whether your OS
  already does this").
- Judge rationales cite both (e.g. q8: "four paragraphs with build advice and a reversal date").
- Median words on short cases: old 116, new 167, control 171.

## Deviations
- Six runners returned their reply as text instead of writing the file (old_a1, old_a2, new_a1,
  new_a2, control_a2, control_q4); those replies were saved verbatim from the returned text.
- Exact model IDs are not recorded in this repo; families are, as in v1-v3.

## Limits
n = 1 reply per case per arm; author-written cases (a1-a3 written with the known answer in hand);
one model family for runners and judges; no human check of this batch.
