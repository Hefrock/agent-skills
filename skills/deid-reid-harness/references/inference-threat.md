# Inference — the Track 3 threat

Tracks 1 and 2 both assume the identifying information is *present* in the note: Safe
Harbor removes direct identifiers that appear as strings; Expert Determination counts
quasi-identifiers that appear as fields. **Inference** is the threat neither can see —
a model *derives* a sensitive attribute or an identity from clinical context that
**never states it**. A note with every direct identifier scrubbed and no obviously rare
quasi-identifier can still describe exactly one plausible patient, because the medicine
itself is identifying.

Track 3 makes this measurable on the one attribute we can hold as ground truth in a
synthetic corpus: the **diagnosis**. Each record gets an *inference vignette* — the
diagnosis's clinical signature (an anchor finding plus a random subset of supporting
features) rendered as prose, with the diagnosis name withheld. The attacker reads the
vignette and names the diagnosis; we score recovery against the manifest.

## Inference vs leakage — the invariant

If the diagnosis appeared in the vignette, Track 3 would be measuring *leakage* (Track
1's job), not *inference*. So the generator enforces an invariant, checked at generation
time by `inference_self_test`:

> the withheld `target_value` is never a substring of its own `inference_case.note`.

This is the Track 3 analogue of Track 1's offset invariant — a machine-checked guarantee
that the thing being scored is the thing the track claims to measure.

## Why the corpus flag is RNG-isolated

`--inference` attaches an `inference_case` to every record using a **disjoint derived
RNG** (`seed + 2`), so `note_text`, `identifiers`, and `quasi_identifiers` are
byte-identical with or without the flag. Tracks 1 and 2 run unchanged on an inference
corpus (they ignore the extra field); the deterministic ~0.45 Safe Harbor number does
not move. Enabling one track never perturbs another.

## Scoring — programmatic now, judged later

The diagnosis is drawn from a **closed synthetic set**, so a normalized string match
against ground truth is exact and needs no LLM. That is deliberate: it keeps Track 3
**verifiable offline**, the same property that makes Tracks 1-2 trustworthy. The scorer
still emits per-case rows in **agent-eval's JSONL schema**
(`{"id","score","category","rationale"}`), so `agent-eval/scripts/score_eval.py`
aggregates, thresholds, and diffs Track 3 runs like any other eval.

When you swap in a real LLM attacker whose guesses are free text ("a lysosomal storage
disorder" for Fabry), exact match breaks — that is when you move scoring to agent-eval's
**LLM judge** for semantic grading, and lean on its **calibration** to answer the
question the deterministic path only stubs out: *does a claimed 0.9 confidence mean 90%
correct?* The report already produces a confidence-vs-accuracy calibration table so the
machinery is exercised from day one.

## What the numbers say

Report fields (`score_inference.py --out`):

- `overall_recovery` — share of vignettes whose withheld diagnosis the attacker named.
- `by_class` — **rare vs common**. Rare diagnoses recover at ~1.0: distinctive
  signatures leak almost perfectly. This is the same rare-diagnosis signal Track 2 flags
  as a quasi-identifier, now seen through inference.
- `by_diagnosis` — per-diagnosis recovery, never averaged away.
- `abstain_rate` — the attacker returns `None` rather than guess when only shared,
  non-specific features survive; the anchor is dropped ~30% of the time precisely to
  create these genuinely ambiguous cases, so recovery is < 100% and meaningful.
- `calibration` — confidence buckets vs actual accuracy.

The deliverable is not the number but the divergence: a note that **passes Safe Harbor**
(no direct identifiers) still leaks a sensitive attribute through inference. The
checklist cannot see it, and the linkage track cannot see it; only an attacker can.

## The attacker is swappable — and constrained

`inference_attackers.py` is a registry, like the de-id defenders. The bundled
`signature-match-v0` is model-independent (knowledge-only keyword matching) so the loop
closes and the threat is demonstrated with zero API access. A real LLM attacker is a
strictly stronger swap-in — register it and run it through agent-eval. The harness's
cardinal rule still binds: **an LLM attacker must not share a base model with any LLM
defender, nor with the judge that grades it**, or their blind spots correlate and the
score means nothing. The deterministic attacker and the programmatic scorer stay
load-bearing so no single model's blind spot can quietly flatter the result.

### The DUA constraint is enforced in code, not just written here

Calling any third-party LLM API on DUA-governed clinical text is, per how academic
clinical-NLP DUAs are standardly written, almost certainly a prohibited third-party
disclosure (see `references/data-sources.md`'s "before use" notes on n2c2/MIMIC-IV).
Before issue #126's Tier 1, that restriction lived only as a sentence in issue #29 —
nothing stopped a Track 3 real-attacker run from calling an external API against real
data. Every `InferenceAttacker` subclass now declares `calls_external_api` (`True` for
anything that makes a network call, `False` for the bundled baseline); `get_attacker()`
refuses to return an attacker whose class sets it `True` unless the caller explicitly
passes `acknowledge_external_api=True` (`score_inference.py --acknowledge-external-api-risk`
on the CLI) — a technical gate against an *accidental* real-data run, not a substitute
for the human compliance review Tier 1 also requires before any DUA-governed corpus is
used at all. Registering a new LLM attacker means setting `calls_external_api = True`
on it; there is no default a new subclass can silently inherit its way around.
