#!/usr/bin/env python3
"""Retention for orchestrate.py's per-episode output — episodes/<date>/
{report.json,script.json,episode.wav} under --data-dir.

Deliberately separate from dedup_store.py's own rolling-window retention
(prune_old_entries(), wired automatically into every rank.py run, no
confirmation needed): that store holds pure classification cache data
with zero value once its window has passed. This directory holds real
episode output — scripts, synthesized audio — a human may not have run
distribute.py against yet, may still want for their own archive, or may
be actively debugging. Automatic, silent deletion of that on every
orchestrate.py run felt like the wrong default for this project's own
conservative-by-default conventions (see wiki-operator's /clean: propose
a plan, then wait for confirmation before changing anything) — so this
is a separate, explicitly-invoked script, dry-run by default, never
called automatically from orchestrate.py or anywhere else.

evidence_store/ (evidence-pinning-mcp's own durable state, also under
--data-dir) is deliberately NOT covered here or by anything else in this
pipeline: it's designed as an append-only provenance log specifically so
a claim's history stays queryable indefinitely (see mcp/evidence-pinning/
README.md's own description) — pruning it would defeat its purpose, not
just free disk space. Retention is a per-store decision, not a single
policy across --data-dir.

stdlib only, matching this repo's other reference tooling."""

import argparse
import os
import shutil
import sys
from datetime import date, timedelta

DEFAULT_RETENTION_DAYS = 90


def list_episode_dirs(data_dir: str) -> list[str]:
    """Every subdirectory of <data_dir>/episodes/ whose name parses as an
    ISO date (orchestrate.py's own naming convention) — anything else (a
    stray file, a malformed or hand-created directory name) is silently
    skipped, not an error; this is a listing helper, not a validator of
    --data-dir's overall structure. Returns [] if episodes/ doesn't exist
    yet (a --data-dir with no runs in it is not an error here either)."""
    episodes_dir = os.path.join(data_dir, "episodes")
    if not os.path.isdir(episodes_dir):
        return []
    result = []
    for name in os.listdir(episodes_dir):
        full = os.path.join(episodes_dir, name)
        if not os.path.isdir(full):
            continue
        try:
            date.fromisoformat(name)
        except ValueError:
            continue
        result.append(name)
    return sorted(result)


def episodes_older_than(data_dir: str, current_date: str, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[str]:
    """The subset of list_episode_dirs() whose date falls outside the
    retention window, oldest first. Pure aside from the directory listing
    itself — no deletion happens here, same "decide, then separately act"
    split as prune_episodes() below."""
    cutoff = date.fromisoformat(current_date) - timedelta(days=retention_days)
    return [d for d in list_episode_dirs(data_dir) if date.fromisoformat(d) < cutoff]


def prune_episodes(data_dir: str, current_date: str, retention_days: int = DEFAULT_RETENTION_DAYS, apply: bool = False) -> list[str]:
    """Returns the list of stale episode date-directories — the ones that
    are (apply=True) or WOULD BE (apply=False, the default) removed.

    apply=False is a dry run: nothing on disk changes, this only reports
    what a real run would do — the default specifically so this can never
    be invoked destructively by accident. Pass apply=True to actually
    shutil.rmtree() each stale directory."""
    stale = episodes_older_than(data_dir, current_date, retention_days)
    if apply:
        for d in stale:
            shutil.rmtree(os.path.join(data_dir, "episodes", d))
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="Same --data-dir orchestrate.py was run with.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Reference 'today' for the retention window (default: today).")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete stale episode directories. Without this flag, prints what WOULD be deleted and changes nothing on disk (the default — always run without --apply first).",
    )
    args = parser.parse_args()

    stale = prune_episodes(args.data_dir, args.date, args.retention_days, apply=args.apply)
    if not stale:
        print(f"No episode directories older than {args.retention_days} days.")
        return 0

    verb = "Deleted" if args.apply else "Would delete (dry run — pass --apply to actually remove)"
    for d in stale:
        print(f"{verb}: episodes/{d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
