#!/usr/bin/env python3
"""
score_eval.py - Aggregate and report results from an agent-eval run.

Usage:
    python score_eval.py results.jsonl
    python score_eval.py results.jsonl --baseline previous_results.jsonl
    python score_eval.py results.jsonl --threshold 0.7
    python score_eval.py results.jsonl --json-out summary.json

CI gate (exit non-zero on failure):
    python score_eval.py results.jsonl --fail-under 0.8
    python score_eval.py results.jsonl --baseline base.jsonl --fail-on-regression
    python score_eval.py results.jsonl --baseline base.jsonl --fail-on-cost-regression --fail-on-latency-regression
    python score_eval.py results.jsonl --fail-if-mean-cost-above 0.01 --fail-if-mean-latency-above 2000

Statistical confidence (see bootstrap_stats.py):
    python score_eval.py results.jsonl --ci
    python score_eval.py results.jsonl --baseline base.jsonl --ci
    python score_eval.py results.jsonl --baseline base.jsonl --fail-on-significant-regression

`--ci` reports a 95% bootstrap confidence interval on pass rate and mean
score, and (with `--baseline`) a paired significance test on both the
pass-rate delta AND the mean-score delta between the two runs — direct
backing for step 7's "with under ~20 cases, a 2-3 case swing can look like
a large percentage shift" warning, which was prose-only before this
existed. `--fail-on-significant-regression` gates on those significance
tests rather than `--fail-on-regression`'s bare threshold-crossing count,
so a single case wobbling from 0.699 to 0.701 (plausible judge noise)
doesn't fail a build the way it would under `--fail-on-regression` alone.
The mean-score test exists specifically because the pass-rate test alone
discards magnitude: a case dropping from 0.9 to 0.4 counts identically to
one dropping from 0.71 to 0.69 once both are "fail," so a real, consistent
quality drop that never crosses `--threshold` is invisible to the pass-rate
signal but not to the raw-score one.

Both of those are still run-wide aggregates, which have their own blind
spot: a real regression concentrated in one category can get diluted away
against unaffected cases from other categories (confirmed directly on
this skill's own worked regression example — see
compute_per_category_confidence()'s docstring). With `--baseline`, `--ci`
also runs both paired tests *per category* (skipping any category under
3 matched cases — too few for a bootstrap to mean anything, see
MIN_CATEGORY_N_FOR_SIGNIFICANCE), and `--fail-on-significant-regression`
gates on those too.

Running that many significance tests together (2 run-wide + 2 per surviving
category) at alpha=0.05 each has its own cost: the chance that AT LEAST ONE
comes back "significant" purely by chance climbs well past 5% as more tests
are added — confirmed empirically (not assumed) at ~27.5% on genuinely
stable data (no real regression anywhere) across 4 categories. Every
significance verdict — run-wide and per-category — is therefore corrected
via the Benjamini-Hochberg (FDR) procedure (see
apply_multiple_comparisons_correction()), brought back to ~8%, close to the
nominal 5% target, in the same simulation. This is why a category whose
uncorrected p-value would have cleared the old fixed 0.05 (e.g. p=0.043 on
8 cases) can still come back "not significant" once corrected — the
correction working as intended, not a bug: that p-value was never strong
enough evidence once every other question asked in the same pass is
honestly accounted for. (An earlier version of this correction used
Bonferroni instead — simpler, but its detectability for a real, unchanged
regression turned out to depend on how many *unrelated* categories
happened to exist alongside it, confirmed directly; Benjamini-Hochberg is
meaningfully more robust to that while keeping the false-positive rate
just as controlled — see apply_multiple_comparisons_correction()'s
docstring for the numbers.)

Regression candidates and everything else (improvements, and the exact-
zero-diff edge case) are corrected as two SEPARATE Benjamini-Hochberg
families, not one pooled ranking — a second fix on top of the first,
confirmed necessary the same way: an unrelated, genuinely significant
*improvement* elsewhere in the same run could otherwise lower the
effective bar for a borderline regression to pass (BH's threshold grows
with rank, and low-p-value improvements occupy the earliest ranks ahead
of it), which was never correcting for a shared decision, since only
regression candidates can ever fire the gate. See
apply_multiple_comparisons_correction()'s docstring for the confirmed
numbers.

The gate fires if any signal — run-wide pass rate, run-wide mean score, or
any single category's pass rate or mean score — is significant after
correction; all of these answer different questions and are meant to be
used together, not as substitutes for each other.

Input format (JSONL, one JSON object per line):
    {"id": "case_001", "score": 1.0, "category": "format", "rationale": "..."}
    {"id": "case_002", "score": 0.0, "category": "accuracy", "rationale": "..."}

`score` can be a float (0-1) or a bool (true/false treated as 1.0/0.0).
`category` and `rationale` are optional but recommended.

`category` is grouped case/whitespace-insensitively ("Accuracy", " accuracy ",
and "ACCURACY" all land in the same by_category row) — the report displays
whichever spelling was seen first. Category names that are merely *similar*
(a likely typo, not an exact match after normalizing) are never auto-merged;
instead a warning is printed listing the suspect pairs, since collapsing two
possibly-different categories automatically would be a worse mistake than
leaving them split.

Stdlib only — no dependencies to install.
"""

import argparse
import difflib
import json
import os
import statistics
import sys
from collections import defaultdict

# realpath, not abspath: agent-redteam calls this script via a relative
# cross-skill path (../agent-eval/scripts/score_eval.py, see that skill's
# SKILL.md step 3) rather than a copy or symlink, but realpath is kept as
# the defensive default regardless — it resolves to this file's actual
# directory even if some future invocation path (a symlink, a packaged
# copy) doesn't match this file's own location, so jsonl_io.py is always
# found next to the real script, not wherever it was invoked from.
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import jsonl_io  # noqa: E402
import bootstrap_stats  # noqa: E402


