#!/usr/bin/env python3
"""
verify_ci_example.py - CI self-check for examples/README.md's "Statistical
confidence" section.

Same bit-rot risk verify_trajectory_example.py already guards against for
the trajectory example: examples/README.md quotes an exact bootstrap
confidence interval and p-value for results_regressed.jsonl vs.
results_baseline.jsonl, and states --fail-on-significant-regression exits 0
on that pair specifically *because* it isn't significant at the aggregate
level — the whole point of that doc section. A silent edit to either
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
EXPECTED = {
    "point_a": 0.8, "point_b": 0.9, "diff": -0.1,
    "ci_lo": -0.3, "ci_hi": 0.1, "p_value": 0.421,
    "significant_at_0.05": False, "n": 20,
}


def main() -> int:
    results = score_eval.load_results(os.path.join(EXAMPLES_DIR, "results_regressed.jsonl"))
    baseline = score_eval.load_results(os.path.join(EXAMPLES_DIR, "results_baseline.jsonl"))
    confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
    diff = confidence.get("paired_pass_rate_diff")

    failures = []
    if diff is None:
        failures.append("compute_confidence() returned no paired_pass_rate_diff at all (no matched case ids?)")
    else:
        for key, expected in EXPECTED.items():
            actual = diff.get(key)
            if actual != expected:
                failures.append(f"{key}: examples/README.md documents {expected!r}, actual is {actual!r}")

    if failures:
        print("FAILED: results_regressed.jsonl/results_baseline.jsonl's --ci output no longer matches examples/README.md's documented numbers:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("Update examples/README.md's Statistical confidence section (and this script's EXPECTED constants) if the change was deliberate.", file=sys.stderr)
        return 1

    gate_would_fire = diff["diff"] < 0 and diff["significant_at_0.05"]
    if gate_would_fire:
        print("FAILED: --fail-on-significant-regression would now fire on this example, contradicting examples/README.md's documented exit-0 story.", file=sys.stderr)
        return 1

    print(f"OK: statistical-confidence example matches examples/README.md (p={diff['p_value']}, not significant, gate exits 0 as documented).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
