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

## What this slice builds (Tracks 1 and 2)

**Track 1 (Safe Harbor leakage)** is implemented **end to end** first, because it is the
only track that is deterministically scorable — you get a working closed loop with
trustworthy numbers before touching the harder tracks. **Track 2 (Expert Determination)**
is a statistical-linkage attacker that needs no LLM judge either, the second
load-bearing, model-independent scorer. **Track 3 (inference)** is now built too, with a
deterministic, model-independent baseline attacker so it also verifies offline; a real
LLM attacker is a documented swap-in that runs through the sibling agent-eval skill. All
three tracks share one corpus generator; each attack is a separate scorer.

The closed loops:

```
generate_corpus.py  ->  run_track1.py  ->  score_leakage.py     (Track 1: Safe Harbor)
   (synthetic notes       (defender runs        (deterministic Safe
    + ground-truth          over corpus)          Harbor coverage,
    manifest; --population                        sliced by cat×form×context)
    and --inference add
    Track 2 & 3 inputs)

generate_corpus.py --population N  ---->  score_reid.py          (Track 2: Expert Det.)
                                             (statistical linkage: k-anonymity +
                                              prosecutor/journalist/marketer risk)

generate_corpus.py --inference  ------->  score_inference.py     (Track 3: inference)
   (diagnosis-free vignettes)                (attacker recovers the withheld
                                              diagnosis; emits agent-eval JSONL)
```

## Running it

```bash
cd scripts
# Track 1 — Safe Harbor leakage
python generate_corpus.py --n 50 --seed 20260101 --population 100000 --inference --utility --out corpus.json
python run_track1.py --corpus corpus.json --pipeline regex-baseline-v0 --out runs.jsonl
python score_leakage.py --runs runs.jsonl --out leakage_report.json

# Track 2 — Expert Determination re-id risk (reads population_ref from the corpus)
python score_reid.py --corpus corpus.json --out reid_report.json

# Track 3 — free-text inference (attacker recovers the withheld diagnosis)
python score_inference.py --corpus corpus.json --out inference_report.json --eval-out inference_eval.jsonl
python ../../agent-eval/scripts/score_eval.py inference_eval.jsonl   # optional: aggregate/diff

# Utility axis + privacy-utility frontier (needs a --utility corpus for clinical spans)
python score_utility.py --corpus corpus.json --runs runs.jsonl --leakage-report leakage_report.json
python score_frontier.py --corpus corpus.json --out frontier.json   # (privacy, utility) per defender

# Cross-track synthesis — the punchline (needs --population AND --inference)
python score_crosstrack.py --corpus corpus.json --out crosstrack_report.json

# Statistical rigor — 95% CIs + paired significance test on ONE corpus
python score_stats.py --corpus corpus.json --out stats.json
# ...or a seed sweep across K independently generated corpora
python score_stats.py --seeds 10 --n 50 --out stats_sweep.json

# Regression suite — invariants + verified headline numbers (stdlib only, ~6s)
python test_harness.py
```

`test_harness.py` locks the load-bearing invariants (byte-identical corpus across flags,
span offset self-tests, redacted-span structure, utility-overlap logic, cross-track ≡
standalone-scorer consistency) and the verified numbers (0.449 baseline, the two frontier
corners, 0.94 recovery, 49/50 cross-track). A failure means a result moved — run it before
committing changes to the generator or any scorer.

**Statistical rigor.** Every number above is a point estimate on n=50, one seed —
`score_stats.py` puts a 95% confidence interval on each of them (a **cluster bootstrap
over records**, since a note's spans are correlated, not independent evidence — see
`references/statistical-rigor.md`), and a **paired** bootstrap test of whether two
defenders' privacy/utility genuinely differ on the same records. On the bundled
defenders, both frontier gaps are significant at α=0.05. A `--seeds K` sweep regenerates
the corpus across K independent seeds to confirm a result isn't an artifact of one draw:
over-redact's privacy (0.886–0.900 across 5 seeds) never overlaps the baseline's
(0.417–0.456).

