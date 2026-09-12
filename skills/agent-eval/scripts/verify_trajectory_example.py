#!/usr/bin/env python3
"""
verify_trajectory_example.py - CI self-check for examples/trajectory_example.jsonl.

ci.yml already self-checks results_regressed.jsonl ("the worked example is
designed to fail the gate — confirm it still does, so the example can't
silently bit-rot"). The trajectory worked example has an equally specific,
equally documented contract — examples/README.md's per-case table (each
case's exact score and what it demonstrates) plus "50% pass rate (3/6),
mean score 0.67" — but nothing verified it stayed true. A silent edit to
trajectory_example.jsonl (or a change to score_eval.py's scoring math)
could quietly break the story examples/README.md tells without any test
noticing, exactly the bit-rot risk the regression self-check already
guards against elsewhere.

Checks the exact per-case scores against examples/README.md's documented
table (not just the aggregate pass rate/mean, which could stay the same
by coincidence while individual cases drifted), plus the aggregate
pass_rate and pass_count.

Stdlib only. Run: python verify_trajectory_example.py"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE_PATH = os.path.join(HERE, "..", "examples", "trajectory_example.jsonl")

# From examples/README.md's "Trajectory example" table — the documented
# contract this script exists to guard.
EXPECTED_SCORES = {
    "traj_01": 1.0,
    "traj_02": 0.4,
    "traj_03": 0.9,
    "traj_04": 0.2,
    "traj_05": 1.0,
    "traj_06": 0.5,
}
EXPECTED_PASS_COUNT = 3  # at score_eval.py's default 0.7 threshold
EXPECTED_MEAN_SCORE = 0.67  # examples/README.md states this rounded to 2 places


def main() -> int:
    with open(EXAMPLE_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    actual_scores = {r["id"]: r["score"] for r in rows}
    failures = []

    if set(actual_scores) != set(EXPECTED_SCORES):
        failures.append(f"case IDs changed: expected {sorted(EXPECTED_SCORES)}, got {sorted(actual_scores)}")
    else:
        for case_id, expected_score in EXPECTED_SCORES.items():
            actual_score = actual_scores[case_id]
            if actual_score != expected_score:
                failures.append(f"{case_id}: examples/README.md documents {expected_score}, file now has {actual_score}")

    pass_count = sum(1 for s in actual_scores.values() if s >= 0.7)
    if pass_count != EXPECTED_PASS_COUNT:
        failures.append(f"pass count changed: examples/README.md documents {EXPECTED_PASS_COUNT}/6, actual is {pass_count}/6")

    mean_score = round(sum(actual_scores.values()) / len(actual_scores), 2) if actual_scores else 0.0
    if mean_score != EXPECTED_MEAN_SCORE:
        failures.append(f"mean score changed: examples/README.md documents {EXPECTED_MEAN_SCORE}, actual is {mean_score}")

    if failures:
        print("FAILED: trajectory_example.jsonl no longer matches examples/README.md's documented table:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("Update examples/README.md's table (and this script's EXPECTED_* constants) if the change was deliberate.", file=sys.stderr)
        return 1

    print(f"OK: trajectory_example.jsonl matches examples/README.md's documented table ({EXPECTED_PASS_COUNT}/6 pass, mean {EXPECTED_MEAN_SCORE}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
