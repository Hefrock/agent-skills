#!/usr/bin/env python3
"""jsonl_io.py - The one shared JSONL-reading primitive underlying every
entry point in this skill.

Before this module existed, score_eval.load_results(), run_judge.
load_cases(), and calibrate_judge.load_scores_by_id() each independently
implemented the same ~10-line pattern (open the file, skip blank lines,
catch json.JSONDecodeError and warn-skip, warn-skip a line missing a
required key) — found via a repo-pincer review that went looking for
exactly this kind of duplication risk. No bug had come from the drift
yet, but nothing would have caught one call site's fix (like run_judge.py's
real malformed-line handling improvements) failing to reach the other two.

load_jsonl() is deliberately narrow: it validates and returns raw dicts,
nothing more. It does NOT coerce score types (score_eval.py's bool->float
handling) or reshape the result (calibrate_judge.py's list->dict-by-id
indexing) — those are real per-caller differences, not duplicated logic,
and forcing them into one rigid shared function would trade one kind of
coupling for a worse one. Callers do that domain-specific step themselves
on the validated rows this returns.

Not a script — imported only, no CLI, no __main__ block.

stdlib only, matching this repo's other reference tooling."""

import json
import sys


def load_jsonl(path: str, required_keys: tuple[str, ...] = ("id",)) -> list[dict]:
    """Reads path as JSONL, one JSON object per line. A blank line is
    silently skipped (not a warning — trailing/blank lines are normal in
    hand-edited files). A line that isn't valid JSON, or is valid JSON
    missing one or more of required_keys, is skipped with a warning to
    stderr and never included in the result — never raises, so one bad
    line in an otherwise-good file doesn't lose every row after it."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: skipping malformed line {lineno} in {path}: {e}", file=sys.stderr)
                continue
            missing = [k for k in required_keys if k not in obj]
            if missing:
                joined = " or ".join(repr(k) for k in missing)
                print(f"Warning: skipping line {lineno} in {path} — missing {joined}", file=sys.stderr)
                continue
            rows.append(obj)
    return rows