**Track 1.** The bundled `regex-baseline-v0` defender is deliberately weak. On the
default corpus it scores ~0.45 Safe Harbor coverage, and the breakdown is the point: ISO
dates are caught 100%, but spelled-out and narrative dates 0%, names 0%, and the
patient's city and facility code 0%. That is the `category × surface_form × context`
diagnostic working as intended — a single averaged number would have hidden exactly the
gaps that matter. The report also lists `categories_not_exercised` — the Safe Harbor
categories the corpus never injects (fax, URL, IP, device IDs, …) — so a coverage number
over 8 categories is never mistaken for coverage of the full 18-item checklist.

**Track 2.** Against a 100k-person background population, every one of the 50 notes is
unique on its generalized QIs (age band, sex, ZIP3, rare-diagnosis flag) — so the
released cohort is **not k-anonymous at any k ≥ 2** (min_k = 1), prosecutor risk is 1.0,
and **6 of 50 records are unique even against the full 100,000-person population** (mean
journalist/marketer risk ≈ 0.37). Read against Track 1, that is the entire thesis in one
run: a note whose 18 direct identifiers are perfectly scrubbed (Safe Harbor pass) is
still a re-identification target through the QIs that must clinically remain (Expert
Determination fail). One caveat the report states in-band: the v0 generator draws ZIP3
uniformly, so the QI space is more dispersed than real geography — the prosecutor /
k-anonymity numbers are an upper bound, and the population-side risks are the
load-bearing figures. A Synthea-backed population restores realistic class sizes.

