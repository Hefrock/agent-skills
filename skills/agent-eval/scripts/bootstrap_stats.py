#!/usr/bin/env python3
"""
bootstrap_stats.py - Confidence intervals and a paired significance test for
score_eval.py's pass rate and mean score.

SKILL.md step 7 already names the problem: "With under ~20 cases, a 2-3 case
swing can look like a large percentage shift... say so explicitly." Before
this module existed, that was pure prose — nothing computed how much a small
eval set's pass rate could plausibly move on noise alone, and
find_regressions() flagged any case that crossed the threshold (even 0.699 ->
0.701) as a "regression" with no sense of whether that's a real signal or an
LLM judge's normal run-to-run wobble.

Same technique `deid-reid-harness/scripts/bootstrap.py` already uses for its
own headline metrics (a percentile bootstrap CI, plus a paired bootstrap test
for a before/after comparison) — borrowed here rather than re-derived, since
it's the same underlying question ("is this gap real or noise at this sample
size") for a different domain. One real simplification versus that module:
deid-reid-harness resamples whole RECORDS because one patient contributes
several correlated identifier spans (see that module's docstring), so a plain
per-span bootstrap would understate uncertainty. An eval case here has no such
internal clustering — one case contributes exactly one score — so a plain
per-case resample is the statistically correct unit, and the numerator/
denominator "pairs" shape that module needs isn't necessary here; this one
resamples flat value lists directly.

Not imported across the skill boundary from deid-reid-harness on purpose —
each skill folder is meant to be independently copyable (see this repo's
README: `cp -r skills/agent-eval ~/.claude/skills/`), so a shared technique
gets its own scoped copy per skill rather than a cross-skill import that
would break the moment one skill is copied without the other.

All randomness is seeded (default 12345, same default deid-reid-harness
uses) so a given input always reproduces the same CI/p-value.

Stdlib only (random) — no dependencies to install.
"""

from __future__ import annotations

import random

DEFAULT_N_BOOT = 2000
DEFAULT_BOOT_SEED = 12345


def bootstrap_mean_ci(values: list[float], n_boot: int = DEFAULT_N_BOOT, boot_seed: int = DEFAULT_BOOT_SEED) -> dict:
    """95% percentile bootstrap CI on the mean of `values`. Works identically
    for a mean score (values are 0.0-1.0 floats) or a pass rate (values are
    0/1 flags — pass_rate is just the mean of "did this case pass"), so
    score_eval.py calls this once for each rather than needing two functions.

    Returns {"point", "ci_lo", "ci_hi", "n", "n_boot"}. Raises ValueError on
    an empty list — there's no meaningful interval over zero cases, same
    "don't fabricate a stat over no data" discipline score_eval.summarize()
    already applies (returns None on empty results rather than crashing)."""
    n = len(values)
    if n == 0:
        raise ValueError("bootstrap_mean_ci: no values")
    point = sum(values) / n
    rng = random.Random(boot_seed)
    boots = []
    for _ in range(n_boot):
        boots.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[min(int(0.975 * n_boot), n_boot - 1)]
    return {"point": round(point, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4), "n": n, "n_boot": n_boot}


def paired_bootstrap_diff(
    values_a: list[float], values_b: list[float], n_boot: int = DEFAULT_N_BOOT, boot_seed: int = DEFAULT_BOOT_SEED
) -> dict:
    """Is the mean of `values_a` different from the mean of `values_b`,
    on the SAME n cases? values_a[i] and values_b[i] must be the same
    case's two scores (current run vs. baseline run) — paired, not two
    independent samples, because both runs graded the identical case set.
    Every bootstrap draw resamples ONE set of case indices and applies it
    to both sides, which cancels shared case-to-case variance the same way
    deid-reid-harness's paired_bootstrap_diff() does for its own paired
    before/after comparisons, and is a tighter, more correct test than
    comparing two independent CIs for overlap.

    Returns point estimates, the CI of the difference (a-b), a two-sided
    p-value approximated from how often the bootstrap difference crosses
    zero, and significant_at_0.05 (true iff the 95% CI excludes zero).
    Raises ValueError if the two lists aren't the same length or are empty
    — a length mismatch means they weren't actually matched by case id
    before calling this, which is a caller bug worth failing loud on."""
    n = len(values_a)
    if n == 0:
        raise ValueError("paired_bootstrap_diff: no values")
    if n != len(values_b):
        raise ValueError("paired_bootstrap_diff: values_a and values_b must be the same length")
    point_a = sum(values_a) / n
    point_b = sum(values_b) / n
    rng = random.Random(boot_seed)
    diffs = []
    for _ in range(n_boot):
        idxs = [rng.randrange(n) for _ in range(n)]
        mean_a = sum(values_a[i] for i in idxs) / n
        mean_b = sum(values_b[i] for i in idxs) / n
        diffs.append(mean_a - mean_b)
    diffs.sort()
    lo = diffs[int(0.025 * n_boot)]
    hi = diffs[min(int(0.975 * n_boot), n_boot - 1)]
    p_le0 = sum(1 for d in diffs if d <= 0) / n_boot
    p_ge0 = sum(1 for d in diffs if d >= 0) / n_boot
    p_value = round(min(1.0, 2 * min(p_le0, p_ge0)), 4)
    return {
        "point_a": round(point_a, 4), "point_b": round(point_b, 4), "diff": round(point_a - point_b, 4),
        "ci_lo": round(lo, 4), "ci_hi": round(hi, 4), "p_value": p_value,
        "significant_at_0.05": not (lo <= 0 <= hi), "n": n, "n_boot": n_boot,
    }
