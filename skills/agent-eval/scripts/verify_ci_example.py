#!/usr/bin/env python3
"""
verify_ci_example.py - CI self-check for examples/README.md's "Statistical
confidence" section.

Same bit-rot risk verify_trajectory_example.py already guards against for
the trajectory example: examples/README.md quotes an exact bootstrap
confidence interval and p-value for results_regressed.jsonl vs.
results_baseline.jsonl — both the pass-rate paired diff AND the mean-score
paired diff (added after the pass-rate-only version showed a lower p-value
than the unbinarized score comparison on this same data) — and states
--fail-on-significant-regression exits 0 on that pair specifically *because*
neither is significant at the aggregate level. A silent edit to either
results file, or a change to bootstrap_stats.py's math, could quietly make
that documented story false (or worse, make the gate actually fire) without
anything noticing.

Deterministic by construction: bootstrap_stats.py seeds its RNG (default
12345), so this reproduces bit-for-bit given the committed inputs and
score_eval.py's own default --n-boot/--boot-seed — no tolerance/fuzz needed.

Stdlib only. Run: python verify_ci_example.py"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(HERE, "..", "examples")

spec = importlib.util.spec_from_file_location("score_eval", os.path.join(HERE, "score_eval.py"))
score_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score_eval)

# From examples/README.md's "Statistical confidence" section — the documented
# contract this script exists to guard.
EXPECTED_PASS_RATE_DIFF = {
    "point_a": 0.8, "point_b": 0.9, "diff": -0.1,
    "ci_lo": -0.3, "ci_hi": 0.1, "p_value": 0.421,
    "significant_at_0.05": False, "n": 20,
}
EXPECTED_MEAN_SCORE_DIFF = {
    "point_a": 0.8375, "point_b": 0.89, "diff": -0.0525,
    "ci_lo": -0.15, "ci_hi": 0.03, "p_value": 0.255,
    "significant_at_0.05": False, "n": 20,
}


def _check(actual, expected, label, failures):
    if actual is None:
        failures.append(f"compute_confidence() returned no {label} at all (no matched case ids?)")
        return
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if actual_value != expected_value:
            failures.append(f"{label}.{key}: examples/README.md documents {expected_value!r}, actual is {actual_value!r}")


def main() -> int:
    results = score_eval.load_results(os.path.join(EXAMPLES_DIR, "results_regressed.jsonl"))
    baseline = score_eval.load_results(os.path.join(EXAMPLES_DIR, "results_baseline.jsonl"))
    confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
    pass_rate_diff = confidence.get("paired_pass_rate_diff")
    score_diff = confidence.get("paired_mean_score_diff")

    failures = []
    _check(pass_rate_diff, EXPECTED_PASS_RATE_DIFF, "paired_pass_rate_diff", failures)
    _check(score_diff, EXPECTED_MEAN_SCORE_DIFF, "paired_mean_score_diff", failures)

    if failures:
        print("FAILED: results_regressed.jsonl/results_baseline.jsonl's --ci output no longer matches examples/README.md's documented numbers:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("Update examples/README.md's Statistical confidence section (and this script's EXPECTED_* constants) if the change was deliberate.", file=sys.stderr)
        return 1

    gate_would_fire = any(
        d is not None and d["diff"] < 0 and d["significant_at_0.05"]
        for d in (pass_rate_diff, score_diff)
    )
    if gate_would_fire:
        print("FAILED: --fail-on-significant-regression would now fire on this example, contradicting examples/README.md's documented exit-0 story.", file=sys.stderr)
        return 1

    print(
        f"OK: statistical-confidence example matches examples/README.md "
        f"(pass-rate p={pass_rate_diff['p_value']}, score p={score_diff['p_value']}, neither significant, gate exits 0 as documented)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
