#!/usr/bin/env python3
"""
calibrate_judge.py - Compare judge scores to hand-scored cases and log the
result, so SKILL.md step 5's calibration step actually gets done instead
of just documented.

Step 5 says: "Every 25-50 judge calls (or whenever you revise the judge
prompt), hand-score 5-10 cases yourself and compare to the judge's
scores. If the mean delta exceeds 0.2, revise the judge prompt. Record
the last calibration date in references/llm-judge-prompt.md." Before this
script existed, every part of that was manual — computing the delta by
eye, deciding whether it crossed 0.2, and editing the log table by hand.
references/llm-judge-prompt.md's own calibration log table still reads
"not yet calibrated" as of this writing, which is the actual evidence
this step has never been exercised on a real eval in this project.

Usage:
    python calibrate_judge.py judge_results.jsonl human_scores.jsonl
    python calibrate_judge.py judge_results.jsonl human_scores.jsonl --threshold 0.15
    python calibrate_judge.py judge_results.jsonl human_scores.jsonl --update-log references/llm-judge-prompt.md

Both input files are JSONL with at least {"id": ..., "score": ...} per
line — judge_results.jsonl is normal score_eval.py-schema output;
human_scores.jsonl is the same shape for whichever 5-10 cases you
hand-scored (only those IDs need to be present — this compares the
intersection, not the full eval set).

Stdlib only. Run: python calibrate_judge.py ..."""

import argparse
import os
import re
import statistics
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jsonl_io  # noqa: E402

DEFAULT_THRESHOLD = 0.2
LOG_HEADER = "| Date | Cases checked | Mean delta vs human | Action taken |"
LOG_SEPARATOR = "|---|---|---|---|"
PLACEHOLDER_ROW = "| — | — | — | not yet calibrated |"


def load_scores_by_id(path: str) -> dict:
    """{id: score} — built on jsonl_io.load_jsonl(), the shared primitive
    score_eval.load_results() and run_judge.load_cases() also build on.
    Never silently treats a missing score as 0; that row is dropped by
    load_jsonl()'s required_keys check instead."""
    rows = jsonl_io.load_jsonl(path, required_keys=("id", "score"))
    return {row["id"]: float(row["score"]) for row in rows}


def compute_deltas(judge_scores: dict, human_scores: dict) -> list[dict]:
    """Returns one row per case ID present in BOTH inputs — a case only
    hand-scored (or only judge-scored) contributes nothing to a
    judge-vs-human comparison, so it's silently excluded rather than
    padded with a guess. Sorted by delta descending — the worst-agreement
    cases are what a prompt revision should actually look at."""
    common_ids = sorted(set(judge_scores) & set(human_scores))
    rows = [
        {"id": case_id, "judge_score": judge_scores[case_id], "human_score": human_scores[case_id], "delta": abs(judge_scores[case_id] - human_scores[case_id])}
        for case_id in common_ids
    ]
    return sorted(rows, key=lambda r: r["delta"], reverse=True)


def summarize_calibration(deltas: list[dict], threshold: float) -> dict:
    if not deltas:
        return {"cases_checked": 0, "mean_delta": None, "over_threshold": None}
    mean_delta = statistics.mean(r["delta"] for r in deltas)
    return {"cases_checked": len(deltas), "mean_delta": mean_delta, "over_threshold": mean_delta > threshold}


def update_calibration_log(log_path: str, new_row: str) -> None:
    """Replaces the "not yet calibrated" placeholder row on first use, or
    appends after the existing table rows on every calibration after
    that — a real, append-only history of every calibration run, not
    just the most recent one, so a reviewer can see whether calibration
    has actually been happening on the documented 25-50-call cadence."""
    with open(log_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    try:
        header_idx = lines.index(LOG_HEADER)
    except ValueError:
        raise ValueError(f"Couldn't find the calibration log table header in {log_path} — has it been reworded?")
    if lines[header_idx + 1] != LOG_SEPARATOR:
        raise ValueError(f"Calibration log table in {log_path} has an unexpected shape after the header line.")

    # Existing data rows: every contiguous "| ... |" line right after the
    # separator. Stop at the first line that isn't a table row.
    row_start = header_idx + 2
    row_end = row_start
    while row_end < len(lines) and re.match(r"^\|.*\|$", lines[row_end]):
        row_end += 1
    existing_rows = lines[row_start:row_end]

    if existing_rows == [PLACEHOLDER_ROW]:
        new_rows = [new_row]
    else:
        new_rows = existing_rows + [new_row]

    updated = lines[:row_start] + new_rows + lines[row_end:]
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("judge_results", help="Path to judge-scored results (score_eval.py-schema JSONL).")
    parser.add_argument("human_scores", help="Path to hand-scored cases for the same IDs (same JSONL schema; a subset is fine).")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"Mean delta above this means 'revise the judge prompt' (default: {DEFAULT_THRESHOLD}, per SKILL.md step 5).")
    parser.add_argument("--update-log", help="Path to references/llm-judge-prompt.md (or similar) — append this run's result to its calibration log table.")
    parser.add_argument("--action", help="Override the auto-generated 'Action taken' log text.")
    args = parser.parse_args()

    judge_scores = load_scores_by_id(args.judge_results)
    human_scores = load_scores_by_id(args.human_scores)
    deltas = compute_deltas(judge_scores, human_scores)
    summary = summarize_calibration(deltas, args.threshold)

    if summary["cases_checked"] == 0:
        print("No case IDs in common between the two files — nothing to calibrate.", file=sys.stderr)
        return 1

    print(f"Calibration: {summary['cases_checked']} case(s) checked, mean delta {summary['mean_delta']:.3f} (threshold {args.threshold})")
    print("\nPer-case (worst agreement first):")
    for r in deltas:
        print(f"  {r['id']}: judge={r['judge_score']:.2f} human={r['human_score']:.2f} delta={r['delta']:.2f}")

    if summary["over_threshold"]:
        default_action = f"ACTION NEEDED: mean delta {summary['mean_delta']:.3f} exceeds {args.threshold} — revise the judge prompt"
        print(f"\n⚠ {default_action}")
    else:
        default_action = f"none needed (mean delta {summary['mean_delta']:.3f} within {args.threshold})"
        print(f"\nOK: {default_action}")

    if args.update_log:
        action = args.action or default_action
        new_row = f"| {date.today().isoformat()} | {summary['cases_checked']} | {summary['mean_delta']:.3f} | {action} |"
        try:
            update_calibration_log(args.update_log, new_row)
        except ValueError as e:
            print(f"\nWarning: couldn't update the log: {e}", file=sys.stderr)
            return 1
        print(f"\nLogged to {args.update_log}")

    return 1 if summary["over_threshold"] else 0


if __name__ == "__main__":
    sys.exit(main())