def load_results(path):
    """List of already-scored rows — thin wrapper over jsonl_io.
    load_jsonl(), the shared primitive run_judge.load_cases() and
    calibrate_judge.load_scores_by_id() also build on. score is coerced
    to float here (bool True/False -> 1.0/0.0) because that coercion is
    specific to what this function returns, not something every JSONL
    reader in this skill needs."""
    results = jsonl_io.load_jsonl(path, required_keys=("id", "score"))
    for r in results:
        score = r["score"]
        r["score"] = 1.0 if score is True else 0.0 if score is False else float(score)
    return results


def normalize_category(raw) -> str:
    """The grouping key used for by_category — case/whitespace-insensitive,
    so "Accuracy", " accuracy ", and "ACCURACY" land in the same bucket
    instead of silently splitting into separate rows. Real risk this
    guards against: `category` is operator-typed per case/run (SKILL.md
    step 4: "assigned by you, not read from the judge's per-criterion
    keys"), not drawn from a fixed enum anywhere in this pipeline, so a
    stray capital or trailing space between two eval runs would otherwise
    silently fragment one category's stats into two without any error —
    exactly the kind of thing summarize()'s output looks plausible while
    being wrong. Only used as the dict key; the display label a user sees
    is still the first-seen original spelling (see summarize())."""
    return str(raw).strip().lower()


def find_likely_typo_categories(categories, similarity_threshold: float = 0.82):
    """Pairs of already-normalized category keys similar enough to likely
    be the same category typed two different ways (e.g. "accruacy" vs
    "accuracy") but not identical — normalize_category() already merges
    exact case/whitespace variants, so a pair only ever reaches here over
    a real spelling difference. Uses difflib's ratio (stdlib, no new
    dependency) rather than true edit distance; deliberately conservative
    (default 0.82) since this is a warning printed to stderr, never an
    automatic merge — two genuinely different category names must never
    be silently combined just because they look similar."""
    pairs = []
    cats = sorted(set(categories))
    for i, a in enumerate(cats):
        for b in cats[i + 1:]:
            if difflib.SequenceMatcher(None, a, b).ratio() >= similarity_threshold:
                pairs.append((a, b))
    return pairs


def summarize(results, threshold):
    if not results:
        return None
    scores = [r["score"] for r in results]
    passed = [r for r in results if r["score"] >= threshold]

    by_category = defaultdict(list)
    display_names = {}
    for r in results:
        raw_category = r.get("category", "uncategorized")
        key = normalize_category(raw_category)
        by_category[key].append(r)
        display_names.setdefault(key, raw_category)

    has_cost = any("cost_usd" in r for r in results)
    has_latency = any("latency_ms" in r for r in results)

    cat_stats = {}
    for key, cat_results in sorted(by_category.items()):
        s = [r["score"] for r in cat_results]
        stat = {
            "count": len(s),
            "mean_score": statistics.mean(s),
            "pass_rate": sum(1 for x in s if x >= threshold) / len(s),
        }
        if has_cost:
            costs = [r["cost_usd"] for r in cat_results if "cost_usd" in r]
            if costs:
                stat["mean_cost_usd"] = statistics.mean(costs)
        if has_latency:
            latencies = [r["latency_ms"] for r in cat_results if "latency_ms" in r]
            if latencies:
                stat["mean_latency_ms"] = statistics.mean(latencies)
        cat_stats[display_names[key]] = stat

    summary = {
        "total": len(results),
        "pass_count": len(passed),
        "pass_rate": len(passed) / len(results),
        "mean_score": statistics.mean(scores),
        "by_category": cat_stats,
    }
    if has_cost:
        all_costs = [r["cost_usd"] for r in results if "cost_usd" in r]
        if all_costs:
            summary["mean_cost_usd"] = statistics.mean(all_costs)
    if has_latency:
        all_latencies = [r["latency_ms"] for r in results if "latency_ms" in r]
        if all_latencies:
            summary["mean_latency_ms"] = statistics.mean(all_latencies)
    return summary


def lowest_scoring(results, n=3):
    return sorted(results, key=lambda r: r["score"])[:n]


def find_regressions(results, baseline_results, threshold):
    baseline_by_id = {r["id"]: r["score"] for r in baseline_results}
    regressions = []
    for r in results:
        base_score = baseline_by_id.get(r["id"])
        if base_score is None:
            continue
        if base_score >= threshold and r["score"] < threshold:
            regressions.append({
                "id": r["id"],
                "baseline_score": base_score,
                "current_score": r["score"],
            })
    return regressions


def matched_scores(results, baseline_results):
    """(current_scores, baseline_scores), aligned by shared id, in `results`'
    order — only ids present in both lists contribute, same "only compare
    what both runs actually share" restriction find_regressions() already
    applies via its own baseline_by_id lookup. This is the paired sample
    compute_confidence()'s significance test needs: values_a[i] and
    values_b[i] must be the same case's two scores, not just two same-
    length lists that happen to line up by position."""
    baseline_by_id = {r["id"]: r["score"] for r in baseline_results}
    current, baseline = [], []
    for r in results:
        if r["id"] in baseline_by_id:
            current.append(r["score"])
            baseline.append(baseline_by_id[r["id"]])
    return current, baseline


