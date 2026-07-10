#!/usr/bin/env python3
"""
Bootstrap statistics — the harness's confidence-interval machinery.

Every headline number so far (0.449 Track 1 coverage, 0.94 Track 3 recovery, the
frontier's two corners, 49/50 cross-track) has been a single point estimate on one
corpus, one seed. That is a reviewer's first objection: is 0.900 vs 0.449 a real gap, or
noise from n=50? This module answers that with a **cluster bootstrap over records**.

Why records, not spans: a note's identifier spans are NOT independent evidence — one
messy patient contributes many correlated spans (a hard-to-catch name, a hard-to-catch
date, all from the SAME note). Bootstrapping at the span level would understate the true
uncertainty by treating correlated observations as independent. So every function here
resamples whole RECORDS with replacement, keeping each record's spans/verdict together —
the standard "cluster bootstrap" for exactly this correlation structure.

Two building blocks:
  * bootstrap_ratio_ci   — a ratio metric (caught/total, preserved/total, a plain
                           proportion where denominator=1) with a percentile CI.
  * paired_bootstrap_diff — is defender A's metric different from defender B's, on the
                           SAME records? Paired because both pipelines score the identical
                           corpus — the same bootstrap resample is applied to both sides,
                           which is more powerful than an unpaired test and is the
                           statistically correct comparison here.

All randomness is seeded (default 12345) so results are reproducible, matching the rest
of the harness's determinism discipline.
"""
from __future__ import annotations
import random

DEFAULT_N_BOOT = 2000
DEFAULT_BOOT_SEED = 12345


def _ratio(idxs, pairs):
    num = sum(pairs[i][0] for i in idxs)
    den = sum(pairs[i][1] for i in idxs)
    return num / den if den else 0.0


def bootstrap_ratio_ci(pairs, n_boot=DEFAULT_N_BOOT, boot_seed=DEFAULT_BOOT_SEED):
    """pairs: list of (numerator, denominator) contributed by each record (the cluster).
    A simple 0/1 proportion is the special case denominator=1 for every record.
    Returns {"point", "ci_lo", "ci_hi", "n_records", "n_boot"}."""
    n = len(pairs)
    if n == 0:
        raise ValueError("bootstrap_ratio_ci: no records")
    point = _ratio(range(n), pairs)
    rng = random.Random(boot_seed)
    boots = []
    for _ in range(n_boot):
        idxs = [rng.randrange(n) for _ in range(n)]
        boots.append(_ratio(idxs, pairs))
    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[min(int(0.975 * n_boot), n_boot - 1)]
    return {"point": round(point, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
            "n_records": n, "n_boot": n_boot}


def paired_bootstrap_diff(pairs_a, pairs_b, n_boot=DEFAULT_N_BOOT, boot_seed=DEFAULT_BOOT_SEED):
    """Is metric(A) different from metric(B) on the SAME n records? pairs_a[i] and
    pairs_b[i] must refer to the same underlying record i (e.g. two defenders scored
    over the identical corpus). Every bootstrap draw uses ONE set of resampled record
    indices for both sides — the pairing — which cancels shared record-to-record
    variance and gives a tighter, correct test than comparing two independent CIs.
    Returns point estimates, the CI of the difference (a-b), and a two-sided p-value
    approximated from how often the bootstrap difference crosses zero."""
    n = len(pairs_a)
    if n != len(pairs_b):
        raise ValueError("paired_bootstrap_diff: pairs_a and pairs_b must be the same length")
    point_a = _ratio(range(n), pairs_a)
    point_b = _ratio(range(n), pairs_b)
    rng = random.Random(boot_seed)
    diffs = []
    for _ in range(n_boot):
        idxs = [rng.randrange(n) for _ in range(n)]
        diffs.append(_ratio(idxs, pairs_a) - _ratio(idxs, pairs_b))
    diffs.sort()
    lo = diffs[int(0.025 * n_boot)]
    hi = diffs[min(int(0.975 * n_boot), n_boot - 1)]
    p_le0 = sum(1 for d in diffs if d <= 0) / n_boot
    p_ge0 = sum(1 for d in diffs if d >= 0) / n_boot
    p_value = round(min(1.0, 2 * min(p_le0, p_ge0)), 4)
    return {"point_a": round(point_a, 4), "point_b": round(point_b, 4),
            "diff": round(point_a - point_b, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
            "p_value": p_value, "significant_at_0.05": not (lo <= 0 <= hi),
            "n_records": n, "n_boot": n_boot}