**Track 3.** Each note gets a diagnosis-free vignette rendering the diagnosis's clinical
signature; the attacker must name the withheld diagnosis from context alone. The bundled
knowledge-only `signature-match-v0` attacker recovers it in **~0.94 of cases** — **1.0
for rare diagnoses** (distinctive signatures leak almost perfectly), 0.93 for common
ones — while correctly abstaining on the ~6% of vignettes reduced to non-specific
features. These notes carry no direct identifiers (Safe Harbor would pass them) and are
not being linked (Track 2's job); the leak is pure inference, which neither other track
can see. Scoring is programmatic against the closed diagnosis set, and the scorer emits
agent-eval's JSONL schema so a real LLM attacker's free-text guesses can later be graded
and calibrated through the agent-eval skill.

**Utility axis / frontier.** All three tracks measure privacy; the utility axis measures
what a defender *destroys*. `--utility` marks the clinical spans a good scrubber must keep
(diagnosis, age ≤ 89, sex — clinically necessary, non-identifying), and `score_frontier.py`
gives each defender a `(privacy, utility)` pair. The two bundled defenders sit at opposite
corners — `regex-baseline-v0` at **privacy 0.449 / utility 1.00** (barely redacts) and
`over-redact-v0` at **privacy 0.900 / utility 0.60** (sweeps up names and dates but deletes
every age and the capitalized diagnoses). Neither dominates; the gap between them is the
frontier, and the whole reason a privacy score is never reported alone.

**Cross-track synthesis — the punchline.** `score_crosstrack.py` joins the tracks per
record to compute the claim the whole harness exists to make. It assumes the best case for
the defender — a *perfect* Safe Harbor scrub, all 18 direct-identifier categories gone —
then asks of each record: still re-identifiable (Track 2, population k < 5) or still
inferable (Track 3, diagnosis recovered)? On the default corpus, **49 of 50 records (98%)
are re-identifiable or inferable despite being perfectly Safe-Harbor-clean** (34 re-id, 47
inferable, 32 both; rare diagnoses 6/6). Those are checklist false-negatives: certified
de-identified, and not. It reuses Track 2's linkage and Track 3's attacker directly (per-
record outputs verified identical to the standalone scorers), so it is deterministic and
adds no new assumptions.

## Architecture — swappable parts + your eval harness as orchestrator

**Corpus generator** (`generate_corpus.py`). Emits synthetic notes and a manifest whose
spans are **exact by construction**: notes are assembled from segments and each
identifier's offset is recorded at placement time, then a self-test asserts
`note_text[start:end] == text` for every span. This is why Track 1 needs no tagger and
no judge. The **person source** is a registry (`person_sources.py`): `synthetic-v0`
(default, `make_person`, byte-identical) or `fhir-synthea`, which reads Synthea FHIR R4
bundles so real demographics flow through the same injection/manifest machinery
(`--person-source fhir-synthea --fhir-dir …`; see `references/data-sources.md`). The
**surface-form layer** renders each logical identifier several realistic ways (a date
as ISO, slashed, spelled-out, or narrative; a name as initials; identifiers in headers,
signatures, and buried in narrative), because real notes leak precisely where they are
messy.

**De-id pipeline / defender** (`deid_pipelines.py`). A pluggable interface
(`scrub(text) -> (scrubbed_text, redacted_spans_or_None)`) with a registry, so the same
eval runs against a regex baseline, a rule-based scrubber, an LLM scrubber, and a
hybrid — comparing pipelines on one frontier is the deliverable. Two ship: the weak
`regex-baseline-v0` (under-redacts) and `over-redact-v0` (over-redacts), the two corners
of the frontier. A pipeline that reports its redaction spans gets exact scoring; a
black-box pipeline returns `None` and the scorers fall back to surface-string presence
checks.

**Re-id attackers / scorers** (`score_leakage.py` = Track 1; `score_reid.py` = Track 2;
`score_inference.py` + `inference_attackers.py` = Track 3). For Track 1 the attacker is
deterministic: did each ground-truth span survive? Output is coverage sliced by category,
by category×surface-form, and by context — never averaged into one figure. For Track 2
the attacker is statistical: over the generalized QI equivalence classes it computes
k-anonymity and prosecutor/journalist/marketer risk against the background population
(`qi_model.py` holds the one generalization both the population generator and the scorer
share). For Track 3 the attacker is a swappable inference engine (registry in
`inference_attackers.py`, like the defenders) that recovers a withheld attribute from
context; the bundled baseline is model-independent, and a real LLM attacker is the
documented swap-in. All three bundled attackers are model-independent, so none can share
a base model with the defender — the harness's cardinal rule holds by construction.

**Utility scorer + frontier** (`score_utility.py`, `score_frontier.py`). The mirror of the
Track 1 attacker: instead of "did an identifier survive?" it asks "did clinical content
survive?" over the `--utility` clinical spans, then `score_frontier.py` pairs each
defender's privacy and utility into the frontier point. Deterministic, so it never depends
on a model's blind spots.

**Orchestrator** (`run_track1.py`, and later your existing **agent-eval** harness).
agent-eval is where the adversarial-eval and judge-calibration machinery plug in: it
orchestrates runs, calibrates the inference-track judge and the attacker's confidence
(does a claimed-0.9 re-identification mean 90% correct against ground truth?), and diffs
across runs so hardening the scrubber shows up as movement on the frontier.

## Two rules that keep the harness honest

**Always pair privacy with utility.** A scrubber that redacts every token has perfect
privacy and zero clinical value — so any privacy number reported without a paired
utility metric is meaningless. This is now enforced in code: `--utility` marks the
clinical spans a scrubber must keep, `score_utility.py` measures preservation, and
`score_frontier.py` plots each defender as a `(privacy, utility)` point. The real
deliverable is that **frontier** across pipelines, not a leaderboard; over-redaction is a
measured cost (`over-redact-v0` buys privacy 0.45→0.90 by dropping utility 1.0→0.60), not
a safe default.

**Never let attacker and defender share a base model.** If both are the same LLM their
blind spots correlate — the attacker misses exactly what the defender missed and the
score looks great and means nothing. Use diverse model families, and keep the
model-independent attackers (deterministic manifest check for Track 1; statistical
linkage for Track 2; knowledge-only signature match for Track 3) load-bearing so the
score never depends on one model's blind spots.

## Extending the harness

- **Statistical rigor — BUILT.** `bootstrap.py` + `score_stats.py` add cluster-bootstrap
  CIs and a paired significance test to every headline metric, plus a seed-sweep mode.
  Extend it toward a real study by growing n well past 50 and reporting effect sizes
  alongside CIs, not just significance. See `references/statistical-rigor.md`.
- **Cross-track synthesis — BUILT.** `score_crosstrack.py` reports how many records pass
  Safe Harbor yet stay re-identifiable or inferable. Extend it when a live LLM attacker
  lands (its recoveries flow straight in) or when Track 2 gets a Synthea population (the
  re-id axis stops being an upper bound).
- **Track 3 (inference) — BUILT (baseline).** `score_inference.py` runs an attacker over
  the `--inference` vignettes and scores recovery of the withheld diagnosis, emitting
  agent-eval JSONL. The bundled `signature-match-v0` is model-independent. **The real
  next step here is the LLM attacker + judge:** register an LLM attacker in
  `inference_attackers.py` (respecting the no-shared-base-model rule) and move scoring to
  agent-eval's LLM judge for semantic grading of free-text guesses and confidence
  calibration. See `references/inference-threat.md`.
- **Utility axis / frontier — BUILT.** `--utility` marks clinical spans, `score_utility.py`
  scores preservation, and `score_frontier.py` reports `(privacy, utility)` per defender
  over one corpus. Extend it by adding richer clinical content (labs, meds) as clinical
  spans and more defenders to the registry to fill in the frontier between the two
  bundled corners.
- **Data fidelity — BUILT (reader), pending real data.** The `fhir-synthea` person source
  reads Synthea FHIR bundles so real demographics enter at the source — this is what turns
  Track 2's uniform-ZIP3 upper bound into a defensible estimate. Validated against a
  bundled fixture; point it at real Synthea output and sanity-check the first run. Still
  open: a FHIR-sourced Track 2 population, and richer clinical content (labs, meds) for the
  utility axis.

## Reference files

- `references/manifest-schema.md` — the frozen data contract. Read before changing the
  generator or writing any scorer; every scorer depends on it.
- `references/safe-harbor-identifiers.md` — the 18 identifier categories as the Track 1
  test taxonomy, and where Safe Harbor and Expert Determination diverge.
- `references/expert-determination.md` — the Track 2 risk model: quasi-identifiers,
  equivalence classes, k-anonymity, and the three attackers (prosecutor/journalist/
  marketer). Read before changing `qi_model.py` or `score_reid.py`.
- `references/inference-threat.md` — the Track 3 contract: inference vs leakage, the
  withheld-target invariant, programmatic-vs-judged scoring, and how the inference
  attacker plugs into agent-eval. Read before changing the inference generator or scorer.
- `references/utility-and-frontier.md` — the utility axis: clinical spans as the mirror of
  identifier spans, how preservation is scored, and how the privacy-utility frontier is
  assembled across defenders. Read before changing `score_utility.py` or `score_frontier.py`.
- `references/data-sources.md` — the person-source registry and the Synthea FHIR → person
  mapping contract. Read before changing `person_sources.py` or feeding real Synthea data.
- `references/statistical-rigor.md` — why the bootstrap resamples records (not spans), the
  bootstrap vs. seed-sweep distinction, and the paired significance test. Read before
  changing `bootstrap.py` or `score_stats.py`, or before citing a number in a writeup.
- `references/expert-determination.md` — the Track 2 risk model: quasi-identifiers,
  equivalence classes, k-anonymity, and the three attackers (prosecutor/journalist/
  marketer). Read before changing `qi_model.py` or `score_reid.py`.

## Standard traceability

Track 1 coverage is reported per Safe Harbor identifier category, so every test maps to
the specific identifier it exercises — the standard-anchored coverage matrix reviewers
and portfolio readers expect. Label Track 1 results as **Safe Harbor coverage**, never
as re-identification risk; conflating them is the exact error this harness exists to
expose.
