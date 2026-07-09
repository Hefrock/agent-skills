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
  "quasi_identifiers": QI_PROFILE        // Track 2 (ED) input; not scored in v0
}
```

`identity_key` is what a re-identification attacker is trying to recover. Two records
with the same `identity_key` are the same synthetic person (supports linkage tests).

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
`population_ref`. Present now, scored later.

## Why generation-time ground truth matters

Because the generator injects every identifier, leakage scoring is *deterministic*:
"did this exact span survive?" has a yes/no answer with no judge in the loop. This is
the whole reason to start with Track 1 — it produces trustworthy numbers before any
LLM-judged track is attempted. The judge is reserved for the inference track, where
ground truth genuinely can't be enumerated.
