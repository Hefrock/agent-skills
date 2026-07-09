# CONTINUATION — dev handoff for Claude Code

You are picking up a partially-built agent skill. This note is the state-of-work brief:
what exists, what's verified, the decisions already made (don't relitigate them without
reason), and the next build. The skill's own operating instructions live in `SKILL.md` —
read that too, but this file is the provenance and roadmap.

## Where this belongs

This is a skill for the user's **agent-skills** repo (GitHub: **Hefrock/agent-skills**),
built on the open Agent Skills standard, alongside existing skills (wiki-operator,
wiki-synthesizer, wiki-librarian, agent-eval). Place `deid-reid-harness/` as a sibling
skill. It is designed to use the existing **agent-eval** skill as its orchestrator/judge
layer once Track 3 is built — wire into that rather than duplicating eval machinery.

## What this skill is (one sentence)

An adversarial de-identification ⟷ re-identification eval harness: a de-identifier's
real metric is its survival against the best re-id attacker, so **the attacker is the
eval**. Three attack tracks, three standards, scored separately — never one averaged
number.

## State of work

**Track 1 (Safe Harbor leakage) — BUILT and VERIFIED end to end.** The loop runs:

```bash
cd scripts
python generate_corpus.py --n 50 --seed 20260101 --population 100000 --out corpus.json
python run_track1.py --corpus corpus.json --pipeline regex-baseline-v0 --out runs.jsonl
python score_leakage.py --runs runs.jsonl --out leakage_report.json
```

Verified output: the bundled deliberately-weak `regex-baseline-v0` defender scores
~0.45 Safe Harbor coverage (247/550 spans) on the default corpus. The breakdown is the
point — ISO dates caught 100%, spelled/slashed/narrative dates 0%, names 0%, city and
facility code 0%. That `category × surface_form × context` diagnostic is the whole
thesis; a single averaged score would hide it. The report also emits
`categories_not_exercised` so the number is never read as coverage of all 18 categories
when the corpus only injects 8.

**Track 2 (Expert Determination) — BUILT and VERIFIED.** `--population N` on the
generator emits a background population (`population.jsonl`, sets `population_ref`); the
new `score_reid.py` reads it and reports k-anonymity + prosecutor/journalist/marketer
risk over the generalized QI set in `qi_model.py`:

```bash
python score_reid.py --corpus corpus.json --out reid_report.json
```

Verified output (n=50, population 100k): the released cohort is not k-anonymous (min_k=1,
prosecutor risk 1.0) and **6/50 records are unique against the full 100k population**
(mean journalist/marketer risk ≈ 0.37). That is the divergence made concrete: a note
that passes Safe Harbor is still an Expert Determination failure through QIs that must
clinically remain. Settled build decisions (confirmed with the user): population size
100k, generalized QIs (age→5-yr bands 90+ capped; sex/zip3/rare_diagnosis raw), verdict
anchored on k≥5 with all three risks reported. Known caveat (stated in the report's
`caveats` field): v0's uniform ZIP3 inflates sample uniqueness, so prosecutor/k-anonymity
are an upper bound and the population-side risks are load-bearing.

**Track 3 — DESIGNED, NOT BUILT.** The manifest already carries its inputs
(`identity_key`, the `quasi_identifiers` profile), so no corpus regeneration is needed.

## Decisions already made (treat as settled unless the user reopens them)

1. **Deterministic track first.** Track 1 is scored with no LLM judge — the generator
   injects every identifier and a self-test enforces `note_text[start:end] == text` on
   every span, so leakage is a yes/no fact. This is why Track 1 shipped first.
2. **Manifest schema is frozen (v1).** See `references/manifest-schema.md`. Every scorer
   depends on it. Don't change span/offset semantics without updating all scorers.
3. **Population and risk model — decided for v0.** `population_ref` is now populated by
   `--population N`. Size 100k, generalized QIs, k≥5 verdict with prosecutor/journalist/
   marketer all reported (see `references/expert-determination.md`). Population *fidelity*
   is still deferred: the v0 denominator is synthetic with uniform ZIP3; a Synthea-backed
   population (the `make_person` swap) is the realism upgrade.

## Two invariants that keep the harness honest (do not violate)

- **Privacy is never reported without a paired utility metric.** A scrubber that redacts
  everything has perfect privacy and zero value. The deliverable is a privacy-utility
  frontier, not a leaderboard. Track 1 currently measures only the privacy axis; adding
  a utility scorer (does the scrubbed note still support NER / phenotype extraction / a
  CDS trigger?) is a legitimate parallel next step.
- **Attacker and defender must never share a base model.** Correlated blind spots =
  falsely optimistic scores. Keep the model-independent attackers (Track 1's
  manifest check; Track 2's statistical linkage) load-bearing.

## Swap points (already designed for extension)

- **Real data:** `make_person()` in `generate_corpus.py` is the ONLY function to replace
  to drop in a Synthea driver (the user has fhir-synthea-lab). Injection + manifest
  machinery stay put.
- **Defender:** registry in `deid_pipelines.py`. Add rule-based / LLM / hybrid scrubbers
  as new `DeidPipeline` subclasses. Pipelines that return redaction spans get exact
  scoring; black-box ones return `None` and the scorer falls back to presence checks.

## Recommended next build (in order)

1. **Utility scorer.** Both tracks now measure privacy only. Add the second axis (does
   the scrubbed note still support NER / phenotype extraction / a CDS trigger?) so results
   become the privacy-utility frontier, not a leaderboard. Smallest, highest-leverage
   next step.
2. **Track 3 (inference).** LLM attacker deriving `identity_key` / sensitive attributes
   from scrubbed text, orchestrated and calibrated through the existing agent-eval skill.
3. **Population fidelity.** Swap `make_person` for a Synthea driver so both the corpus and
   the Track 2 population get realistic geography — this is what turns Track 2's
   uniform-ZIP3 upper bound into a defensible risk estimate.

## Known limitations to keep visible

- Synthetic notes are structurally cleaner than real clinical text (no copy-forward
  bloat, OCR garble, or PHI in fax headers). The surface-form layer narrows this gap; it
  doesn't close it. Any scrubber that scores well here still needs proving on messy
  input.
- The fallback presence-check scorer is approximate even with its per-literal
  occurrence budget (a surviving coincidental substring still counts against the
  budget). Prefer span-reporting defenders so scoring stays exact.