def compute_confidence(results, threshold, baseline_results=None, n_boot=bootstrap_stats.DEFAULT_N_BOOT, boot_seed=bootstrap_stats.DEFAULT_BOOT_SEED):
    """The noise-awareness SKILL.md step 7 asks for in prose ("say so
    explicitly" when a sample is too small to call a shift real) but
    nothing before this computed: a 95% bootstrap CI on this run's pass
    rate and mean score, plus — when a baseline is given and shares at
    least one case id — a paired bootstrap significance test on the pass-
    rate delta between the two runs. That last piece is the direct fix
    for find_regressions()'s blind spot: it flags any case that crosses
    the threshold (even 0.699 -> 0.701, plausible LLM-judge run-to-run
    noise) as a "regression" with no sense of whether the *aggregate*
    shift is distinguishable from chance at this sample size.

    Also computes "paired_mean_score_diff" — the same paired test on the
    *raw* scores, not binarized to pass/fail. Binarizing to pass/fail
    throws away magnitude: a case that drops from 0.9 to 0.4 counts
    exactly the same as one that drops from 0.71 to 0.69 once both are
    "fail," so a real, large-magnitude regression that happens not to
    flip enough individual cases across --threshold can under-power the
    pass-rate test specifically. Confirmed on this skill's own worked
    regression example (examples/README.md's "Statistical confidence"
    section): the pass-rate paired diff there gives p=0.421, the raw-
    score version p=0.255 — same direction, meaningfully more sensitive,
    from the identical data. Reported as a second, independent number
    rather than replacing the pass-rate diff, since they answer genuinely
    different questions ("did the topline pass/fail count move" vs. "did
    quality move at all") and either can be significant without the other.

    Returns {"pass_rate_ci", "mean_score_ci"} always, plus
    "paired_pass_rate_diff" and "paired_mean_score_diff" (both
    bootstrap_stats.paired_bootstrap_diff() results) when baseline_results
    is given and shares at least one id with results — omitted, not
    fabricated, when there's no overlap to pair on."""
    scores = [r["score"] for r in results]
    passed_flags = [1.0 if s >= threshold else 0.0 for s in scores]
    confidence = {
        "pass_rate_ci": bootstrap_stats.bootstrap_mean_ci(passed_flags, n_boot, boot_seed),
        "mean_score_ci": bootstrap_stats.bootstrap_mean_ci(scores, n_boot, boot_seed),
    }
    if baseline_results:
        current_scores, baseline_scores = matched_scores(results, baseline_results)
        if current_scores:
            current_passed = [1.0 if s >= threshold else 0.0 for s in current_scores]
            baseline_passed = [1.0 if s >= threshold else 0.0 for s in baseline_scores]
            confidence["paired_pass_rate_diff"] = bootstrap_stats.paired_bootstrap_diff(
                current_passed, baseline_passed, n_boot, boot_seed
            )
            confidence["paired_mean_score_diff"] = bootstrap_stats.paired_bootstrap_diff(
                current_scores, baseline_scores, n_boot, boot_seed
            )
    return confidence


# Below this n, a bootstrap CI's width is not a reliable signal of precision
# — a narrow interval can appear simply because resampling a tiny, low-variance
# sample can't produce much spread, not because the true value is well pinned
# down. Verified empirically: mean CI width over 20 trials at p=0.7 is ~0.55
# at n=3, still ~0.5 at n=8-10, and only settles below ~0.3 past n=30-50 — the
# 20-case threshold below matches the pre-existing plain-pass-rate warning in
# print_report() rather than inventing a second, unjustified cutoff.
CI_LOW_RELIABILITY_N = 20

MIN_CATEGORY_N_FOR_SIGNIFICANCE = 3


def compute_per_category_confidence(
    results, threshold, baseline_results,
    n_boot=bootstrap_stats.DEFAULT_N_BOOT, boot_seed=bootstrap_stats.DEFAULT_BOOT_SEED,
    min_n=MIN_CATEGORY_N_FOR_SIGNIFICANCE,
):
    """compute_confidence()'s pass-rate/mean-score diffs only ever test the
    *overall* run — and that's a real blind spot this repo's own worked
    regression example demonstrates directly: results_regressed.jsonl's
    aggregate pass-rate diff is not significant (p=0.421) because an
    8-case regression concentrated entirely in the `accuracy` category
    gets diluted against 12 unaffected cases from three other categories.
    Tested directly against that exact category in isolation (not
    hypothesized): the same paired test, scoped to just the 8 matched
    `accuracy` cases, gives p=0.043 on both signals — genuinely
    significant. That's this function's whole reason to exist: run
    compute_confidence()'s same two paired tests once per category
    instead of once for the whole run, so a regression an aggregate test
    dilutes away still gets caught somewhere.

    Categories are grouped the same case/whitespace-insensitive way
    summarize()'s by_category is (normalize_category() for the key, first-
    seen spelling for the display label) — a case is assigned to whatever
    category its *current* row carries; baseline-only categories or
    id/category mismatches are not specially handled beyond matched_scores()'s
    existing "only ids present in both runs count" rule.

    A category with fewer than `min_n` matched cases is reported with
    "skipped_reason" instead of a computed diff, not silently dropped and
    not computed anyway. Below n=3, a paired bootstrap can only ever
    resample from 1-2 distinct per-case diffs — confirmed directly: two
    cases pointing the same direction (one a real regression, one just
    ordinary judge noise) bootstrap to p=0.0, "significant," with the
    bootstrap structurally unable to ever land on the other side of zero
    regardless of how thin that evidence actually is. That's a false
    confidence trap, not a real result, so this floor exists to refuse to
    compute rather than report it.

    A single category spanning every matched case (no `category` field
    anywhere, or every row sharing one label) is skipped too, for a
    different reason: its pairs are, by construction, the exact same set
    compute_confidence()'s run-wide diffs already test — confirmed
    directly, byte-for-byte identical output given the same n_boot/
    boot_seed, since it's the identical input to the identical function.
    Computing it anyway wouldn't just be wasted work: apply_multiple_
    comparisons_correction() counts every diff this function returns
    toward the test family it corrects for, so an undetected duplicate
    silently doubled the correction penalty (n_tests=4 instead of 2, alpha
    cut in half) for zero new information — a real bug this repo's own
    critique process caught, not a hypothetical.

    Returns {category_display_name: {"n": int, "paired_pass_rate_diff": ...,
    "paired_mean_score_diff": ...}} for categories at or above min_n and
    not the sole category, or {"n": int, "skipped_reason": str} for either
    skip reason. Empty dict if no baseline_results or no matched ids at
    all."""
    if not baseline_results:
        return {}
    baseline_by_id = {r["id"]: r["score"] for r in baseline_results}
    by_category: dict = {}
    display_names: dict = {}
    for r in results:
        if r["id"] not in baseline_by_id:
            continue
        raw_category = r.get("category", "uncategorized")
        key = normalize_category(raw_category)
        display_names.setdefault(key, raw_category)
        by_category.setdefault(key, []).append((r["score"], baseline_by_id[r["id"]]))

    if len(by_category) == 1:
        (key, pairs), = by_category.items()
        return {
            display_names[key]: {
                "n": len(pairs),
                "skipped_reason": "the only category present spans every matched case — identical to the run-wide result above, not computed again",
            }
        }

    per_category = {}
    for key, pairs in sorted(by_category.items()):
        n = len(pairs)
        label = display_names[key]
        if n < min_n:
            per_category[label] = {
                "n": n,
                "skipped_reason": f"only {n} matched case(s) — need at least {min_n} for a meaningful bootstrap",
            }
            continue
        current_scores = [p[0] for p in pairs]
        baseline_scores = [p[1] for p in pairs]
        current_passed = [1.0 if s >= threshold else 0.0 for s in current_scores]
        baseline_passed = [1.0 if s >= threshold else 0.0 for s in baseline_scores]
        per_category[label] = {
            "n": n,
            "paired_pass_rate_diff": bootstrap_stats.paired_bootstrap_diff(current_passed, baseline_passed, n_boot, boot_seed),
            "paired_mean_score_diff": bootstrap_stats.paired_bootstrap_diff(current_scores, baseline_scores, n_boot, boot_seed),
        }
    return per_category


