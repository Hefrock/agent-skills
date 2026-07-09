# Manifest Schema (v1) — the data contract

The manifest is the single source of ground truth. Every scorer reads it; nothing
overrides it. It is emitted by the corpus generator *at injection time*, so spans are
exact by construction rather than recovered by a fallible tagger. Freeze this schema
before writing scorers — changing it later forces a rebuild of every track.

The schema deliberately carries fields for all three attack tracks even though only
Track 1 (Safe Harbor leakage) is scored in this slice. The quasi-identifier profile
and identity key exist now so the Expert Determination and inference tracks can be
added later without regenerating corpora.

## Top level

```json
{
  "manifest_version": "1",
  "seed": 20260101,
  "generator": "synthetic-v0",
  "population_ref": "population.jsonl",   // background population for the ED track (optional in v0)
  "records": [ RECORD, ... ]
}
```

`population_ref` points at the denominator population used by the Expert Determination
track (Track 2). It is `null` until you generate with `--population N`, which emits a
`population.jsonl` of QI profiles alongside the corpus and sets this field to its
filename (resolved relative to the corpus). The corpus and its reference population thus
always travel together. The population rows share the QI field names of a record's
`quasi_identifiers` block by construction (see `generate_corpus.qi_profile`), so the
generator and the scorer generalize them identically.

## RECORD

```json
{
  "record_id": "rec-000001",
  "identity_key": "person-000001",       // true synthetic identity; the re-id target
  "note_text": "…full clinical note…",
  "identifiers": [ SPAN, ... ],          // Track 1 ground truth
  "quasi_identifiers": QI_PROFILE,       // Track 2 (ED) input
  "inference_case": INFERENCE_CASE       // Track 3 input; present only with --inference
}
```

`identity_key` is what a re-identification attacker is trying to recover. Two records
with the same `identity_key` are the same synthetic person (supports linkage tests).

`inference_case` is attached only when the corpus is generated with `--inference`. Its
presence adds a field but never alters `note_text`, `identifiers`, or
`quasi_identifiers` — those are produced from a disjoint RNG stream, so a corpus is
byte-identical on the Track 1/2 fields with or without the flag.

## SPAN — one injected identifier instance

```json
{
  "span_id": "rec-000001:s03",
  "start": 142,                 // char offset into note_text, inclusive
  "end": 154,                   // char offset, exclusive; note_text[start:end] == text
  "text": "555-0142",
  "hipaa_category": "phone_fax",    // one of the 18 Safe Harbor categories (see safe-harbor-identifiers.md)
  "surface_form": "dashed",         // how this value was rendered (dashed/spelled/embedded/header/…)
  "context": "narrative"            // where it sits: header | signature | narrative | structured
}
```

Invariant, checked by the generator's self-test: for every span,
`note_text[start:end] == text`. If that assertion ever fails, the corpus is invalid
and scoring is meaningless — so it is enforced at generation time, not trusted.

`surface_form` and `context` are what make the corpus a real test rather than a toy:
the same logical identifier (a phone number) is injected many ways (dashed, dotted,
spelled out, mid-sentence, in a fax header) so coverage is measured as
*category × surface form × context*, not just "did it catch phone numbers once."

## QI_PROFILE — quasi-identifier profile (Track 2 input, unscored in v0)

```json
{
  "age": 47,
  "sex": "F",
  "zip3": "021",              // first 3 ZIP digits — Safe Harbor's own geographic unit
  "admission_date": "2026-03-11",
  "rare_diagnosis": false,
  "facility_id": "fac-07"
}
```

These are the fields an Expert Determination risk model consumes to compute
population uniqueness (k-anonymity, prosecutor/journalist/marketer risk) against
`population_ref`.

## INFERENCE_CASE — inference vignette (Track 3 input, present only with --inference)

```json
{
  "target_attribute": "diagnosis",     // what the attacker must recover
  "target_value": "Fabry disease",     // ground truth; NEVER a substring of `note`
  "is_rare": true,                     // rare vs common — the key recovery slice
  "note": "De-identified vignette: a 45-49 man presents with acroparesthesias…"
}
```

`note` renders the diagnosis's clinical signature (an anchor finding plus a random
subset of supporting features) with the diagnosis name withheld — so the target is
*derivable from context but never stated*. The generator's `inference_self_test`
enforces that `target_value` is not a substring of `note`; if it were, Track 3 would be
measuring leakage (Track 1), not inference. Scoring is a normalized string match against
`target_value` (exact, since the diagnosis is drawn from a closed set), so Track 3 is
verifiable offline; an LLM attacker's free-text guesses move scoring to agent-eval's
judge.

## Why generation-time ground truth matters

Because the generator injects every identifier, leakage scoring is *deterministic*:
"did this exact span survive?" has a yes/no answer with no judge in the loop. This is
the whole reason to start with Track 1 — it produces trustworthy numbers before any
LLM-judged track is attempted. Track 3 stays deterministically scorable too, but only
because its inference target is drawn from a closed synthetic set; the LLM judge becomes
necessary the moment a real attacker returns free-text guesses whose correctness is a
matter of semantic, not string, equality.
