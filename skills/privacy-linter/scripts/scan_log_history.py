#!/usr/bin/env python3
"""Rolling privacy-linter scan history, in agent-eval's own JSONL schema —
the same "bridge, not reimplementation" pattern broadcast's own
qa_gate_history.py already established for its QA gate.

scan_diff.py's --log-dir writes one timestamped JSON record per scan run
(the run's findings only — never the scanned content itself, so this log can
never become a second copy of whatever content triggered a finding). Nothing
ever turned that into score_eval.py's {id, score, category, rationale} rows,
so a privacy-linter user's leak rate over time — is committing secrets
becoming more or less frequent — has never been tracked, gated on, or
checked for regressions the way agent-eval/SKILL.md's own "programmatic
eval" type already generalizes.

Deliberately a bridge, not a reimplementation: reads scan_diff.py's
--log-dir JSON files and WRITES a JSONL file in agent-eval's schema. Does
not import or duplicate score_eval.py's aggregation/regression/gate logic —
that already exists, tested, in a different skill, and skills in this repo
are meant to be self-contained (see this repo's own README); the
integration point between two independent skills is a documented file
format, not a cross-skill import.

    python scan_diff.py --log-dir ~/.privacy-linter-log            # per real scan run
    python scan_log_history.py --log-dir ~/.privacy-linter-log --out trend.jsonl
    python ../../agent-eval/scripts/score_eval.py trend.jsonl --fail-under 0.9
    python ../../agent-eval/scripts/score_eval.py trend.jsonl --baseline last_month.jsonl --fail-on-regression

A scan run doesn't have a fixed checklist the way qa_gate.py does — it
emits zero or more findings of varying leak_class/severity, not a pass/fail
per named check — so unlike qa_gate_history.py's one-row-per-real-check
shape, the "checks" here are DERIVED per run: "clean" (zero findings of any
kind), "no_secrets" (zero secret findings), "no_direct_pii" (zero Direct
PII findings), "no_high_severity" (zero findings at high severity).
category is the derived-check name, so score_eval.py's own per-category
breakdown becomes "which kind of leak recurs most often over time," not
just one overall clean rate. id is "<run_timestamp>__<check>", stable and
unique per pair.

--scan-history runs are intentionally excluded from what scan_diff.py logs
(see write_run_log()'s callers in scan_diff.py) — a --scan-history run
aggregates many historical commits into one call, not comparable to a
single pre-commit hook run, and mixing the two would make "clean rate over
time" meaningless.

stdlib only, matching this repo's other reference tooling."""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_WINDOW_DAYS = 30

# Each value decides whether one Finding dict "counts against" that check.
# "clean" counts every finding; the rest are scoped to one dimension each.
DERIVED_CHECKS = {
    "clean": lambda f: True,
    "no_secrets": lambda f: f["leak_class"] == "secret",
    "no_direct_pii": lambda f: f["leak_class"] == "direct_pii",
    "no_high_severity": lambda f: f["severity"] == "high",
}


def load_run_logs(log_dir: str, cutoff: datetime) -> list[dict]:
    """Reads every *.json file under log_dir written by scan_diff.py's
    --log-dir, keeping only runs at or after `cutoff`. A malformed or
    unreadable log file is skipped with a stderr warning, never a crash —
    same "a bad row doesn't take down the batch" discipline agent-eval's
    own loaders (jsonl_io.load_jsonl) already apply, just for a directory
    of whole files instead of a directory of JSONL lines."""
    runs = []
    for path in sorted(glob.glob(os.path.join(log_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
            run_time = datetime.fromisoformat(record["timestamp"])
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            print(f"Warning: skipping unreadable log file {path}: {e}", file=sys.stderr)
            continue
        if run_time >= cutoff:
            runs.append(record)
    return runs


def flatten_run(record: dict) -> list[dict]:
    """Pure transform: one --log-dir record (a timestamp + a findings
    list) into score_eval.py's {"id", "score", "category", "rationale"}
    schema, one row per derived check. No I/O, fully unit-testable — same
    split qa_gate_history.flatten_qa_checks() already follows between
    pure logic and disk access."""
    findings = record["findings"]
    rows = []
    for check_name, applies in DERIVED_CHECKS.items():
        matching = [f for f in findings if applies(f)]
        passed = len(matching) == 0
        if passed:
            rationale = "no matching findings"
        else:
            labels = "; ".join(f["finding"] for f in matching[:3])
            more = f" (+{len(matching) - 3} more)" if len(matching) > 3 else ""
            rationale = f"{len(matching)} finding(s): {labels}{more}"
        rows.append({
            "id": f"{record['timestamp']}__{check_name}",
            "score": 1.0 if passed else 0.0,
            "category": check_name,
            "rationale": rationale,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log-dir", required=True, help="Directory scan_diff.py's --log-dir wrote run records to.")
    parser.add_argument("--date", default=None, help="Reference 'today' (UTC, YYYY-MM-DD) for the window (default: today).")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help=f"How many days back to look (default: {DEFAULT_WINDOW_DAYS}).")
    parser.add_argument("--out", required=True, help="Path to write the flattened scan history, in score_eval.py's JSONL schema.")
    args = parser.parse_args()

    reference_date = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc) if args.date else datetime.now(timezone.utc)
    cutoff = reference_date - timedelta(days=args.days)

    runs = load_run_logs(args.log_dir, cutoff)

    rows = []
    for record in runs:
        rows.extend(flatten_run(record))

    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    if not rows:
        print(f"No run logs found under {args.log_dir} in the last {args.days} day(s). Wrote an empty {args.out}.")
        return 0

    print(
        f"Wrote {len(rows)} check result(s) across {len(runs)} scan run(s) (last {args.days} day(s)) -> {args.out}\n"
        f"Run skills/agent-eval/scripts/score_eval.py against it for pass rate, per-category breakdown, and regression/CI gating."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