DEFAULT_FAMILY_ALPHA = 0.05


def bonferroni_alpha(n_tests: int, family_alpha: float = DEFAULT_FAMILY_ALPHA) -> float:
    """The corrected per-test significance threshold when `n_tests`
    significance tests are examined together and any one of them firing
    would trigger an action (here, failing a build) — family_alpha divided
    by the test count. Controls the family-wise false-positive rate
    regardless of whether the tests are independent (they aren't, fully,
    here — a category's pass-rate and mean-score diffs come from the same
    underlying cases), which is a real strength — but see
    benjamini_hochberg_significance()'s docstring for why this repo no
    longer uses it as the default: that same test-count sensitivity means
    a real regression's detectability depends on how many *unrelated*
    categories happen to exist alongside it in the same eval set, confirmed
    directly (not theorized) to flip a fixed, unchanged regression from
    detected to undetected as irrelevant categories were added elsewhere.
    Kept here — not deleted — as a stricter, still-available alternative;
    apply_multiple_comparisons_correction() no longer calls it by default.
    n_tests <= 0 returns family_alpha unchanged — nothing to correct for."""
    if n_tests <= 0:
        return family_alpha
    return family_alpha / n_tests


def benjamini_hochberg_significance(diffs: list, family_alpha: float = DEFAULT_FAMILY_ALPHA) -> None:
    """Standard step-up Benjamini-Hochberg procedure: sort `diffs` by
    p-value ascending, find the LARGEST rank k where p_(k) <= (k/m)*alpha
    (m = len(diffs)), and mark every diff at or below that rank as
    significant — not a fixed per-test threshold the way Bonferroni's is,
    since the cutoff `k` depends on the whole sorted p-value distribution,
    not just the count. Controls the *false discovery rate* (expected
    proportion of false positives among what's flagged), a different,
    generally more permissive guarantee than Bonferroni's family-wise
    error rate (probability of *any* false positive) — the standard,
    textbook alternative when Bonferroni is too conservative, not an
    exotic or ad hoc substitute.

    Mutates `diffs` in place, adding "significant_after_correction" to
    each, same convention apply_multiple_comparisons_correction() (this
    function's only caller) already established. No-op on an empty list."""
    m = len(diffs)
    if m == 0:
        return
    ranked = sorted(range(m), key=lambda i: diffs[i]["p_value"])
    largest_k = 0
    for rank, idx in enumerate(ranked, 1):
        if diffs[idx]["p_value"] <= (rank / m) * family_alpha:
            largest_k = rank
    for rank, idx in enumerate(ranked, 1):
        diffs[idx]["significant_after_correction"] = rank <= largest_k


