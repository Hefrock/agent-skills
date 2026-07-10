# Utility and the privacy-utility frontier

Every attack track in this harness measures **privacy**: how much identifying signal a
de-identifier fails to remove (Track 1) or how re-identifiable what remains is (Tracks
2-3). None of that is meaningful alone. A scrubber that replaces the entire note with
`[REDACTED]` has perfect privacy on every track and zero clinical value. So the harness's
cardinal rule is: **never report a privacy number without a paired utility number.** The
real deliverable is a *frontier* across defenders, not a leaderboard.

## Utility is the mirror of Track 1

Track 1 marks **identifier spans** that must NOT survive scrubbing; leakage is an
identifier surviving. The utility axis marks **clinical spans** that MUST survive; a
utility loss is a clinical span destroyed. Both are exact by construction and checked by
the same offset self-test.

Clinical spans are marked with `--utility` and cover content that is clinically
necessary but **not** identifying:

| clinical_category | example | why it must survive |
|-------------------|---------|---------------------|
| `diagnosis` | "community-acquired pneumonia" | the phenotype every downstream task needs |
| `age` | "54" (only when ≤ 89) | dosing, risk scoring; ages > 89 are PHI and are an *identifier* span instead |
| `sex` | "woman" / "man" | a core clinical and phenotyping variable |

These are marked in place over the note as already generated — no new text, no RNG draw —
so a `--utility` corpus is byte-identical to a plain one on `note_text`, `identifiers`,
and `quasi_identifiers`. Tracks 1-3 run on it unchanged.

## Scoring

`score_utility.py` reads the same `run_track1.py` rows the leakage scorer uses. A clinical
span is **preserved** iff no redacted span overlaps it (any overlap breaks the term).
Exact when the defender reports its redaction spans; a black-box defender falls back to an
occurrence-budget presence check, documented as approximate — the same tradeoff as Track
1's fallback. Utility is preserved/total, sliced by clinical category, never averaged into
one figure that would hide *which* clinical content a scrubber eats.

## The frontier

`score_frontier.py` runs every defender over one corpus and reports a `(privacy, utility)`
pair each, reusing the two model-independent scorers directly. On the default corpus:

| defender | privacy (Safe Harbor coverage) | utility (clinical preserved) |
|----------|-------------------------------|------------------------------|
| `regex-baseline-v0` | 0.449 | 1.000 |
| `over-redact-v0` | 0.900 | 0.600 |

Read it as a tradeoff, not a ranking. The baseline barely redacts, so it leaks
identifiers (low privacy) but keeps all clinical content (full utility). `over-redact-v0`
sweeps up every capitalized token and every number, and merges redactions separated only
by separators so a multi-token name collapses into one span — catching the names,
spelled-out dates, and geography the baseline misses (privacy up to 0.90) — but in doing
so it deletes every patient's **age** (a bare number: 0/50 preserved) and the
**capitalized diagnoses** like "Fabry disease" (40/50), while lowercase diagnoses and
`sex` survive. Neither defender dominates the other; the gap between them is the frontier.
A third, smarter pipeline would try to push up and to the right of both — and this is
exactly the plot on which to prove it did.

## Why this stays model-independent

Both axes are deterministic: identifier survival and clinical-span survival are string
facts, no judge required. So the frontier never depends on a model's blind spots — the
same property that makes Tracks 1-2 trustworthy. Over-redaction is a *measured cost* here,
not a safe default; a defender that hides behind "at least it's private" is put on the
same plot as everything it destroyed.
