#!/usr/bin/env python3
"""
verify_ci_example.py - CI self-check for examples/README.md's "Statistical
confidence" section.

Same bit-rot risk verify_trajectory_example.py already guards against for
the trajectory example: examples/README.md quotes exact bootstrap numbers
for results_regressed.jsonl vs. results_baseline.jsonl at three levels —
the run-wide pass-rate diff, the run-wide mean-score diff, and the
`accuracy` category's own paired diffs — and states the run-wide aggregate
is NOT significant while the `accuracy` category IS, so
--fail-on-significant-regression correctly exits 1 (via the per-category
signal) even though both run-wide signals alone would exit 0. A silent
edit to either results file, or a change to bootstrap_stats.py's math or
score_eval.py's grouping, could quietly make that documented story false
(numbers drifting, or worse, the wrong half of the story flipping) without
anything noticing.

Deterministic by construction: bootstrap_stats.py seeds its RNG (default
12345) and sums with math.fsum() (not the builtin sum(), which changed its
float-accumulation algorithm between Python 3.11 and 3.12 -- see
bootstrap_stats.py's module docstring for the confirmed cross-version
mismatch that motivated the switch), so this reproduces bit-for-bit given
the committed inputs and score_eval.py's own default --n-boot/--boot-seed
-- no tolerance/fuzz needed, and confirmed identical across Python 3.10
through 3.13.

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
EXPECTED_ACCURACY_PASS_RATE_DIFF = {
    "point_a": 0.625, "point_b": 1.0, "diff": -0.375,
    "p_value": 0.043, "significant_at_0.05": True, "n": 8,
}
EXPECTED_ACCURACY_MEAN_SCORE_DIFF = {
    "point_a": 0.725, "point_b": 0.9125, "diff": -0.1875,
    "p_value": 0.043, "significant_at_0.05": True, "n": 8,
}


def _check(actual, expected, label, failures):
    if actual is None:
        failures.append(f"{label} is None (missing entirely)")
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

    per_category = score_eval.compute_per_category_confidence(results, threshold=0.7, baseline_results=baseline)
    accuracy = per_category.get("accuracy", {})
    accuracy_pass_rate_diff = accuracy.get("paired_pass_rate_diff")
    accuracy_score_diff = accuracy.get("paired_mean_score_diff")

    failures = []
    _check(pass_rate_diff, EXPECTED_PASS_RATE_DIFF, "run-wide paired_pass_rate_diff", failures)
    _check(score_diff, EXPECTED_MEAN_SCORE_DIFF, "run-wide paired_mean_score_diff", failures)
    _check(accuracy_pass_rate_diff, EXPECTED_ACCURACY_PASS_RATE_DIFF, "accuracy category paired_pass_rate_diff", failures)
    _check(accuracy_score_diff, EXPECTED_ACCURACY_MEAN_SCORE_DIFF, "accuracy category paired_mean_score_diff", failures)

    if failures:
        print("FAILED: results_regressed.jsonl/results_baseline.jsonl's --ci output no longer matches examples/README.md's documented numbers:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("Update examples/README.md's Statistical confidence section (and this script's EXPECTED_* constants) if the change was deliberate.", file=sys.stderr)
        return 1

    def is_significant_regression(d):
        return d is not None and d["diff"] < 0 and d["significant_at_0.05"]

    run_wide_would_fire = is_significant_regression(pass_rate_diff) or is_significant_regression(score_diff)
    if run_wide_would_fire:
        print("FAILED: a run-wide signal is now significant, contradicting examples/README.md's documented aggregate-dilution story.", file=sys.stderr)
        return 1

    per_category_would_fire = is_significant_regression(accuracy_pass_rate_diff) or is_significant_regression(accuracy_score_diff)
    if not per_category_would_fire:
        print("FAILED: --fail-on-significant-regression would now exit 0, contradicting examples/README.md's documented exit-1 story (the per-category signal is supposed to catch this).", file=sys.stderr)
        return 1

    print(
        "OK: statistical-confidence example matches examples/README.md "
        f"(run-wide pass-rate p={pass_rate_diff['p_value']}, run-wide score p={score_diff['p_value']}, "
        f"neither significant; accuracy category p={accuracy_pass_rate_diff['p_value']} on both signals, "
        "significant — gate correctly exits 1 via the per-category check)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