def apply_multiple_comparisons_correction(confidence, per_category_confidence, family_alpha: float = DEFAULT_FAMILY_ALPHA):
    """Adds "significant_after_correction" to every paired-diff dict this
    run actually computed — both run-wide diffs from compute_confidence()
    and every non-skipped category's two diffs from
    compute_per_category_confidence() — via Benjamini-Hochberg instead of
    the fixed 0.05 each individual bootstrap_stats.paired_bootstrap_diff()
    call used for its own "significant_at_0.05" (left untouched, still
    present, just no longer what gates/reporting act on).

    Why any correction exists at all, verified empirically before
    building: running every test this module can produce together (2
    run-wide + 2 per surviving category) at an uncorrected alpha=0.05 each
    inflates the chance that AT LEAST ONE comes back "significant" purely
    from asking more questions — simulated on genuinely stable data
    (baseline and current drawn from the identical distribution, no real
    regression anywhere), 4 categories x 2 metrics + 2 run-wide = 10 tests
    together: 27.5% of runs falsely tripped what would become
    --fail-on-significant-regression, with nothing actually wrong.

    Why Benjamini-Hochberg specifically, not the Bonferroni correction this
    function used originally: Bonferroni's own test-count sensitivity
    turned out to have a second, undocumented-until-found cost, confirmed
    directly against a fixed, unchanged regression (p=0.005) while only
    varying how many *unrelated*, entirely stable categories existed
    alongside it — Bonferroni flips it from detected to undetected at just
    3 unrelated categories; Benjamini-Hochberg still detects it at 7. Both
    keep the original false-positive simulation under control at this
    module's default --n-boot (2000): Bonferroni ~6.5%, Benjamini-Hochberg
    ~8.0% — both close to the nominal 5% family_alpha target and nowhere
    near the original uncorrected 27.5%, confirmed on the identical
    simulation before switching, not assumed from the method's reputation.
    bonferroni_alpha() is kept, not deleted, as a stricter alternative;
    this function no longer calls it by default.

    A real, honest consequence from when this correction (any version of
    it) was first added, not smoothed over: it's why this skill's own
    flagship worked example (see examples/README.md's "Statistical
    confidence" section) no longer trips --fail-on-significant-regression
    on the accuracy category alone — its p=0.043 cleared the old
    uncorrected alpha=0.05 but doesn't survive being honestly weighed
    against the other tests examined in the same pass. That's the
    correction working as intended, not a regression in capability:
    p=0.043 was never strong evidence once every question asked in the
    same pass is properly accounted for, at only 8 matched cases.

    Regression candidates (diff < 0) and everything else (diff >= 0 —
    improvements, and the zero-diff edge case) are corrected as TWO
    SEPARATE Benjamini-Hochberg families, not one pooled ranking —
    confirmed necessary, not a style preference: pooling them let an
    unrelated, genuinely significant *improvement* elsewhere in the same
    run lower the effective bar for a borderline regression to pass,
    since BH's rank-dependent threshold grows with rank, and low-p-value
    improvements occupy the lowest ranks ahead of it. Verified directly on
    a fixed, unchanged borderline regression (p=0.043): adding 2+ unrelated
    *stable* categories never flipped it significant, but adding 2+
    unrelated *strongly improving* categories did, at matched test counts,
    with the identical regression data throughout. Only diff < 0 diffs can
    ever fire --fail-on-significant-regression (see check_gates()), so
    pooling improvements into that same correction family was never
    correcting for a shared decision — it was letting one family's
    evidence quietly move the other's goalposts. Improvements still get
    their own BH-corrected significance (so "SIGNIFICANT IMPROVEMENT" in
    the report stays meaningful), just never at the regression family's
    expense.

    Mutates the diff dicts in place — compute_confidence() and
    compute_per_category_confidence() construct them fresh on every call,
    never share or cache one across calls, so there's nothing else that
    could be surprised by the mutation. Returns {"n_tests",
    "n_regression_candidates", "n_non_regression_candidates", "method",
    "family_alpha"} for the caller to report alongside the per-diff
    verdicts — no single "corrected alpha" number the way Bonferroni had,
    since Benjamini-Hochberg's cutoff depends on the whole sorted p-value
    distribution of whichever family a diff belongs to, not a fixed
    per-test threshold."""
    diffs = []
    if confidence:
        for key in ("paired_pass_rate_diff", "paired_mean_score_diff"):
            d = confidence.get(key)
            if d is not None:
                diffs.append(d)
    for entry in (per_category_confidence or {}).values():
        if "skipped_reason" in entry:
            continue
        diffs.append(entry["paired_pass_rate_diff"])
        diffs.append(entry["paired_mean_score_diff"])

    regression_candidates = [d for d in diffs if d["diff"] < 0]
    non_regression_candidates = [d for d in diffs if d["diff"] >= 0]
    benjamini_hochberg_significance(regression_candidates, family_alpha)
    benjamini_hochberg_significance(non_regression_candidates, family_alpha)
    return {
        "n_tests": len(diffs),
        "n_regression_candidates": len(regression_candidates),
        "n_non_regression_candidates": len(non_regression_candidates),
        "method": "benjamini_hochberg",
        "family_alpha": family_alpha,
    }


def find_metric_regression(summary, baseline_summary, metric, tolerance):
    """summary/baseline_summary are summarize()'s return values; metric is
    "mean_cost_usd" or "mean_latency_ms". Returns a details dict if the
    current mean exceeds the baseline mean by more than `tolerance` (a
    fraction — 0.2 means "more than 20% higher fails"), else None.

    A change that holds pass rate steady while doubling cost or latency
    was previously invisible to every gate this script offered — score_
    eval.py already computes and prints mean_cost_usd/mean_latency_ms
    every run, this is the missing other half: actually gating on them,
    the same way --fail-on-regression gates on score regressions. Returns
    None (not a failure) when either summary lacks the metric — a results
    file with no cost/latency data at all isn't a regression, it's just
    data this gate can't evaluate."""
    if summary is None or baseline_summary is None:
        return None
    current = summary.get(metric)
    baseline = baseline_summary.get(metric)
    if current is None or baseline is None or baseline <= 0:
        return None
    if current > baseline * (1 + tolerance):
        return {"metric": metric, "baseline": baseline, "current": current, "tolerance": tolerance}
    return None


