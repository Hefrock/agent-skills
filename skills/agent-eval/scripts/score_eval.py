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

Input format (JSONL, one JSON object per line):
    {"id": "case_001", "score": 1.0, "category": "format", "rationale": "..."}
    {"id": "case_002", "score": 0.0, "category": "accuracy", "rationale": "..."}

`score` can be a float (0-1) or a bool (true/false treated as 1.0/0.0).
`category` and `rationale` are optional but recommended.

Stdlib only — no dependencies to install.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict


def load_results(path):
    results = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: skipping malformed line {lineno} in {path}: {e}", file=sys.stderr)
                continue
            if "id" not in obj or "score" not in obj:
                print(f"Warning: skipping line {lineno} in {path} — missing 'id' or 'score'", file=sys.stderr)
                continue
            score = obj["score"]
            if isinstance(score, bool):
                score = 1.0 if score else 0.0
            obj["score"] = float(score)
            results.append(obj)
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


def check_gates(summary, regressions, fail_under, fail_on_regression):
    """Return a list of gate-failure messages (empty list means all gates pass).

    Used to turn a report into a CI pass/fail. Kept separate from print_report
    so it can be unit-tested and so reporting never depends on gate config.
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
    return failures


def print_report(summary, results, regressions, threshold):
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
    args = parser.parse_args()

    results = load_results(args.results)
    summary = summarize(results, args.threshold)

    regressions = []
    if args.baseline:
        baseline_results = load_results(args.baseline)
        regressions = find_regressions(results, baseline_results, args.threshold)

    print_report(summary, results, regressions, args.threshold)

    if args.json_out and summary:
        output = dict(summary)
        output["regressions"] = regressions
        with open(args.json_out, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Summary written to {args.json_out}")

    gate_failures = check_gates(summary, regressions, args.fail_under, args.fail_on_regression)
    if gate_failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for msg in gate_failures:
            print(f"  - {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
