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

Input format (JSONL, one JSON object per line):
    {"id": "case_001", "score": 1.0, "category": "format", "rationale": "..."}
    {"id": "case_002", "score": 0.0, "category": "accuracy", "rationale": "..."}

`score` can be a float (0-1) or a bool (true/false treated as 1.0/0.0).
`category` and `rationale` are optional but recommended.

Stdlib only — no dependencies to install.
"""

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

# realpath, not abspath: this file is also reached via a real symlink
# (skills/agent-redteam/scripts/score_eval.py -> ../../agent-eval/scripts/
# score_eval.py, see that skill's Files table) — __file__ under a symlink
# invocation reflects the symlink's own path, not the real one, so abspath
# alone would look for jsonl_io.py next to the symlink and fail to find
# it. realpath resolves through the symlink to this file's actual
# directory regardless of which path was used to invoke it.
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import jsonl_io  # noqa: E402


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


def summarize(results, threshold):
    if not results:
        return None
    scores = [r["score"] for r in results]
    passed = [r for r in results if r["score"] >= threshold]
    by_category = defaultdict(list)
    for r in results:
        by_category[r.get("category", "uncategorized")].append(r)

    has_cost = any("cost_usd" in r for r in results)
    has_latency = any("latency_ms" in r for r in results)

    cat_stats = {}
    for cat, cat_results in sorted(by_category.items()):
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
        cat_stats[cat] = stat

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
):
    """Return a list of gate-failure messages (empty list means all gates pass).

    Used to turn a report into a CI pass/fail. Kept separate from print_report
    so it can be unit-tested and so reporting never depends on gate config.

    cost_regression/latency_regression are find_metric_regression()'s
    return values (or None) — computed by the caller, not here, so this
    function stays a pure function over already-computed inputs, same
    convention as `regressions` above (find_regressions()'s output, not
    recomputed inside check_gates either).
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


def print_report(summary, results, regressions, threshold, cost_regression=None, latency_regression=None):
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
    args = parser.parse_args()

    if (args.fail_on_cost_regression or args.fail_on_latency_regression) and not args.baseline:
        print("--fail-on-cost-regression/--fail-on-latency-regression require --baseline.", file=sys.stderr)
        sys.exit(2)

    results = load_results(args.results)
    summary = summarize(results, args.threshold)

    regressions = []
    baseline_summary = None
    if args.baseline:
        baseline_results = load_results(args.baseline)
        regressions = find_regressions(results, baseline_results, args.threshold)
        baseline_summary = summarize(baseline_results, args.threshold)

    cost_regression = find_metric_regression(summary, baseline_summary, "mean_cost_usd", args.cost_regression_tolerance) if args.fail_on_cost_regression else None
    latency_regression = find_metric_regression(summary, baseline_summary, "mean_latency_ms", args.latency_regression_tolerance) if args.fail_on_latency_regression else None

    print_report(summary, results, regressions, args.threshold, cost_regression=cost_regression, latency_regression=latency_regression)

    if args.json_out and summary:
        output = dict(summary)
        output["regressions"] = regressions
        with open(args.json_out, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Summary written to {args.json_out}")

    gate_failures = check_gates(
        summary, regressions, args.fail_under, args.fail_on_regression,
        cost_regression=cost_regression, latency_regression=latency_regression,
        fail_if_mean_cost_above=args.fail_if_mean_cost_above, fail_if_mean_latency_above=args.fail_if_mean_latency_above,
    )
    if gate_failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for msg in gate_failures:
            print(f"  - {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
