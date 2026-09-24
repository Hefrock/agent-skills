#!/usr/bin/env python3
"""Retention for cron_check.sh's own JSON-per-line audit log.

Unlike broadcast's prune_episodes.py (real episode output a human may still
want, so it's a separate manual dry-run-by-default script), this log holds
zero PII/secret text -- only finding labels and file:line locations, per
cron_check.sh's own header comment -- so it's pure disposable operational
telemetry, the same "safe to auto-prune, no confirmation needed" bucket as
broadcast's dedup_store.py rolling-window cache. cron_check.sh calls this
automatically after every append; there is no --apply gate here.

Conservative on malformed input: a line that isn't valid JSON, or has no
"timestamp" field, is kept rather than dropped -- a parse bug should never
silently destroy log data.

stdlib only, matching this repo's other reference tooling."""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

DEFAULT_RETENTION_DAYS = 90


def _line_date(line: str) -> str | None:
    """The line's timestamp truncated to a date, or None if the line can't
    be parsed or has no "timestamp" field -- callers treat None as "keep"."""
    try:
        timestamp = json.loads(line)["timestamp"]
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").date().isoformat()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def prune_log_lines(lines: list[str], current_date: str, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[str]:
    """The subset of lines to keep -- everything within the retention window,
    plus anything whose timestamp couldn't be determined (kept, not dropped).
    Pure aside from no I/O -- same "decide, then separately act" split as
    prune_episodes.episodes_older_than()."""
    cutoff = date.fromisoformat(current_date) - timedelta(days=retention_days)
    kept = []
    for line in lines:
        line_date = _line_date(line)
        if line_date is None or date.fromisoformat(line_date) >= cutoff:
            kept.append(line)
    return kept


def prune_log_file(log_file: str, current_date: str, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Rewrites log_file in place, keeping only lines within the retention
    window. Returns the number of lines dropped. A missing log_file is not
    an error -- nothing to prune yet. Writes to a temp file in the same
    directory and os.replace()s it, so a run that's interrupted mid-write
    can never leave a truncated log behind."""
    if not os.path.exists(log_file):
        return 0

    with open(log_file) as f:
        lines = f.readlines()

    kept = prune_log_lines(lines, current_date, retention_days)
    dropped = len(lines) - len(kept)
    if dropped == 0:
        return 0

    tmp_path = f"{log_file}.tmp"
    with open(tmp_path, "w") as f:
        f.writelines(kept)
    os.replace(tmp_path, log_file)
    return dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--date", default=date.today().isoformat(), help="Reference 'today' for the retention window (default: today).")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    args = parser.parse_args()

    dropped = prune_log_file(args.log_file, args.date, args.retention_days)
    if dropped:
        print(f"Pruned {dropped} log line(s) older than {args.retention_days} days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
