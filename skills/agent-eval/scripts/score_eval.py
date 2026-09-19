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
signal but not to the raw-score one. The gate fires if either is
significant; all three gates (`--fail-on-regression` and both halves of
`--fail-on-significant-regression`) answer different questions and are
meant to be used together, not as substitutes for each other.

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
    if significant_regression is not None and significant_regression["diff"] < 0 and significant_regression["significant_at_0.05"]:
        failures.append(
            f"--fail-on-significant-regression (pass rate): {significant_regression['point_b']:.3f} -> "
            f"{significant_regression['point_a']:.3f} is statistically significant (p={significant_regression['p_value']}, "
            f"n={significant_regression['n']}), not just a threshold-crossing on noise"
        )
    if significant_score_regression is not None and significant_score_regression["diff"] < 0 and significant_score_regression["significant_at_0.05"]:
        failures.append(
            f"--fail-on-significant-regression (mean score): {significant_score_regression['point_b']:.3f} -> "
            f"{significant_score_regression['point_a']:.3f} is statistically significant (p={significant_score_regression['p_value']}, "
            f"n={significant_score_regression['n']}) — caught here even though the pass-rate test alone might not"
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


def print_report(summary, results, regressions, threshold, cost_regression=None, latency_regression=None, confidence=None):
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

    if n < 20:
        print(f"⚠ Small sample (n={n}) — treat the pass rate as directional, not precise.")

    if confidence is not None:
        pr_ci = confidence["pass_rate_ci"]
        ms_ci = confidence["mean_score_ci"]
        print(f"\n95% CI (bootstrap, {pr_ci['n_boot']} resamples):")
        print(f"  Pass rate:  [{pr_ci['ci_lo'] * 100:.1f}%, {pr_ci['ci_hi'] * 100:.1f}%]")
        print(f"  Mean score: [{ms_ci['ci_lo']:.3f}, {ms_ci['ci_hi']:.3f}]")
        diff = confidence.get("paired_pass_rate_diff")
        if diff is not None:
            verdict = "SIGNIFICANT" if diff["significant_at_0.05"] else "not significant"
            print(
                f"  Pass rate vs baseline: {diff['point_b'] * 100:.1f}% -> {diff['point_a'] * 100:.1f}% "
                f"(diff {diff['diff'] * 100:+.1f}pp, 95% CI [{diff['ci_lo'] * 100:+.1f}pp, {diff['ci_hi'] * 100:+.1f}pp], "
                f"p={diff['p_value']}, {verdict} at alpha=0.05, n={diff['n']} matched case(s))"
            )
        score_diff = confidence.get("paired_mean_score_diff")
        if score_diff is not None:
            verdict = "SIGNIFICANT" if score_diff["significant_at_0.05"] else "not significant"
            print(
                f"  Mean score vs baseline: {score_diff['point_b']:.3f} -> {score_diff['point_a']:.3f} "
                f"(diff {score_diff['diff']:+.3f}, 95% CI [{score_diff['ci_lo']:+.3f}, {score_diff['ci_hi']:+.3f}], "
                f"p={score_diff['p_value']}, {verdict} at alpha=0.05, n={score_diff['n']} matched case(s)) "
                f"— unbinarized, catches magnitude the pass-rate test above can miss"
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
    if (args.ci or args.fail_on_significant_regression) and summary is not None:
        confidence = compute_confidence(
            results, args.threshold, baseline_results=baseline_results if args.baseline else None,
            n_boot=args.n_boot, boot_seed=args.boot_seed,
        )
        significant_regression = confidence.get("paired_pass_rate_diff")
        significant_score_regression = confidence.get("paired_mean_score_diff")

    print_report(summary, results, regressions, args.threshold, cost_regression=cost_regression, latency_regression=latency_regression, confidence=confidence)

    if args.json_out and summary:
        output = dict(summary)
        output["regressions"] = regressions
        if confidence is not None:
            output["confidence"] = confidence
        with open(args.json_out, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Summary written to {args.json_out}")

    gate_failures = check_gates(
        summary, regressions, args.fail_under, args.fail_on_regression,
        cost_regression=cost_regression, latency_regression=latency_regression,
        fail_if_mean_cost_above=args.fail_if_mean_cost_above, fail_if_mean_latency_above=args.fail_if_mean_latency_above,
        significant_regression=significant_regression if args.fail_on_significant_regression else None,
        significant_score_regression=significant_score_regression if args.fail_on_significant_regression else None,
    )
    if gate_failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for msg in gate_failures:
            print(f"  - {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
