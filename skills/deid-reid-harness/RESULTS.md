# Sample Results — de-id ⟷ re-id harness

A verified run so you can see what the harness produces without cloning and
running it. Every number here is reproducible:

```bash
cd scripts
python generate_corpus.py --n 50 --seed 20260101 --population 100000 --inference --utility --out corpus.json
python run_track1.py  --corpus corpus.json --pipeline regex-baseline-v0 --out runs.jsonl
python score_leakage.py   --runs runs.jsonl --out leakage_report.json
python score_reid.py      --corpus corpus.json --out reid_report.json
python score_inference.py --corpus corpus.json --out inference_report.json --eval-out inference_eval.jsonl
python score_frontier.py  --corpus corpus.json --out frontier.json
python score_crosstrack.py --corpus corpus.json --out crosstrack_report.json
python score_stats.py     --corpus corpus.json --out stats.json      # 95% bootstrap CIs
```

Corpus: 50 synthetic clinical notes, seed `20260101`, 100k-person background
population. 95% CIs are a cluster bootstrap over records (n_boot=2000).

---

## The headline

**49 of 50 records (98%, 95% CI 94–100%) remain vulnerable even after a *perfect*
Safe Harbor scrub** — i.e. after all 18 direct-identifier categories are removed.

That is the entire thesis in one number: a checklist-clean note is not a safe note.
The vulnerability splits into two attacks the checklist cannot see:

| Despite a perfect Safe Harbor scrub… | Records | Fraction |
|---|---:|---:|
| …re-identifiable via quasi-identifiers (Track 2) | 34 / 50 | 0.68 |
| …diagnosis inferable from context (Track 3) | 47 / 50 | 0.94 |
| …vulnerable on **either** axis | 49 / 50 | **0.98** |
| …vulnerable on **both** axes | 32 / 50 | 0.64 |

Rare-diagnosis records: 6/6 (100%) vulnerable. Common: 43/44 (97.7%).

---

## The privacy–utility frontier

A de-identifier's privacy score means nothing without what it destroys to get there.
Each defender is one `(privacy, utility)` point. The two bundled defenders sit at
**opposite corners — neither dominates.** That gap *is* the frontier.

```
 utility
 (clinical    1.00 ┤ ● regex-baseline-v0
  spans                 (0.449, 1.00)
  kept)      0.90 ┤
             0.80 ┤
             0.70 ┤
             0.60 ┤                            ● over-redact-v0
             0.50 ┤                                 (0.900, 0.60)
                  └────┬────┬────┬────┬────┬────┬──
                      0.40 0.50 0.60 0.70 0.80 0.90
                        privacy  (Safe Harbor coverage) →
```

| Defender | Privacy (Safe Harbor) | Utility (clinical preservation) |
|---|---|---|
| `regex-baseline-v0` | **0.449**  [0.429 – 0.469] | **1.00**  [1.00 – 1.00] |
| `over-redact-v0` | **0.900**  [0.876 – 0.922] | **0.60**  [0.56 – 0.63] |

Both gaps are statistically significant at α=0.05 (paired bootstrap, p ≈ 0):
the privacy difference is −0.451 [−0.476, −0.424]; the utility difference is
+0.40 [+0.367, +0.44]. `over-redact-v0` buys +0.45 privacy by destroying 0.40 of
clinical utility — it sweeps up names and dates but deletes every age and the
capitalized diagnoses.

---

## Why a single privacy number lies — Track 1 breakdown

`regex-baseline-v0` scores 0.449 overall. Averaged, that looks like "about half."
Sliced by identifier category, it's a very specific failure profile:

| Category | Coverage | | Category | Coverage |
|---|---:|---|---|---:|
| email | 1.00 | | phone/fax | 0.40 |
| mrn | 1.00 | | date | 0.27 |
| ssn | 1.00 | | **name** | **0.00** |
| geo_subdivision | 0.50 | | **other_unique_id** | **0.00** |

Structured identifiers (email, MRN, SSN) are caught perfectly; **every name and
every narrative/spelled-out identifier leaks.** A single averaged number would
have hidden exactly the gaps that matter. The report also lists the 10 Safe Harbor
categories the corpus never exercises (fax, URL, IP, device IDs, …) so 8-category
coverage is never mistaken for full 18-item coverage.

---

## Track 2 — Expert Determination (re-id via quasi-identifiers)

Against the 100k-person background population, on generalized QIs (5-year age band,
sex, ZIP3, rare-diagnosis flag):

- **min k = 1** — the released cohort is **not k-anonymous at any k ≥ 2**.
- **Prosecutor risk = 1.0** (every record is a sample singleton).
- **6 of 50 records are unique against the full 100,000-person population**
  (mean journalist/marketer risk ≈ 0.37).

These are the same notes Track 1 would pass. Safe Harbor pass, Expert Determination
fail — the divergence the harness exists to surface.

> Caveat the report states in-band: the v0 generator draws ZIP3 uniformly, so the
> prosecutor/k-anonymity figures are an upper bound; the population-side singleton
> count is the load-bearing number. A Synthea-backed population restores realistic
> class sizes.

---

## Track 3 — free-text inference (the pure AI-era threat)

Each note gets a diagnosis-free vignette; the attacker must name the *withheld*
diagnosis from clinical context alone. These notes carry **no** direct identifiers
(Safe Harbor passes them) and are **not** being linked (not Track 2's job) — the
leak is pure inference.

- **Overall recovery: 0.94** (95% CI 0.86 – 1.00)
- Rare diagnoses: **1.00** (distinctive signatures leak almost perfectly)
- Common diagnoses: 0.93
- Abstains correctly on 6% of vignettes reduced to non-specific features

The bundled `signature-match-v0` is a knowledge-only baseline; the scorer emits
agent-eval's JSONL schema, so a real LLM attacker's free-text guesses can be graded
and calibrated through the [`agent-eval`](../agent-eval/) skill.

---

## What this demonstrates

Three orthogonal threat models, each with the right standard, none collapsed into
one number; a privacy score always paired with a utility cost; and every headline
figure carries a confidence interval. The `test_harness.py` regression suite (31
tests) locks these numbers so a result can't silently move.
