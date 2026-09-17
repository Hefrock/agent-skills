---
name: style-obfuscator
description: Computes a deterministic stylometric fingerprint of a piece of writing — function-word frequency, sentence-length distribution, punctuation habits (em dash rate, contraction rate), approximate passive-voice rate, and repeated-phrase detection — and optionally scores its similarity against a reference corpus of your own known writing (your vault, past posts). No model or network call; classic authorship-attribution stylometry (Mosteller & Wallace, Burrows' Delta) has always been word-frequency statistics, not semantic understanding. Use when the user wants to check whether a draft (a GitHub PR/README/commit message, a LinkedIn post, any public professional writing) carries a distinctive fingerprint that could later be used to match it against something they wrote under a different identity, asks "does this sound too much like me," "will this be traceable back to my other writing," or wants a stylometric self-check before publishing publicly. Triggers on "check my writing style," "stylometric fingerprint," "does this sound like me," "will this be traceable to my other posts," "style-obfuscator," and the script fingerprint.py. Does NOT rewrite or obscure text — see "What's NOT built here" for why that's a separate, harder, not-yet-built tier.
---

# Style Obfuscator

Tier 1 of a two-tier design: **flag, don't rewrite.** This version computes a
stylometric fingerprint and tells you what's distinctive about a draft's writing
style; it never touches the text itself.

## The actual threat model

Publishing under your real name on GitHub or LinkedIn isn't itself the risk — that
writing is already attributed to you. The risk is that a **distinctive enough public
writing fingerprint becomes the reference corpus** an adversary (or, per `privacy-
threat-oracle`'s `autonomous_ai_agent` adversary class, an automated scraper doing this
at machine scale) uses to match you to something you *didn't* attribute your name to —
a pseudonymous post, an anonymous opinion, anything you'd rather keep separate from
your professional profile. Hardening the public side makes it a weaker reference for
that kind of matching.

The most directly useful workflow follows from that: build a reference corpus from
your own known writing, then run a **pseudonymous** draft against it before posting —
not to check the public draft alone, but to see whether the anonymous one leans on the
same distinctive habits (em dash rate, favorite hedges, a specific function-word
profile) that a matcher would key on.

## Why this is deterministic-only, not model-based

Unlike `privacy-linter`'s deferred stylometric class (genuinely blocked on a local-
model decision that hasn't been made — see its `references/leak-taxonomy.md`),
computing a stylometric *fingerprint* doesn't need a model at all. Real authorship
stylometry research (the Federalist Papers disputed-authorship study, Burrows' Delta)
has always been built on function-word frequency and sentence statistics — topic-
independent, unconsciously produced, and fully computable with `collections.Counter`
and arithmetic. What genuinely needs judgment is the *comparison* step — deciding what
a similarity score means for a specific threat model, and whether two corpora really
are the same author — which is exactly why this tool reports numbers and deltas, not a
verdict.

One consequence worth being direct about: because the content here is about to be
published publicly anyway (a GitHub PR, a LinkedIn draft), sending it to an external
LLM for a *rewrite suggestion* would not be a new disclosure the way it would for
`privacy-linter`'s private-vault content — that's the exact reason a future rewrite
tier is a fundamentally different, more tractable design problem than `privacy-
linter`'s deferred stylometric detection. See "What's NOT built here."

## How this works

```bash
python scripts/fingerprint.py --file draft.md
python scripts/fingerprint.py --file draft.md --reference known_writing.md
python scripts/fingerprint.py --file draft.md --reference ~/vault/Journal/
python scripts/fingerprint.py --text -
python scripts/fingerprint.py --file draft.md --reference known.md --json
python scripts/fingerprint.py --file draft.md --reference known.md --emit-findings
python scripts/fingerprint.py --file draft.md --reference known.md --log-dir ~/.style-obfuscator-log
python scripts/fingerprint.py --file draft.md --show-phrase-text
```

1. **Fingerprint the draft.** Reports word/sentence/paragraph counts, mean sentence
   length and its standard deviation, vocabulary richness (type-token ratio), em-dash
   rate, comma/semicolon/exclamation rates, contraction rate, an *approximate*
   passive-voice rate (a regex heuristic — see the caveat below, not real POS tagging),
   the most frequent function words, and any 3-4 word phrase that repeats. Repeated
   phrases are **redacted by default** in both the text and `--json` report — shown
   only as a count + n-gram length ("3 found (3/4-word, text redacted...)") — because
   `top_phrases` is verbatim draft text, and a fingerprint report that leaks the actual
   phrases undermines the reason this tool exists if that report is ever shared, pasted,
   or logged. Pass `--show-phrase-text` to reveal the real phrases for your own
   terminal.
2. **Compare against a reference, if given** (`--reference`, a single file or a
   directory — a directory's `.md`/`.txt` files are concatenated into one corpus,
   since a reference is "everything I know is really you," not one sample). Reports
   an overall similarity score (0-100%) plus which specific features drive it —
   trivial zero-vs-zero absences (neither text ever uses a semicolon) are filtered out
   of the headline "closely matches"/"most different" lists, since an absence isn't
   an actionable tell the way a shared presence is.
3. **Act on what it tells you, don't trust a single score.** A 70% similarity on a
   35-word draft is noisy — short input gets an explicit warning in the report, not a
   confident-looking number. The tool never says "this is/isn't the same author"; it
   reports comparable numbers and leaves the judgment call to you.
4. **`--emit-findings`** turns the comparison into a `privacy-linter`-shaped Finding
   list instead of a report — `{severity, leak_class: "stylometric", finding, reason,
   location}`, the exact shape `scan_diff.py --json` already produces and
   `privacy-threat-oracle`'s `--from-linter-json` already reads. Below
   `--emit-findings-threshold` (default 60%) it emits `[]` rather than a synthetic
   low-severity finding — the same "report nothing for clean content" convention
   `privacy-linter` uses. Requires `--reference`; there's nothing to fingerprint-match
   without one.
5. **`--log-dir DIR`** appends one timestamped JSON record per run — `{timestamp,
   label, reference_label, similarity_score, severity}` — for trend-tracking whether a
   draft's stylometric distinctiveness is rising or falling over time. It never writes
   the fingerprint's numeric feature vector or `top_phrases`: even the feature rates,
   while not literal draft text, ARE the stylometric signal this tool exists to expose,
   so a durable log of them (unlike a one-off report you read and discard) is exactly
   the kind of data-at-rest this tool should minimize, not accumulate. Also requires
   `--reference`.

## A concrete example of the reference-corpus workflow

```bash
# Build a reference from everything already attributed to you:
python scripts/fingerprint.py --file pseudonymous_draft.md --reference ~/vault/
# High similarity on em_dash_per_1k, fw_but_per_1k, contraction_per_1k?
# Those are the specific things to vary before posting pseudonymously.
```

## What's NOT built here

- **Rewriting/obscuring.** Detection only — never touches the draft. Auto-rewriting to
  reduce distinctiveness is generation, not detection, and a crude rule-based version
  (synonym substitution, sentence restructuring) is a historically weak defense — tools
  like Anonymouth used this approach with mixed real-world results, and it risks
  mangling meaning. A real version needs an actual paraphrase-quality LLM call,
  presented as a diff to review and never auto-applied — a genuine follow-up, not
  attempted in this version.
- **Real POS-based passive-voice detection.** `passive_voice_per_1k` is a regex
  heuristic (`is/are/was/were/be/been/being` + a past participle) — it will
  false-positive on an adjectival use ("was excited") and false-negative on any
  irregular participle not in its fixed list. Treated as a directional signal only.
- **Cross-identity matching against a third party's writing.** The reference corpus
  here is always *your own* known writing. This tool tells you what a matcher would
  key on; it doesn't run an actual match against someone else's corpus, and never
  sends anything anywhere to do so.
- **Markdown-aware tokenization.** Headers, list markers, and code fences aren't
  stripped before analysis — this is built for prose (a LinkedIn draft, a PR
  description, README prose), and heavily-structured markdown will skew the sentence/
  paragraph statistics. Run it against the prose portions.
- **Any presence at the actual point of disclosure** (a LinkedIn post editor or GitHub
  PR box typed directly in-browser, never touching a file `fingerprint.py` can read).
  A structural property of this whole stack's file-based architecture, not something
  fixable in this skill alone — see `privacy-threat-oracle/SKILL.md`'s "What's NOT
  built here" for the full writeup and what was considered and deferred.

## Pairing

- **privacy-threat-oracle** — `--emit-findings` closes the loop that used to be a
  "designed to fit together, not actually wired up" note here: it emits a
  `{leak_class: "stylometric", ...}` Finding in `scan_diff.py`'s own JSON shape, so it
  pipes straight into `--from-linter-json` exactly like a `privacy-linter` scan does:
  ```bash
  python scripts/fingerprint.py --file draft.md --reference ~/vault/ --emit-findings | \
    python ../privacy-threat-oracle/scripts/oracle.py \
      --source-compartment personal --target-compartment public_professional \
      --target-exposure public_internet --from-linter-json -
  ```
  One thing worth knowing about that bridge: the oracle's `content_classes_from_linter_
  json` only reads each finding's `leak_class`, not its `severity` — so a "low" and a
  "high" severity stylometric finding both resolve to the same fixed `stylometric` →
  `medium` sensitivity tier on the oracle side. This tool's own severity field is real
  (it drives the ranking if you're reading `--emit-findings` output directly) but
  doesn't currently change the oracle's tier once it crosses.
- **privacy-linter** — a distinct, deferred leak class in that skill's own taxonomy;
  this skill is what actually builds the flagging half of it, scoped to a fingerprint
  rather than the full inference-cue class privacy-linter also defers.

## Output discipline

- Never present a similarity score as a verdict ("this is/isn't the same author") —
  it's a number for you to judge, the same way `agent-eval`'s calibration step never
  lets a judge score stand in for ground truth without a human spot-check.
- Always surface the short-input noise warning when word count is under 100 — a
  precise-looking percentage on a tiny sample is exactly the kind of false confidence
  this repo's other tools (`agent-eval`'s sample-size honesty rule) already guard
  against.
- Report the passive-voice rate as a heuristic every time it's shown, never bare —
  the same "don't let a degraded/approximate check look like a precise one" discipline
  `privacy-linter` already applies to its Pillow-unavailable metadata fallback.
- Never print `top_phrases`' verbatim text by default, in either the human report or
  `--json` — a tool meant to help you avoid becoming identifiable shouldn't itself leak
  identifying substrings in its own output. `--show-phrase-text` opts in explicitly, for
  your own terminal only.
- Never write the fingerprint feature vector or `top_phrases` to `--log-dir` — a
  durable trend log should hold the minimum needed to answer "is this getting more or
  less distinctive over time," not accumulate a second historical copy of the actual
  writing signal, the same "findings/score only, never the content" discipline
  `privacy-linter`'s own `--log-dir` established first.

## Files

| Path | What it is |
|---|---|
| `scripts/fingerprint.py` | The fingerprint computation + comparison + CLI |
| `scripts/test_fingerprint.py` | Unit + CLI test suite (stdlib unittest) |
