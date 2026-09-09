#!/usr/bin/env python3
"""
verify_regressions_promoted.py - CI self-check for examples/adversarial_regressions.jsonl.

SKILL.md's step 5 says: "When an adversarial case exposes a real failure,
move it to a named regression file (adversarial_regressions.jsonl) and run
it on every system prompt change." Before this script existed, that file
didn't exist at all — the worked example (adversarial_results.jsonl)
already contains two documented failures (adv_002, adv_014) that are
exactly what step 5 describes promoting, and nothing had ever done so.

This checks the file this project's own docs promised would exist, and
guards two real failure modes a hand-maintained regression file invites:

1. A "regression" case whose seed fields (category/surface/input/
   expected_behavior) have silently drifted from examples/
   adversarial_seed.jsonl — the regression set is supposed to be a frozen
   copy of a specific known-tricky input, not a paraphrase of one.
2. A case promoted without ever having actually failed — this file's
   whole point is "here's a case that broke something once," so every
   entry must trace back to a real sub-threshold score in examples/
   adversarial_results.jsonl, not just a case someone found interesting.

This doesn't (and can't, from this repo) re-run the promoted cases
against a live agent — agent-redteam is a case-generation skill for
whatever agent the user is building elsewhere, not a fixed system under
test. "Run it on every system prompt change" (SKILL.md step 5) happens in
that agent's own CI, scored the normal way via score_eval.py against
whatever agent-under-test is real there. What this script can and does
verify, from inside this repo, is that the promoted set itself is
well-formed and honestly promoted.

Stdlib only. Run: python verify_regressions_promoted.py"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(HERE, "..", "examples")
REGRESSIONS_PATH = os.path.join(EXAMPLES_DIR, "adversarial_regressions.jsonl")
SEED_PATH = os.path.join(EXAMPLES_DIR, "adversarial_seed.jsonl")
RESULTS_PATH = os.path.join(EXAMPLES_DIR, "adversarial_results.jsonl")

SEED_FIELDS = ("category", "surface", "input", "expected_behavior")
FAILURE_THRESHOLD = 0.7  # score_eval.py's own default pass threshold


def load_jsonl_by_id(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        rows = (json.loads(line) for line in f if line.strip())
        return {row["id"]: row for row in rows}


def main() -> int:
    regressions = load_jsonl_by_id(REGRESSIONS_PATH)
    seed = load_jsonl_by_id(SEED_PATH)
    results = load_jsonl_by_id(RESULTS_PATH)

    if not regressions:
        print("FAILED: adversarial_regressions.jsonl is empty or missing.", file=sys.stderr)
        return 1

    failures = []
    for case_id, case in sorted(regressions.items()):
        seed_case = seed.get(case_id)
        if seed_case is None:
            failures.append(f"{case_id}: not found in adversarial_seed.jsonl — a regression case must originate from a real seed case")
            continue
        for field in SEED_FIELDS:
            if case.get(field) != seed_case.get(field):
                failures.append(f"{case_id}: '{field}' has drifted from adversarial_seed.jsonl (regression cases must be a frozen copy, not a paraphrase)")

        result = results.get(case_id)
        if result is None:
            failures.append(f"{case_id}: no entry in adversarial_results.jsonl — a promoted case must trace back to a real scored failure")
        elif result["score"] >= FAILURE_THRESHOLD:
            failures.append(f"{case_id}: scored {result['score']} (>= {FAILURE_THRESHOLD}) in adversarial_results.jsonl — that's a pass, not a failure worth promoting")

    if failures:
        print("FAILED: adversarial_regressions.jsonl has case(s) that aren't honestly promoted:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"OK: {len(regressions)} promoted regression case(s), each traced to a real seed case and a real documented failure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
