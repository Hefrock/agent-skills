---
name: deid-reid-harness
description: >
  Build and run an adversarial de-identification ⟷ re-identification evaluation harness
  for clinical text: generate synthetic clinical notes with ground-truth identifier
  spans, run a de-id pipeline (defender) over them, and score leakage against an
  attacker. Use this whenever the task involves evaluating a PHI scrubber or de-id
  pipeline, measuring re-identification risk, testing HIPAA Safe Harbor coverage or
  Expert Determination risk, building a privacy-utility frontier for clinical NLP, or
  comparing de-identification approaches — even if the user only says "test my
  de-identifier," "how leaky is this scrubber," or "de-id eval." Prefer this over an
  ad-hoc script whenever synthetic clinical data, PHI, or de-identification evaluation
  is mentioned.
---

# De-id ⟷ Re-id Adversarial Eval Harness

## The core idea

A de-identifier's real metric is **not its recall** — it is its **survival against the
best available re-identification attacker**. So the attacker *is* the eval. Report a
scrubber's score only as its survival against a defined attack, never as a standalone
"accuracy."

The attacker is really **three different attackers**, corresponding to three threat
models with three different standards. Never collapse them into one number:

1. **Direct-identifier leakage** → HIPAA **Safe Harbor** (a checklist: remove 18
   identifier categories). Deterministically scorable because ground truth is known.
2. **Quasi-identifier linkage** → **Expert Determination** (a statistical argument that
   re-id risk is very small). Needs a background population as denominator.
3. **Free-text inference** → the pure AI-era threat (a model *derives* identity from
   context that never states it). Needs an LLM judge; the only track where ground truth
   can't be enumerated.

A Safe Harbor pass can still be an Expert Determination failure. That divergence is the
whole point of the harness — surface it, don't hide it.

## What this slice builds (Track 1)

This slice implements Track 1 (Safe Harbor leakage) **end to end**, because it is the
only track that is deterministically scorable — you get a working closed loop with
trustworthy numbers before touching the harder tracks. Tracks 2 and 3 are designed for
but not yet scored; the manifest already carries their inputs so no corpus regeneration
is needed later.

The closed loop:

```
generate_corpus.py  ->  run_track1.py  ->  score_leakage.py
   (synthetic notes       (defender runs        (deterministic Safe
    + ground-truth          over corpus)          Harbor coverage,
    manifest)                                     sliced by cat×form×context)
```

## Running it

```bash
cd scripts
python generate_corpus.py --n 50 --seed 20260101 --out corpus.json
python run_track1.py --corpus corpus.json --pipeline regex-baseline-v0 --out runs.jsonl
python score_leakage.py --runs runs.jsonl --out leakage_report.json
```

The bundled `regex-baseline-v0` defender is deliberately weak. On the default corpus it
scores ~0.45 Safe Harbor coverage, and the breakdown is the point: ISO dates are caught
100%, but spelled-out and narrative dates 0%, names 0%, and the patient's city and
facility code 0%. That is the `category × surface_form × context` diagnostic working as
intended — a single averaged number would have hidden exactly the gaps that matter. The
report also lists `categories_not_exercised` — the Safe Harbor categories the corpus
never injects (fax, URL, IP, device IDs, …) — so a coverage number over 8 categories is
never mistaken for coverage of the full 18-item checklist.

## Architecture — four swappable parts + your eval harness as orchestrator

**Corpus generator** (`generate_corpus.py`). Emits synthetic notes and a manifest whose
spans are **exact by construction**: notes are assembled from segments and each
identifier's offset is recorded at placement time, then a self-test asserts
`note_text[start:end] == text` for every span. This is why Track 1 needs no tagger and
no judge. `make_person()` is the ONLY function to replace to swap in a real Synthea
driver (e.g. fhir-synthea-lab) — the injection and manifest machinery stay put. The
**surface-form layer** renders each logical identifier several realistic ways (a date
as ISO, slashed, spelled-out, or narrative; a name as initials; identifiers in headers,
signatures, and buried in narrative), because real notes leak precisely where they are
messy.

**De-id pipeline / defender** (`deid_pipelines.py`). A pluggable interface
(`scrub(text) -> (scrubbed_text, redacted_spans_or_None)`) with a registry, so the same
eval runs against a regex baseline, a rule-based scrubber, an LLM scrubber, and a
hybrid — comparing pipelines on one frontier is the deliverable. A pipeline that reports
its redaction spans gets exact scoring; a black-box pipeline returns `None` and the
scorer falls back to surface-string presence checks.

**Re-id attacker / scorer** (`score_leakage.py` = the Track 1 attacker). For Track 1 the
"attacker" is deterministic: did each ground-truth span survive? Output is coverage
sliced by category, by category×surface-form, and by context — never averaged into one
figure. Tracks 2 and 3 add the statistical-linkage attacker and the LLM-inference
attacker as sibling scorers.

**Orchestrator** (`run_track1.py`, and later your existing **agent-eval** harness).
agent-eval is where the adversarial-eval and judge-calibration machinery plug in: it
orchestrates runs, calibrates the inference-track judge and the attacker's confidence
(does a claimed-0.9 re-identification mean 90% correct against ground truth?), and diffs
across runs so hardening the scrubber shows up as movement on the frontier.

## Two rules that keep the harness honest

**Always pair privacy with utility.** A scrubber that redacts every token has perfect
privacy and zero clinical value — so any privacy number reported without a paired
utility metric (does the scrubbed note still support a downstream task: NER, phenotype
extraction, a CDS trigger?) is meaningless. The real deliverable is a **privacy-utility
frontier** across pipelines, not a leaderboard. Over-redaction is a measurable cost, not
a safe default.

**Never let attacker and defender share a base model.** If both are the same LLM their
blind spots correlate — the attacker misses exactly what the defender missed and the
score looks great and means nothing. Use diverse model families, and keep the
model-independent attackers (deterministic manifest check for Track 1; statistical
linkage for Track 2) load-bearing so the score never depends on one model's blind spots.

## Extending to Tracks 2 and 3

- **Track 2 (Expert Determination).** Generate a large Synthea **background population**
  and write it to `population_ref`; the corpus already emits each record's
  `quasi_identifiers` profile. Add a statistical-linkage scorer computing population
  uniqueness / k-anonymity and prosecutor-vs-journalist-vs-marketer risk. Decide
  population size and risk model deliberately — this is the most novel, highest-surface
  piece.
- **Track 3 (inference).** Add an LLM attacker that tries to derive `identity_key` or a
  sensitive attribute from the scrubbed note. This is where agent-eval's judge
  calibration is essential, since ground truth can't be enumerated.

## Reference files

- `references/manifest-schema.md` — the frozen data contract. Read before changing the
  generator or writing any scorer; every scorer depends on it.
- `references/safe-harbor-identifiers.md` — the 18 identifier categories as the Track 1
  test taxonomy, and where Safe Harbor and Expert Determination diverge.

## Standard traceability

Track 1 coverage is reported per Safe Harbor identifier category, so every test maps to
the specific identifier it exercises — the standard-anchored coverage matrix reviewers
and portfolio readers expect. Label Track 1 results as **Safe Harbor coverage**, never
as re-identification risk; conflating them is the exact error this harness exists to
expose.