def check_gates(
    summary, regressions, fail_under, fail_on_regression,
    cost_regression=None, latency_regression=None,
    fail_if_mean_cost_above=None, fail_if_mean_latency_above=None,
    significant_regression=None, significant_score_regression=None,
    per_category_confidence=None,
):
    """Return a list of gate-failure messages (empty list means all gates pass).

    Used to turn a report into a CI pass/fail. Kept separate from print_report
    so it can be unit-tested and so reporting never depends on gate config.

    cost_regression/latency_regression are find_metric_regression()'s
    return values (or None) — computed by the caller, not here, so this
    function stays a pure function over already-computed inputs, same
    convention as `regressions` above (find_regressions()'s output, not
    recomputed inside check_gates either). significant_regression/
    significant_score_regression are compute_confidence()'s
    "paired_pass_rate_diff"/"paired_mean_score_diff" values (or None),
    same "caller computes it, this function just reads it" split. Both
    are checked under the same --fail-on-significant-regression flag —
    deliberately, not two separate flags: the pass-rate test alone can
    miss a real, large-magnitude regression that doesn't flip enough
    individual cases across --threshold (see compute_confidence()'s
    docstring for the confirmed example), so the gate needs both signals
    to actually catch what "significant regression" should mean, not
    just the more conservative of the two.

    per_category_confidence is compute_per_category_confidence()'s return
    value (or None) — checked under the same flag for the same reason:
    an aggregate-level regression concentrated in one category can be
    diluted away by every signal above (confirmed on this skill's own
    worked example — see compute_per_category_confidence()'s docstring),
    so the gate walks every category's own paired diffs too, not just the
    two run-wide ones.
    """
    failures = []
    if fail_under is not None:
        if summary is None:
            failures.append(f"--fail-under {fail_under}: no valid results to score")
        elif summary["pass_rate"] < fail_under:
            failures.append(
                f"--fail-under {fail_under}: pass rate {summary['pass_rate']:.3f} is below the gate"
            )
    if fail_on_regression and regressions:
        failures.append(f"--fail-on-regression: {len(regressions)} regression(s) vs baseline")
    if significant_regression is not None and significant_regression["diff"] < 0 and significant_regression["significant_after_correction"]:
        failures.append(
            f"--fail-on-significant-regression (pass rate): {significant_regression['point_b']:.3f} -> "
            f"{significant_regression['point_a']:.3f} is statistically significant (p={significant_regression['p_value']}, "
            f"n={significant_regression['n']}), not just a threshold-crossing on noise"
        )
    if significant_score_regression is not None and significant_score_regression["diff"] < 0 and significant_score_regression["significant_after_correction"]:
        failures.append(
            f"--fail-on-significant-regression (mean score): {significant_score_regression['point_b']:.3f} -> "
            f"{significant_score_regression['point_a']:.3f} is statistically significant (p={significant_score_regression['p_value']}, "
            f"n={significant_score_regression['n']}) — caught here even though the pass-rate test alone might not"
        )
    for category, entry in (per_category_confidence or {}).items():
        for metric_key, metric_label in (("paired_pass_rate_diff", "pass rate"), ("paired_mean_score_diff", "mean score")):
            diff = entry.get(metric_key)
            if diff is not None and diff["diff"] < 0 and diff["significant_after_correction"]:
                failures.append(
                    f"--fail-on-significant-regression (category {category!r}, {metric_label}): "
                    f"{diff['point_b']:.3f} -> {diff['point_a']:.3f} is statistically significant "
                    f"(p={diff['p_value']}, n={diff['n']}) — diluted away in the run-wide aggregate"
                )
    if cost_regression is not None:
        failures.append(
            f"--fail-on-cost-regression: mean cost ${cost_regression['current']:.4f} exceeds baseline "
            f"${cost_regression['baseline']:.4f} by more than {cost_regression['tolerance'] * 100:.0f}%"
        )
    if latency_regression is not None:
        failures.append(
            f"--fail-on-latency-regression: mean latency {latency_regression['current']:.0f}ms exceeds baseline "
            f"{latency_regression['baseline']:.0f}ms by more than {latency_regression['tolerance'] * 100:.0f}%"
        )
    if fail_if_mean_cost_above is not None and summary is not None and summary.get("mean_cost_usd") is not None:
        if summary["mean_cost_usd"] > fail_if_mean_cost_above:
            failures.append(f"--fail-if-mean-cost-above {fail_if_mean_cost_above}: mean cost ${summary['mean_cost_usd']:.4f} exceeds it")
    if fail_if_mean_latency_above is not None and summary is not None and summary.get("mean_latency_ms") is not None:
        if summary["mean_latency_ms"] > fail_if_mean_latency_above:
            failures.append(f"--fail-if-mean-latency-above {fail_if_mean_latency_above}: mean latency {summary['mean_latency_ms']:.0f}ms exceeds it")
    return failures


