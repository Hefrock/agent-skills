# Person sources — swapping synthetic people for real Synthea data

`make_person()` was always the single swap point for real data. `person_sources.py` makes
that a registry, the same pattern as the de-id defenders and the inference attackers: the
corpus generator, the injection/manifest machinery, and every scorer stay put — only
where a person's fields come from changes.

| source name | what it is |
|-------------|-----------|
| `synthetic-v0` (default) | wraps `make_person()`. Unchanged, byte-identical — the default corpus and every locked number are exactly as before. |
| `fhir-synthea` | reads a directory of Synthea FHIR R4 patient bundles and maps each `Patient` to the person dict the generator consumes. |

```bash
python generate_corpus.py --person-source fhir-synthea --fhir-dir ./synthea_output \
    --inference --utility --out corpus.json
```

## Why this is the biggest fidelity lever

The synthetic generator draws ZIP3 uniformly over ~999 values, which is why Track 2's
prosecutor / k-anonymity numbers are an acknowledged **upper bound** (everyone looks
unique because the geography is unrealistically dispersed). Real Synthea output carries
real address distributions, real age/sex structure, and real comorbidity — so feeding it
through `fhir-synthea` turns that upper bound into a **defensible estimate**, and the
cross-track re-id axis with it. Nothing downstream changes; the realism enters entirely
at the source.

## FHIR → person mapping (the contract)

Per patient bundle (files with no `Patient` are skipped, e.g. Synthea's hospital/
practitioner bundles):

| person field | FHIR source | notes |
|--------------|-------------|-------|
| first / last | `Patient.name[0].given[0]` / `family` | Synthea digit suffixes (`Adaeze317`) stripped |
| sex | `Patient.gender` | `female`→`F`, else `M` |
| age | `Patient.birthDate` | `2026 − birth year` |
| city / zip3 | `Patient.address[0].city` / `postalCode` | ZIP3 = first 3 digits of the real postal code |
| ssn / mrn | `Patient.identifier` | by `us-ssn` system / `MR` type; derived deterministically if absent |
| phone | `Patient.telecom` (`phone`) | derived if absent |
| diagnosis / rare | `Condition.code` text/display | mapped to the nearest known signature diagnosis (see below) |
| admission / last_seen | `Encounter.period.start` | earliest / latest; derived if absent |
| facility | `Encounter.serviceProvider.display` | derived if absent |

Fields FHIR does not carry are derived deterministically from the patient id, so a record
is always complete and reproducible.

## Known limitations (read before a real run)

- **The FHIR reader is validated against a bundled Synthea-structured fixture**
  (`fixtures/fhir/`), not against a live Synthea export in this environment. Sanity-check
  the first real run — Synthea profiles vary (identifier systems, extensions), and the
  mapping above covers the common shape, not every variant.
- **Track 3 diagnosis mapping is lossy by design.** Real conditions are open-vocabulary
  SNOMED; the harness's inference signatures cover nine diagnoses. `fhir-synthea` maps
  each condition to the nearest known diagnosis (keyword match, else a deterministic
  fallback) so the signature-based Track 3 still runs. True open-vocabulary inference is
  the LLM attacker's job, not the deterministic baseline's.
- **The Track 2 population is still synthetic.** `--population` draws from `make_person`
  regardless of source, so a fully FHIR-consistent Track 2 needs a FHIR-sourced population
  too (a larger directory of bundles). Until then, read a FHIR-sample-vs-synthetic-
  population run as mixed, not apples-to-apples.
- Adding a source is a registry entry in `person_sources.py` — a `PersonSource` subclass
  whose `person(i, rng)` returns a dict carrying every key in `PERSON_KEYS`.
