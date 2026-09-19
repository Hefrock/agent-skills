#!/usr/bin/env python3
"""
verify_ci_example.py - CI self-check for examples/README.md's "Statistical
confidence" section.

Same bit-rot risk verify_trajectory_example.py already guards against for
the trajectory example: examples/README.md quotes exact bootstrap numbers
for results_regressed.jsonl vs. results_baseline.jsonl at three levels —
the run-wide pass-rate diff, the run-wide mean-score diff, and the
`accuracy` category's own paired diffs — and states that, after Bonferroni
correction across all 10 tests examined together, NONE of them clear the
corrected bar (alpha=0.005), so --fail-on-significant-regression correctly
exits 0 on this example. That's a deliberate, documented reversal from an
earlier edition of this doc (before the correction existed, the accuracy
category's uncorrected p=0.043 alone tripped the gate) — this script guards
the corrected story, not the old one. A silent edit to either results file,
or a change to bootstrap_stats.py's math, score_eval.py's grouping, or the
correction itself, could quietly make that documented story false without
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
    "n": 20,
}
EXPECTED_MEAN_SCORE_DIFF = {
    "point_a": 0.8375, "point_b": 0.89, "diff": -0.0525,
    "ci_lo": -0.15, "ci_hi": 0.03, "p_value": 0.255,
    "n": 20,
}
EXPECTED_ACCURACY_PASS_RATE_DIFF = {
    "point_a": 0.625, "point_b": 1.0, "diff": -0.375,
    "p_value": 0.043, "n": 8,
}
EXPECTED_ACCURACY_MEAN_SCORE_DIFF = {
    "point_a": 0.725, "point_b": 0.9125, "diff": -0.1875,
    "p_value": 0.043, "n": 8,
}
EXPECTED_N_TESTS = 10
EXPECTED_BONFERRONI_ALPHA = 0.005


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
    per_category = score_eval.compute_per_category_confidence(results, threshold=0.7, baseline_results=baseline)
    n_tests, alpha = score_eval.apply_multiple_comparisons_correction(confidence, per_category)

    pass_rate_diff = confidence.get("paired_pass_rate_diff")
    score_diff = confidence.get("paired_mean_score_diff")
    accuracy = per_category.get("accuracy", {})
    accuracy_pass_rate_diff = accuracy.get("paired_pass_rate_diff")
    accuracy_score_diff = accuracy.get("paired_mean_score_diff")

    failures = []
    _check(pass_rate_diff, EXPECTED_PASS_RATE_DIFF, "run-wide paired_pass_rate_diff", failures)
    _check(score_diff, EXPECTED_MEAN_SCORE_DIFF, "run-wide paired_mean_score_diff", failures)
    _check(accuracy_pass_rate_diff, EXPECTED_ACCURACY_PASS_RATE_DIFF, "accuracy category paired_pass_rate_diff", failures)
    _check(accuracy_score_diff, EXPECTED_ACCURACY_MEAN_SCORE_DIFF, "accuracy category paired_mean_score_diff", failures)
    if n_tests != EXPECTED_N_TESTS:
        failures.append(f"n_tests: examples/README.md documents {EXPECTED_N_TESTS}, actual is {n_tests}")
    if alpha != EXPECTED_BONFERRONI_ALPHA:
        failures.append(f"bonferroni_alpha: examples/README.md documents {EXPECTED_BONFERRONI_ALPHA}, actual is {alpha}")

    if failures:
        print("FAILED: results_regressed.jsonl/results_baseline.jsonl's --ci output no longer matches examples/README.md's documented numbers:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("Update examples/README.md's Statistical confidence section (and this script's EXPECTED_* constants) if the change was deliberate.", file=sys.stderr)
        return 1

    # The documented story, post-correction: NOTHING in this example clears
    # the corrected bar — including the accuracy category, which cleared
    # the old fixed 0.05 (p=0.043) before this correction existed. That's
    # not a "would fire" check on the raw p-values anymore; it's a check
    # that every diff's own significant_after_correction flag (computed by
    # apply_multiple_comparisons_correction() above) is consistently False.
    all_diffs = [pass_rate_diff, score_diff, accuracy_pass_rate_diff, accuracy_score_diff]
    still_fires = any(d["diff"] < 0 and d["significant_after_correction"] for d in all_diffs)
    if still_fires:
        print("FAILED: a signal in this example is now significant after correction, contradicting examples/README.md's documented exit-0 story.", file=sys.stderr)
        return 1

    print(
        "OK: statistical-confidence example matches examples/README.md "
        f"({n_tests} tests -> Bonferroni alpha={alpha}; run-wide pass-rate p={pass_rate_diff['p_value']}, "
        f"run-wide score p={score_diff['p_value']}, accuracy category p={accuracy_pass_rate_diff['p_value']} on both signals — "
        "none clear the corrected bar, gate correctly exits 0)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
