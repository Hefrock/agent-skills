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
  (`fixtures/fhir/`) and, since, against a real 353-patient Synthea v3.3.0 Massachusetts
  export (light `years_of_history` config, seed 999) — that pilot run is what surfaced
  and fixed the ZIP3 issue below, not a hypothetical. Still worth a fresh sanity-check
  against a differently-configured Synthea export (a different state, a newer Synthea
  version) before trusting it blind — profiles vary, and the mapping covers the common
  shape confirmed so far, not every variant.
- **ZIP3 recovery from a degenerate Synthea postal code.** Confirmed on that same pilot:
  Synthea v3.3.0's Massachusetts module emits a literal `postalCode: "00000"` for
  patients in certain towns — systematically, per town, not per-patient randomness
  (~24% of the pilot population) — even though the same record carries a real city,
  state, and lat/long. Left unhandled, that silently dumped a real quarter of the
  population into one fake `zip3="000"` bucket, corrupting Track 2's k-anonymity numbers
  (an artificially huge "safe" bucket, with every affected patient's true ZIP3
  undercounted). `_zip3_from_city()` in `person_sources.py` now recovers a real ZIP3 from
  the city name (`ma_city_zip3.json`, sourced from real USPS/Census-derived city-ZIP
  facts, not guessed) whenever `postalCode` is missing or `"00000"`. A city this table
  has never seen still falls back to `"000"` — now with a loud stderr warning naming the
  city, not a silent corruption — since a new Synthea version or a different state will
  surface towns not yet in the table.
- **Track 3 diagnosis mapping is lossy by design.** Real conditions are open-vocabulary
  SNOMED; the harness's inference signatures cover nine diagnoses. `fhir-synthea` maps
  each condition to the nearest known diagnosis (keyword match, else a deterministic
  fallback) so the signature-based Track 3 still runs. True open-vocabulary inference is
  the LLM attacker's job, not the deterministic baseline's.
- Adding a source is a registry entry in `person_sources.py` — a `PersonSource` subclass
  whose `person(i, rng)` returns a dict carrying every key in `PERSON_KEYS`.

## Matching the Track 2 population to the sample

Track 2's linkage risk is only meaningful if the background population is drawn from the
**same distribution** as the sample. The population therefore uses the **same person
source** as the corpus by default: `--population-source` defaults to `--person-source`, so
`--person-source fhir-synthea --population 100000` draws the denominator from FHIR too, not
from `make_person`. Point `--population-fhir-dir` at a **larger, ideally disjoint** bundle
directory (your full background export) while `--fhir-dir` holds the released cohort:

```bash
python generate_corpus.py --person-source fhir-synthea --fhir-dir ./cohort \
    --population 100000 --population-source fhir-synthea --population-fhir-dir ./background \
    --inference --out corpus.json
```

A file-backed population is capped at the directory's supply (with a printed note), so a
real 100k-person denominator needs ~100k bundles — that volume is what actually retires
Track 2's uniform-ZIP3 upper bound. The synthetic population path is unchanged and
byte-identical when `--person-source` is left at the default.

This has actually been run against a real, non-fixture Synthea population — see
`RESULTS.md`'s "With a real Synthea-backed population" section for the real numbers
(22,754-person background, 0/50 population-unique) and the exact reproduction command.

## n2c2 — a different shape of "real data" (issue #126, Tier 2)

Everything above swaps out *where synthetic demographic fields come from*, feeding
`generate_corpus.py`'s segment-assembly pipeline. n2c2 is the opposite shape:
pre-existing, already-annotated real clinical narrative under DUA — nothing to
generate, an existing corpus to load. `n2c2_adapter.py` is that separate ingestion
path (not a `PersonSource`), reading n2c2/i2b2 2014-format XML into
`manifest-schema.md`'s `RECORD`/`SPAN` shape.

**Built without the corpus itself**, which is DUA-gated — scoped by a premortem run
before writing any code, from public sources only:

- **Confirmed**, cross-checked against a real working parser for this exact corpus
  (`xml_to_brat.py`, github.com/google/NeuroNER-CSPMC, read directly): a root element
  with one `<TEXT>` (the raw note) and one `<TAGS>` element whose children are one per
  PHI instance, each carrying `start`/`end`/`text`/`TYPE` — the child's own tag name is
  the top-level category, `TYPE` the subcategory. Independently confirmed subcategories
  for two categories only: `LOCATION` (`ROOM, DEPARTMENT, HOSPITAL, ORGANIZATION,
  STREET, CITY, STATE, COUNTRY, ZIP, OTHER`) and `ID` (`SOCIAL SECURITY NUMBER, MEDICAL
  RECORD NUMBER, HEALTH PLAN NUMBER, ACCOUNT NUMBER, LICENSE NUMBER, VEHICLE ID, DEVICE
  ID, BIOMETRIC ID, ID NUMBER`). Multiple sources agree on 6 top-level categories, 25
  subcategories, 28,872 total PHI instances in the real corpus.
- **Not confirmed**: `DATE`, `AGE`, `CONTACT`, `PROFESSION`, and most of `NAME`'s tag
  structure — the primary sources (the Stubbs & Uzuner 2015 paper, the n2c2 portal
  itself) are blocked by this environment's egress proxy; everything above came from
  secondary sources. `DATE` matters most: it's this harness's own documented
  highest-yield leakage category (`references/safe-harbor-identifiers.md`), and it's
  exactly the one left unverified. Also unconfirmed: how n2c2 encodes "these notes
  belong to the same patient" (the corpus is explicitly longitudinal — 1,304 notes
  across 296 patients — but no source found described the grouping convention).

`n2c2_adapter.py`'s `CATEGORY_MAP` covers only the confirmed entries above;
`map_category()` raises `UnmappedCategoryError` — loud, never a silent default — for
anything else, the same discipline `MA_CITY_ZIP3_OVERRIDES` and the inference
attacker's compliance gate already use. `identity_key` is a placeholder (the filename
stem) pending the patient-grouping question. Validated so far against a hand-built
fixture (`fixtures/n2c2/`) shaped to the confirmed schema — **not against real n2c2
files**. Per this project's own precedent, the Synthea FHIR reader was fixture-
validated first and still surfaced two real bugs only at real scale (see above) —
expect at least one more surprise here too, even for the categories already mapped.
Not wired into `generate_corpus.py`'s CLI yet; this is ingestion infrastructure to
build and test against now, not a ready-to-run corpus source.
