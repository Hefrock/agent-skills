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

**Track 3 (inference) — BUILT (deterministic baseline).** `--inference` attaches a
diagnosis-free vignette per record (`inference_case`), rendered from a per-diagnosis
clinical signature with the diagnosis name withheld and enforced absent by
`inference_self_test`. `inference_attackers.py` is a registry (like the defenders) with a
model-independent `signature-match-v0` baseline; `score_inference.py` scores recovery of
the withheld diagnosis and emits agent-eval JSONL:

```bash
python generate_corpus.py --n 50 --seed 20260101 --inference --out corpus.json
python score_inference.py --corpus corpus.json --out inference_report.json --eval-out inference_eval.jsonl
python ../../agent-eval/scripts/score_eval.py inference_eval.jsonl   # optional aggregate/diff
```

Verified output (n=50): overall recovery ~0.94 (rare 1.0, common 0.93, abstain 0.06).
Vignettes carry no direct identifiers (Safe Harbor passes them) yet the signature leaks
the diagnosis — pure inference. Settled build decisions (recommended defaults; the
AskUserQuestion prompt was interrupted so they were confirmed by the user's "continue"):
inference-proper via signatures; deterministic programmatic scoring on the closed
diagnosis set + documented agent-eval judge path; interface+mock, no live LLM calls
(reversible — a live attacker is just a new registry entry). RNG-isolated (`seed+2`) so
Tracks 1/2 stay byte-identical (verified: Track 1 still 0.449 on an --inference corpus).

**The real Track 3 next step is the LLM attacker + judge** (see below) — the baseline
proves the loop and the threat; a live model is strictly stronger and needs agent-eval's
semantic judge + calibration.

**Utility axis + privacy-utility frontier — BUILT and VERIFIED.** `--utility` marks
clinical spans that must SURVIVE scrubbing (diagnosis, age ≤ 89, sex — the mirror of
identifier spans), in place over the note so the corpus stays byte-identical on the
Track 1/2/3 fields (verified with all flags on). `score_utility.py` scores preservation;
`score_frontier.py` reports `(privacy, utility)` per defender. A second defender,
`over-redact-v0`, was added so the frontier has two contrasting corners:

```bash
python generate_corpus.py --n 50 --seed 20260101 --utility --out corpus.json
python score_frontier.py --corpus corpus.json --out frontier.json
```

Verified frontier (n=50): `regex-baseline-v0` at privacy 0.449 / utility 1.00 (under-
redacts), `over-redact-v0` at privacy 0.900 / utility 0.60 (catches names+dates but
deletes every age and the capitalized diagnoses; age utility 0/50, diagnosis 40/50, sex
50/50). Neither dominates — the gap is the frontier. This closes the harness's own
cardinal gap: privacy was previously reported with no paired utility. (over-redact merges
redactions separated only by separators, so a fully-redacted multi-token name counts as
covered under Track 1's single-span rule rather than scoring as leaked.)

**Cross-track synthesis — BUILT and VERIFIED.** `score_crosstrack.py` joins the tracks
per record to compute the harness's punchline: assume a PERFECT Safe Harbor scrub (all 18
direct-identifier categories gone), then count how many records stay re-identifiable
(Track 2, population k < 5) or inferable (Track 3, diagnosis recovered). It reuses Track
2's linkage and Track 3's attacker directly — per-record k_population and inferable were
verified identical (0 mismatches) to the standalone `score_reid`/`score_inference`, so it
introduces no new logic or assumptions. Needs a corpus with BOTH --population and
--inference.

```bash
python generate_corpus.py --n 50 --seed 20260101 --population 100000 --inference --out corpus.json
python score_crosstrack.py --corpus corpus.json --out crosstrack_report.json
```

Verified (n=50): 550 direct identifiers removed, yet 49/50 records (98%) are re-
identifiable or inferable despite being Safe-Harbor-clean (34 re-id, 47 inferable, 32
both; rare 6/6, common 43/44). This IS the thesis — "a Safe Harbor pass can still be an
Expert Determination failure / inference leak" — made a single number.

**Person source / Synthea reader — BUILT (reader), pending real data.** `make_person` is
now one source behind a registry (`person_sources.py`): `synthetic-v0` (default, byte-
identical) and `fhir-synthea`, which reads Synthea FHIR R4 bundles and maps each Patient
to the person dict the generator consumes (`--person-source fhir-synthea --fhir-dir …`).
Real demographics flow through the unchanged injection/manifest/scoring machinery — the
fix for Track 2's uniform-ZIP3 upper bound, once fed real data. Validated end-to-end
against a bundled Synthea-structured fixture (`fixtures/fhir/`); the reader has NOT been
run against a live Synthea export in this environment, so the first real run should be
sanity-checked. Open follow-ups: a FHIR-sourced Track 2 population (denominator is still
synthetic) and mapping open-vocabulary conditions for Track 3 (currently mapped to the
nearest known signature diagnosis). See `references/data-sources.md`.

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
  frontier, not a leaderboard. NOW ENFORCED: `--utility` + `score_utility.py` +
  `score_frontier.py` give every defender a paired (privacy, utility) point. Keep it that
  way — don't report a track's privacy number in isolation.
- **Attacker and defender must never share a base model.** Correlated blind spots =
  falsely optimistic scores. Keep the model-independent attackers (Track 1's manifest
  check; Track 2's statistical linkage; Track 3's signature-match baseline) load-bearing.
  When an LLM attacker is added it must also differ from the judge that grades it.

## Swap points (already designed for extension)

- **Real data:** DONE via the person-source registry (`person_sources.py`) — `make_person`
  is `synthetic-v0`; `fhir-synthea` reads Synthea FHIR bundles. Injection + manifest
  machinery unchanged. Remaining: FHIR-sourced Track 2 population; the corpus sample now
  upgrades directly.
- **Defender:** registry in `deid_pipelines.py`. Add rule-based / LLM / hybrid scrubbers
  as new `DeidPipeline` subclasses. Pipelines that return redaction spans get exact
  scoring; black-box ones return `None` and the scorer falls back to presence checks.
- **Inference attacker:** registry in `inference_attackers.py`. Add an LLM attacker as a
  new `InferenceAttacker` subclass returning `{guess, confidence, rationale}`; move
  scoring to agent-eval's judge for free-text guesses.
- **Defender for the frontier:** add more `DeidPipeline` subclasses to fill in the frontier
  between the `regex-baseline-v0` (under-redact) and `over-redact-v0` (over-redact) corners.

## Recommended next build (in order)

Everything below is enhancement, not a gap — the three tracks + utility frontier + cross-
track synthesis are complete and verified, and a regression suite now guards them. Split
by whether it can be verified offline:

0. **Regression suite — BUILT.** `test_harness.py` (stdlib unittest, ~3s, 13 tests) locks
   the invariants (byte-identical corpus across flags, span offsets, redacted-span
   structure, utility overlap, cross-track ≡ standalone scorers) and the headline numbers
   (0.449 baseline, frontier corners, 0.94 recovery, 49/50 cross-track). Run it before
   committing generator/scorer changes; if a number moved on purpose, update it in the
   same commit. (Now also covers the FHIR person source: parsing, field mapping, full
   pipeline on a FHIR corpus — 18 tests total.)
1. **Feed real Synthea data through `fhir-synthea` (offline once you have the data).** The
   reader is built and fixture-tested; point `--fhir-dir` at real fhir-synthea-lab output,
   sanity-check the mapping, then add a FHIR-sourced Track 2 population so the denominator
   matches the sample. This is what actually retires Track 2's uniform-ZIP3 upper bound.
2. **Track 3 LLM attacker + judge (needs live API — this dev sandbox had none).** Register
   an LLM attacker in `inference_attackers.py`, move scoring to agent-eval's LLM judge for
   semantic grading + confidence calibration. Its recoveries flow straight into the cross-
   track report. This is where agent-eval becomes load-bearing rather than optional, and
   where the no-shared-base-model rule finally gets exercised for real. Also unlocks
   open-vocabulary Track 3 (no more mapping real conditions to the nearest signature).
3. **A better defender.** A rule-based or hybrid scrubber that pushes up-and-right of both
   bundled corners — the frontier now exists to prove it did.

## Known limitations to keep visible

- Synthetic notes are structurally cleaner than real clinical text (no copy-forward
  bloat, OCR garble, or PHI in fax headers). The surface-form layer narrows this gap; it
  doesn't close it. Any scrubber that scores well here still needs proving on messy
  input.
- The fallback presence-check scorer is approximate even with its per-literal
  occurrence budget (a surviving coincidental substring still counts against the
  budget). Prefer span-reporting defenders so scoring stays exact.
- Track 3's inference is idealized: each diagnosis has one clean signature and the
  baseline attacker shares that vocabulary, so recovery (~0.94) is an upper-ish bound on
  what a *keyword* attacker manages, and a lower bound on what a strong LLM would do from
  subtler context. Real inference draws on comorbidities, meds, temporal patterns, and
  writing style the signatures don't model. The LLM-attacker swap is what closes this.
