# Statistical rigor — confidence intervals and significance

Every headline number reported by Tracks 1–3, the frontier, and the cross-track synthesis
was, until now, a single point estimate on one corpus, one seed, n=50. That is the first
thing a reviewer will press on: is a 0.90-vs-0.45 privacy gap real, or noise from a small
sample? `bootstrap.py` and `score_stats.py` answer that.

## Why records, not spans, are the resampling unit

A note's identifier spans are **not independent evidence**. One messy patient record
contributes many correlated spans — a hard-to-catch name, a hard-to-catch date, a phone
number, all from the SAME note, and all likely to fail (or succeed) together because they
share the same surface-form messiness. Treating each span as an independent sample would
understate the true uncertainty.

So every function in `bootstrap.py` resamples whole **records** with replacement — a
standard **cluster bootstrap**, with the record as the cluster. A record's contribution to
a ratio metric (e.g. Track 1 coverage) is carried as one `(numerator, denominator)` pair —
e.g. `(spans_caught, spans_total)` for that record — and a bootstrap draw resamples n
records, sums their numerators and denominators, and recomputes the ratio. A simple 0/1
proportion (cross-track vulnerable, Track 3 correct) is the special case denominator=1.

## Two questions, two modes

**1. Bootstrap (one corpus) — "how much could this number move on resampling?"**
`bootstrap_ratio_ci(pairs, n_boot=2000)` gives a 95% percentile CI for any of the
harness's ratio metrics: Track 1 coverage, utility preservation, Track 3 recovery,
cross-track vulnerable fraction — all computed by directly re-invoking the corresponding
scorer's per-record logic (`score_leakage.score_record`, `score_utility.score_record`,
`score_inference.score_corpus`, `score_crosstrack.compute_per_record`), so a bootstrap
point estimate is **guaranteed identical** to the standalone report's number — the same
parity discipline cross-track already applies to Track 2/3.

**2. Paired bootstrap difference — "is defender A really different from defender B?"**
`paired_bootstrap_diff(pairs_a, pairs_b)` tests whether two defenders' metrics differ on
the *same* records, using the *same* resampled indices for both sides on every draw. This
pairing is the statistically correct comparison here (both pipelines score the identical
corpus) and is more powerful than comparing two independent CIs, because it cancels
record-to-record variance that both defenders share. It reports the CI of the difference
and a two-sided p-value (from how often the bootstrap difference crosses zero);
`significant_at_0.05` is `True` iff the 95% CI excludes zero.

**3. Seed sweep — "is this an artifact of one synthetic-generation draw?"**
`score_stats.py --seeds K` regenerates the corpus from scratch across K independent seeds
and reports each metric's mean/min/max across seeds. This is a *different* question from
the bootstrap CI: the bootstrap asks about sampling uncertainty within one corpus; the
seed sweep asks whether the result reproduces across independently generated corpora. On
the bundled defenders, both agree: at n=50, over-redact's privacy (0.886–0.900 across 5
seeds) never overlaps the baseline's (0.417–0.456) — the frontier gap is real, not noise
from one draw.

## Verified results (n=50, seed 20260101, 2000 bootstrap resamples)

| metric | point | 95% CI |
|--------|-------|--------|
| Track 1 privacy — `regex-baseline-v0` | 0.449 | [0.429, 0.469] |
| Track 1 privacy — `over-redact-v0` | 0.900 | [0.876, 0.922] |
| utility — `regex-baseline-v0` | 1.000 | [1.000, 1.000] |
| utility — `over-redact-v0` | 0.600 | [0.560, 0.633] |
| Track 3 recovery | 0.940 | [0.860, 1.000] |
| cross-track vulnerable | 0.980 | [0.940, 1.000] |

Both frontier differences are **significant at α=0.05** (privacy diff p≈0, utility diff
p≈0) — the defenders' tradeoff is a real effect at this sample size, not sampling noise.

## Usage

```bash
# CIs on one corpus (also runs the paired difference test if exactly 2 --pipelines given)
python score_stats.py --corpus corpus.json --out stats.json

# stability across 10 independently generated corpora
python score_stats.py --seeds 10 --n 50 --out stats_sweep.json
```

## What this does NOT fix

A bootstrap CI describes uncertainty **given this corpus and this attacker** — it cannot
tell you whether n=50 synthetic notes generalize to real clinical text, whether the
signature-match baseline generalizes to a real attacker, or whether the ZIP3 upper-bound
caveat (see `references/expert-determination.md`) is resolved. Those require real data
(see `references/data-sources.md`) and a real LLM attacker, not more bootstrap iterations.
Wider CIs on synthetic data are not a substitute for external validity — treat this as
"the numbers we report are stable," not "the numbers we report are true of real notes."