def print_report(summary, results, regressions, threshold, cost_regression=None, latency_regression=None, confidence=None, per_category_confidence=None, correction=None):
    if summary is None:
        print("No valid results found.")
        return

    n = summary["total"]
    print("=== Eval Report ===")
    print(f"Cases: {n}")
    print(f"Pass rate (threshold {threshold}): {summary['pass_count']}/{n} ({summary['pass_rate'] * 100:.1f}%)")
    print(f"Mean score: {summary['mean_score']:.2f}")
    if summary.get("mean_cost_usd") is not None:
        print(f"Mean cost: ${summary['mean_cost_usd']:.4f}")
    if summary.get("mean_latency_ms") is not None:
        print(f"Mean latency: {summary['mean_latency_ms']:.0f}ms")

    if n < CI_LOW_RELIABILITY_N:
        print(f"⚠ Small sample (n={n}) — treat the pass rate as directional, not precise.")

    if confidence is not None:
        pr_ci = confidence["pass_rate_ci"]
        ms_ci = confidence["mean_score_ci"]
        print(f"\n95% CI (bootstrap, {pr_ci['n_boot']} resamples):")
        print(f"  Pass rate:  [{pr_ci['ci_lo'] * 100:.1f}%, {pr_ci['ci_hi'] * 100:.1f}%]")
        print(f"  Mean score: [{ms_ci['ci_lo']:.3f}, {ms_ci['ci_hi']:.3f}]")
        if pr_ci["n"] < CI_LOW_RELIABILITY_N:
            print(
                f"  ⚠ n={pr_ci['n']} — below n={CI_LOW_RELIABILITY_N}, bootstrap CI width is not a reliable "
                f"measure of precision; a narrow interval here can be an artifact of a small, low-variance "
                f"sample rather than a well-pinned-down estimate."
            )
        verdict_suffix = " (after correction)" if correction else ""
        diff = confidence.get("paired_pass_rate_diff")
        if diff is not None:
            verdict = "SIGNIFICANT" if diff["significant_after_correction"] else "not significant"
            print(
                f"  Pass rate vs baseline: {diff['point_b'] * 100:.1f}% -> {diff['point_a'] * 100:.1f}% "
                f"(diff {diff['diff'] * 100:+.1f}pp, 95% CI [{diff['ci_lo'] * 100:+.1f}pp, {diff['ci_hi'] * 100:+.1f}pp], "
                f"p={diff['p_value']}, {verdict}{verdict_suffix}, n={diff['n']} matched case(s))"
            )
            if diff["n"] < CI_LOW_RELIABILITY_N:
                print(f"  ⚠ n={diff['n']} matched case(s) — below n={CI_LOW_RELIABILITY_N}, this CI's width is not reliable either.")
        score_diff = confidence.get("paired_mean_score_diff")
        if score_diff is not None:
            verdict = "SIGNIFICANT" if score_diff["significant_after_correction"] else "not significant"
            print(
                f"  Mean score vs baseline: {score_diff['point_b']:.3f} -> {score_diff['point_a']:.3f} "
                f"(diff {score_diff['diff']:+.3f}, 95% CI [{score_diff['ci_lo']:+.3f}, {score_diff['ci_hi']:+.3f}], "
                f"p={score_diff['p_value']}, {verdict}{verdict_suffix}, n={score_diff['n']} matched case(s)) "
                f"— unbinarized, catches magnitude the pass-rate test above can miss"
            )
            if score_diff["n"] < CI_LOW_RELIABILITY_N:
                print(f"  ⚠ n={score_diff['n']} matched case(s) — below n={CI_LOW_RELIABILITY_N}, this CI's width is not reliable either.")
        if correction:
            print(
                f"  Multiple-comparisons correction: {correction['n_tests']} significance test(s) examined this pass "
                f"({correction['n_regression_candidates']} regression candidate(s), "
                f"{correction['n_non_regression_candidates']} other) -> Benjamini-Hochberg (FDR) at family "
                f"alpha={correction['family_alpha']:.2f}, corrected as two separate families so an unrelated "
                f"improvement can never move a regression's bar (see apply_multiple_comparisons_correction()'s "
                f"docstring). Every verdict above and below already uses the corrected outcome."
            )

    if len(summary["by_category"]) > 1:
        print("\nBy category:")
        for cat, stats in summary["by_category"].items():
            line = f"  {cat}: {stats['pass_rate'] * 100:.0f}% pass, mean {stats['mean_score']:.2f} (n={stats['count']})"
            if "mean_cost_usd" in stats:
                line += f", ${stats['mean_cost_usd']:.4f}/call"
            if "mean_latency_ms" in stats:
                line += f", {stats['mean_latency_ms']:.0f}ms"
            print(line)

    if per_category_confidence:
        header = "\nBy category vs baseline (paired bootstrap significance"
        header += ", Benjamini-Hochberg-corrected)" if correction else ")"
        print(header + ":")
        any_low_n = any(
            "skipped_reason" not in entry and entry["n"] < CI_LOW_RELIABILITY_N
            for entry in per_category_confidence.values()
        )
        if any_low_n:
            print(
                f"  (⚠ marks a category below n={CI_LOW_RELIABILITY_N} — its CI width, not just its "
                f"significance verdict, is unreliable there; see the run-wide CI caveat above)"
            )
        for cat, entry in per_category_confidence.items():
            if "skipped_reason" in entry:
                print(f"  {cat}: skipped — {entry['skipped_reason']}")
                continue
            lines = []
            for metric_key, metric_label in (("paired_pass_rate_diff", "pass rate"), ("paired_mean_score_diff", "score")):
                diff = entry[metric_key]
                if not diff["significant_after_correction"]:
                    verdict = "not significant"
                else:
                    # significant_after_correction alone doesn't say which
                    # direction — an improvement (diff > 0, e.g. this exact
                    # example's `format` category, every case held or rose)
                    # reads identically to a regression unless the sign is
                    # spelled out here explicitly.
                    verdict = "SIGNIFICANT REGRESSION" if diff["diff"] < 0 else "SIGNIFICANT IMPROVEMENT"
                lines.append(f"{metric_label} p={diff['p_value']} ({verdict})")
            low_n_marker = " ⚠" if entry["n"] < CI_LOW_RELIABILITY_N else ""
            print(f"  {cat} (n={entry['n']}){low_n_marker}: {', '.join(lines)}")

    lowest = lowest_scoring(results, n=min(3, n))
    print("\nLowest-scoring cases:")
    for r in lowest:
        rationale = r.get("rationale", "")
        suffix = f" — {rationale}" if rationale else ""
        print(f"  [{r['score']:.2f}] {r['id']}{suffix}")

    if regressions:
        print(f"\n⚠ {len(regressions)} regression(s) vs baseline (passed before, failing now):")
        for reg in regressions:
            print(f"  {reg['id']}: {reg['baseline_score']:.2f} -> {reg['current_score']:.2f}")

    for label, reg in (("Cost", cost_regression), ("Latency", latency_regression)):
        if reg is None:
            continue
        pct = (reg["current"] / reg["baseline"] - 1) * 100
        if reg["metric"] == "mean_cost_usd":
            current_str, baseline_str = f"${reg['current']:.4f}", f"${reg['baseline']:.4f}"
        else:
            current_str, baseline_str = f"{reg['current']:.0f}ms", f"{reg['baseline']:.0f}ms"
        print(f"\n⚠ {label} regression: mean {current_str} vs baseline {baseline_str} (+{pct:.0f}%, tolerance {reg['tolerance'] * 100:.0f}%)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Aggregate and report agent-eval results.")
    parser.add_argument("results", help="Path to a JSONL results file")
    parser.add_argument("--baseline", help="Path to a previous JSONL results file, to flag regressions")
    parser.add_argument("--threshold", type=float, default=0.7, help="Score >= threshold counts as a pass (default 0.7)")
    parser.add_argument("--json-out", help="Optional path to write the summary as JSON")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="Exit non-zero if the overall pass rate is below this value (CI gate)")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="Exit non-zero if any regression vs --baseline is found (CI gate)")
    parser.add_argument("--fail-on-cost-regression", action="store_true",
                        help="Exit non-zero if mean cost_usd increased more than --cost-regression-tolerance vs --baseline (CI gate; requires --baseline)")
    parser.add_argument("--fail-on-latency-regression", action="store_true",
                        help="Exit non-zero if mean latency_ms increased more than --latency-regression-tolerance vs --baseline (CI gate; requires --baseline)")
    parser.add_argument("--cost-regression-tolerance", type=float, default=0.2,
                        help="Fraction increase allowed before --fail-on-cost-regression trips (default: 0.20 = 20%%)")
    parser.add_argument("--latency-regression-tolerance", type=float, default=0.2,
                        help="Fraction increase allowed before --fail-on-latency-regression trips (default: 0.20 = 20%%)")
    parser.add_argument("--fail-if-mean-cost-above", type=float, default=None,
                        help="Exit non-zero if mean cost_usd exceeds this absolute value (CI gate; no --baseline needed)")
    parser.add_argument("--fail-if-mean-latency-above", type=float, default=None,
                        help="Exit non-zero if mean latency_ms exceeds this absolute value (CI gate; no --baseline needed)")
    parser.add_argument("--ci", action="store_true",
                        help="Report a 95%% bootstrap confidence interval on pass rate and mean score (and, with --baseline, "
                             "a paired significance test on the pass-rate delta) instead of treating every number as exact")
    parser.add_argument("--fail-on-significant-regression", action="store_true",
                        help="Exit non-zero only if --baseline's pass-rate drop is statistically significant (95%% CI excludes "
                             "zero), not just any single case crossing --threshold (CI gate; requires --baseline)")
    parser.add_argument("--n-boot", type=int, default=bootstrap_stats.DEFAULT_N_BOOT,
                        help=f"Bootstrap resamples for --ci/--fail-on-significant-regression (default: {bootstrap_stats.DEFAULT_N_BOOT})")
    parser.add_argument("--boot-seed", type=int, default=bootstrap_stats.DEFAULT_BOOT_SEED,
                        help=f"Random seed for the bootstrap, for reproducible CIs (default: {bootstrap_stats.DEFAULT_BOOT_SEED})")
    args = parser.parse_args()

    if (args.fail_on_cost_regression or args.fail_on_latency_regression) and not args.baseline:
        print("--fail-on-cost-regression/--fail-on-latency-regression require --baseline.", file=sys.stderr)
        sys.exit(2)
    if args.fail_on_significant_regression and not args.baseline:
        print("--fail-on-significant-regression requires --baseline.", file=sys.stderr)
        sys.exit(2)

    results = load_results(args.results)
    summary = summarize(results, args.threshold)

    regressions = []
    baseline_summary = None
    baseline_results = []
    if args.baseline:
        baseline_results = load_results(args.baseline)
        regressions = find_regressions(results, baseline_results, args.threshold)
        baseline_summary = summarize(baseline_results, args.threshold)

    categories_seen = {normalize_category(r.get("category", "uncategorized")) for r in results + baseline_results}
    typo_pairs = find_likely_typo_categories(categories_seen)
    if typo_pairs:
        print("Warning: these category names look similar enough to possibly be the same category, typo'd differently:", file=sys.stderr)
        for a, b in typo_pairs:
            print(f"  {a!r} vs {b!r}", file=sys.stderr)

    cost_regression = find_metric_regression(summary, baseline_summary, "mean_cost_usd", args.cost_regression_tolerance) if args.fail_on_cost_regression else None
    latency_regression = find_metric_regression(summary, baseline_summary, "mean_latency_ms", args.latency_regression_tolerance) if args.fail_on_latency_regression else None

    confidence = None
    significant_regression = None
    significant_score_regression = None
    per_category_confidence = None
    correction = None
    if (args.ci or args.fail_on_significant_regression) and summary is not None:
        confidence = compute_confidence(
            results, args.threshold, baseline_results=baseline_results if args.baseline else None,
            n_boot=args.n_boot, boot_seed=args.boot_seed,
        )
        if args.baseline:
            per_category_confidence = compute_per_category_confidence(
                results, args.threshold, baseline_results, n_boot=args.n_boot, boot_seed=args.boot_seed,
            )
            correction = apply_multiple_comparisons_correction(confidence, per_category_confidence)
        significant_regression = confidence.get("paired_pass_rate_diff")
        significant_score_regression = confidence.get("paired_mean_score_diff")

    print_report(
        summary, results, regressions, args.threshold, cost_regression=cost_regression, latency_regression=latency_regression,
        confidence=confidence, per_category_confidence=per_category_confidence, correction=correction,
    )

    if args.json_out and summary:
        output = dict(summary)
        output["regressions"] = regressions
        if confidence is not None:
            output["confidence"] = confidence
        if per_category_confidence:
            output["per_category_confidence"] = per_category_confidence
        if correction:
            output["multiple_comparisons_correction"] = correction
        with open(args.json_out, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Summary written to {args.json_out}")

    gate_failures = check_gates(
        summary, regressions, args.fail_under, args.fail_on_regression,
        cost_regression=cost_regression, latency_regression=latency_regression,
        fail_if_mean_cost_above=args.fail_if_mean_cost_above, fail_if_mean_latency_above=args.fail_if_mean_latency_above,
        significant_regression=significant_regression if args.fail_on_significant_regression else None,
        significant_score_regression=significant_score_regression if args.fail_on_significant_regression else None,
        per_category_confidence=per_category_confidence if args.fail_on_significant_regression else None,
    )
    if gate_failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for msg in gate_failures:
            print(f"  - {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
