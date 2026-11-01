# length-fit anchors (2026-10-26) - APPLIED to rubric.txt (human only); v2.py / judge prompts NOT changed

Status: the rater chose the STRICTER reading (control:q3, r3, q8 = 0.3, not 0.5). Applied to
rubric.txt on 2026-10-26. Not applied to scripts/v2.py: a judge-prompt change needs a re-judge of both
arms in one blind batch (v4) and fresh held-out cases first.
NOTE: the existing 0.5 scores in final.jsonl for those three items predate the anchors and have NOT been
rescored; do that blind (rescore shows only your own previous score), not by editing the file.

## Proposed rubric text (replaces the one-line length-fit criterion)

- length-fit: length and visible structure are proportionate to the reference notes. Judge SHAPE, not
  correctness. Anchors for a case whose reference expects a SHORT answer (2-4 sentences, roughly 40-70 words):
  - 1.0: answer in the first sentence; 2-4 sentences, about 70 words or fewer; prose only.
  - 0.9: same, plus one extra caveat sentence; up to about 90 words; still prose, one paragraph.
  - 0.8: right answer first and still prose, but runs over: a second paragraph (e.g. "what would trigger
    it" or "what I can't tell"), about 90-120 words.
  - 0.5: right answer first, but adds a visible list or "what I'd do" section; about 150-200 words.
  - 0.3: reasoning is walked through around the answer: headed or numbered sections, a numbered
    recommendation list, or more than about 220 words.
  - 0.0-0.1: multi-part document, options table, or full checklist for a one-line question.
  - Too thin (cases that expect depth): a bare verdict with no reason where the reference expects
    reasons scores 0.3-0.5, mirroring the over-long side.
  A correct reply is not rescued by being correct: substance is not scored here.

## Where the evidence comes from (kept OUT of the rubric text, so a judge cannot memorize items)

| item | words | shape | judge | your blind | your final | anchor says |
|---|---|---|---|---|---|---|
| skill:q3 | 58 | 1 paragraph | 1.00 | 0.9 | 0.9 | 1.0 / 0.9 |
| skill:q4 | 87 | 1 paragraph | 1.00 | 0.9 | 0.9 | 0.9 |
| skill:q8 | 86 | 1 paragraph | 0.90 | 0.9 | 0.9 | 0.9 |
| skill:r3 | 91 | 2 paragraphs | 0.90 | 0.9 | 0.8 | 0.8 |
| control:q4 | 192 | prose + bullet "what I'd do" | 0.50 | 0.9 | 0.5 | 0.5 |
| control:q3 | 223 | prose + "suggested approach" bullets | 0.35 | 0.8 | 0.5 | 0.3 |
| control:r3 | 238 | prose + numbered recommendation | 0.30 | 0.8 | 0.5 | 0.3 |
| control:q8 | 310 | headed sections + numbered steps | 0.30 | 0.8 | 0.5 | 0.3 |

Word counts are from cases.jsonl `output` fields. Judge scores from judge.jsonl (judge A).

## Decision for you (this is your reading, not mine)

The anchors above put control:q3, r3 and q8 at 0.3, which is BELOW your revised 0.5 and matches the
judge. The alternative reading keeps them at 0.5 (list or numbered steps present, but the answer comes
first and the reply stays under about 250 words), which leaves roughly 0.15-0.2 of control-arm
disagreement with the judge. Pick one; the draft takes the first only because it follows the rubric's
own sentence "a visible multi-step checklist when a short answer was expected scores low".

## Cautions

1. These anchors were drafted from the very items in dispute. That is tuning on the test set. All four
   length-fit items (q3, q4, q8, r3) are used above, so there is no held-out length-fit item. Validate on
   NEW cases (3-4 fresh replies, written before scoring) before treating agreement as improved.
2. Changing the judge prompt in scripts/v2.py (line 97) changes the judge: old and new scores are not
   comparable across batches (judge noise is about +/-0.1). Re-judge BOTH arms together in one blind batch
   (a "v4"). The human rubric.txt can be changed first without touching any judge.
3. Word-count bands are a proxy. If a reply is under 90 words but is a visible 7-step list, the
   "walks through a checklist" language should win; the bands are there to stop drift, not to be a formula.
4. One rater (you), n=4 length-fit items.
