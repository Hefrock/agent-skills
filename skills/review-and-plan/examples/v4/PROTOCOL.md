# v4 pre-registration (written 2026-11-03, BEFORE any v4 reply exists)

Purpose: test the decision-protocol edit to `SKILL.md` (step 1 reads current state, step 2 always
includes do-nothing / by-hand / existing options, step 7 adds a reversal condition and check date for
build or adopt decisions, coverage line declares skipped steps).

## Design
- Arms, all generated fresh and graded in the same blind batch: `old` (SKILL.md at 5e222c6), `new`
  (the edit), `control` (no skill). The old arm is regenerated rather than reusing v2/v3 results,
  because v3 showed identical replies scoring 0.59 in one batch and 0.36 in another, and the runner
  and judge models have changed since those batches.
- Cases: v2 set (q1-q8, r3), fresh set (f1-f4, frozen, read from `v3/human/fresh/fresh_cases.json`
  unchanged), new a1-a3 (`a_cases.json`, hash in `A_FREEZE.txt`). One reply per case per arm: 48.
- a1-a3 embed file excerpts with distractors instead of giving the runner a repo. This tests whether
  the reply notices and uses the existing tool when it is in front of it, not whether it goes and looks.
- Runner: a fresh Sonnet-family subagent per reply, reading one prompt file (`scripts/v2.py` INSTR
  preamble) and writing its reply to a file, as in v3.
- Judges: A = Opus-family, B = Sonnet-family subagents, prompts built by `scripts/v4.py judge`.
  Batch `main` (39 items) uses the v2 rubric verbatim. Batch `a` (9 items) adds one binary
  criterion, `flags_existing`.

## Decision rules (fixed now, from the handoff)
1. **Revert** if, for the `new` arm versus the `old` arm in this batch, mean length-fit on the short
   cases (q3, q4, q8, r3, f1, f2, f3) OR mean grounding (q1, q5, q7) is lower on BOTH judges.
2. **Not working** if the `new` arm flags the existing tool or risk in fewer than 2 of a1-a3. A case
   counts as flagged when both judges give `flags_existing` = 1. A keyword check (`v4.py`
   `mech_flag`) is reported alongside as a cross-check, not as the rule.
3. **Stop and ask J** (no re-tuning) if any existing case (q*, r3, f*) scores lower for `new` than
   `old` on both judges.
4. Check date for the edit: 2026-12-02.

## Known weaknesses, stated in advance
- n = 1 reply per case per arm. Earlier rounds measured ~0.1 judge noise on single items, so rules 1
  and 3 can fire on noise. They are applied as written anyway; the report will say so.
- a1-a3 were written by the same author as the edit, with the known answer in hand.
- Runners and judges are all one model family.
