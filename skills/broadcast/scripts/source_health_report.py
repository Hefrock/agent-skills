#!/usr/bin/env python3
"""Multi-day source-utilization report — the rolling view rank.py's
summarize_source_utilization() was deliberately scoped NOT to build
(see its docstring): a single run's numbers are noisy on their own (a
source with zero candidates today might just mean nothing newsworthy
happened, not that it's being starved). This is that deferred next
step: reads every episodes/<date>/report.json under --data-dir within
a window, sums each source's "source_utilization" field across days,
and reports which sources are actually winning selection slots over
time versus which are structurally never winning one.

Standalone and read-only, same convention as live_smoke_test.py and
prune_episodes.py — never called automatically from orchestrate.py.
Reuses prune_episodes.list_episode_dirs() rather than re-implementing
episode-directory discovery, this project's own precedent (see
evidence_pinning_client.py's docstring) for not duplicating logic that
then drifts apart.

A source can be legitimately quiet for real reasons this report can't
distinguish from starvation on its own (a slow news week, a feed that
posts less often than daily) — this is a diagnostic to prompt a human
question, not an automated verdict. Pure observability, like the
per-run field it aggregates: nothing here fails a run or blocks
anything, because nothing here runs as part of a real episode at all.

stdlib only, matching this repo's other reference tooling."""

import argparse
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prune_episodes  # noqa: E402
import source_registry  # noqa: E402

DEFAULT_WINDOW_DAYS = 30


def load_source_utilization(data_dir: str, episode_date: str) -> dict | None:
    """Reads episodes/<episode_date>/report.json and returns its
    "source_utilization" field, or None if the report doesn't exist,
    isn't valid JSON, or predates this field (an older episode run
    before summarize_source_utilization() existed) — any of these is a
    day this report simply has no data for, not an error to surface."""
    report_path = os.path.join(data_dir, "episodes", episode_date, "report.json")
    if not os.path.isfile(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return report.get("source_utilization")


def aggregate_source_utilization(per_day: list[dict]) -> dict:
    """per_day is a list of already-loaded source_utilization dicts (one
    per day considered, already filtered to non-None by the caller — see
    load_source_utilization). Sums every field across days per source,
    plus "days_with_data" (how many of these days this source had at
    least one candidate at all) and an overall selection_rate.

    A source absent from a given day's dict (rank.summarize_source_
    utilization() omits sources with zero candidates that day, on
    purpose — see its own docstring) contributes nothing to that
    source's totals for that day, same as it contributed nothing to
    that day's episode. len(per_day) itself is the caller's "total days
    considered" — not tracked per-source here, since it's the same
    denominator for every source in a given call.

    Returns {source_key: {"days_with_data", "candidates",
    "selected_top_three", "selected_quick_hits", "selected_total",
    "not_selected", "dropped_duplicates", "selection_rate"}, ...}."""
    totals: dict = {}

    def bucket(source_key: str) -> dict:
        return totals.setdefault(source_key, {
            "days_with_data": 0, "candidates": 0, "selected_top_three": 0,
            "selected_quick_hits": 0, "selected_total": 0, "not_selected": 0, "dropped_duplicates": 0,
        })

    for day_util in per_day:
        for source_key, stats in day_util.items():
            b = bucket(source_key)
            b["days_with_data"] += 1
            b["candidates"] += stats.get("candidates", 0)
            b["selected_top_three"] += stats.get("selected_top_three", 0)
            b["selected_quick_hits"] += stats.get("selected_quick_hits", 0)
            b["selected_total"] += stats.get("selected_total", 0)
            b["not_selected"] += stats.get("not_selected", 0)
            b["dropped_duplicates"] += stats.get("dropped_duplicates", 0)

    for stats in totals.values():
        stats["selection_rate"] = (stats["selected_total"] / stats["candidates"]) if stats["candidates"] else None

    return totals


def find_never_appearing_sources(registry: dict, aggregated: dict) -> list[str]:
    """Registered source keys with zero entries anywhere in aggregated —
    a source that never had even one candidate across the whole window,
    the single clearest "worth asking a human about" signal this report
    can produce. Sorted for stable, readable output."""
    registered = {s["key"] for s in registry["sources"]}
    return sorted(registered - set(aggregated.keys()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="Same --data-dir orchestrate.py was run with.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Reference 'today' for the window (default: today).")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help=f"How many days back to look (default: {DEFAULT_WINDOW_DAYS}).")
    args = parser.parse_args()

    registry_path = os.path.join(HERE, "..", "config", "sources.json")
    registry = source_registry.load_registry(registry_path)

    cutoff = date.fromisoformat(args.date) - timedelta(days=args.days)
    episode_dates = [d for d in prune_episodes.list_episode_dirs(args.data_dir) if date.fromisoformat(d) >= cutoff]

    per_day = []
    for episode_date in episode_dates:
        util = load_source_utilization(args.data_dir, episode_date)
        if util is not None:
            per_day.append(util)

    if not per_day:
        print(f"No episodes/<date>/report.json with source_utilization data found in the last {args.days} day(s) under {args.data_dir}.")
        return 0

    aggregated = aggregate_source_utilization(per_day)

    print(f"Source utilization over {len(per_day)} day(s) with data (of {len(episode_dates)} episode directories in the last {args.days} day(s)):\n")
    header = f"{'source':<20} {'days':>5} {'candidates':>11} {'top3':>5} {'quick':>6} {'not_sel':>8} {'dup':>4} {'rate':>6}"
    print(header)
    print("-" * len(header))
    # Most-starved (lowest selection_rate) first — a source with plenty
    # of candidates but a low rate is the one worth asking a human about;
    # None (zero candidates the whole window) sorts last, not first,
    # since that's covered separately below, not ambiguous with "loses
    # a lot."
    for source_key, stats in sorted(aggregated.items(), key=lambda kv: (kv[1]["selection_rate"] is None, kv[1]["selection_rate"] or 0)):
        rate_str = f"{stats['selection_rate']:.2f}" if stats["selection_rate"] is not None else "n/a"
        print(f"{source_key:<20} {stats['days_with_data']:>5} {stats['candidates']:>11} {stats['selected_top_three']:>5} {stats['selected_quick_hits']:>6} {stats['not_selected']:>8} {stats['dropped_duplicates']:>4} {rate_str:>6}")

    never_appeared = find_never_appearing_sources(registry, aggregated)
    if never_appeared:
        print(f"\nRegistered but ZERO candidates in this entire window: {', '.join(never_appeared)}")
        print("Worth asking a human about — could be a quiet source, or could be a real problem (check ingest_failed in its recent report.json files first).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
