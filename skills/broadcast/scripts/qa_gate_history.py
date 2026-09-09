#!/usr/bin/env python3
"""Rolling QA-gate history, in agent-eval's own JSONL schema — the bridge
this pipeline's QA gate never had to skills/agent-eval/.

qa_gate.py already produces exactly the shape agent-eval's score_eval.py
was built to aggregate: a named check, a pass/fail, a detail string —
report.json's own "qa_checks" field, per episode. But nothing ever turned
that into score_eval.py's {id, score, category, rationale} rows, so this
pipeline's QA-gate pass rate over time has never been tracked, gated on,
or checked for regressions the way skills/agent-eval/SKILL.md's own
"programmatic eval" type already generalizes — the exact blind spot
source_health_report.py was built to close for source_utilization,
just not for the QA gate itself.

This is deliberately a bridge, not a reimplementation: it reads report.
json files and WRITES a JSONL file in agent-eval's schema. It does not
import or duplicate score_eval.py's aggregation/regression/gate logic —
that already exists, tested, in a different skill, and skills in this
repo are meant to be self-contained (see this repo's own README); the
integration point between two independent skills is a documented file
format, not a cross-skill import. Actually computing pass rate,
regressions, or a CI gate from the file this script writes is
score_eval.py's job:

    python qa_gate_history.py --data-dir ~/.broadcast-data --out qa_results.jsonl
    python skills/agent-eval/scripts/score_eval.py qa_results.jsonl --fail-under 0.9
    python skills/agent-eval/scripts/score_eval.py qa_results.jsonl --baseline last_week.jsonl --fail-on-regression

One row per (episode, check) pair — category is the check name (has_intro,
story_segments_grounded, etc.), so score_eval.py's own per-category
breakdown becomes "which specific QA check fails most often across
episodes," not just an overall pass rate. id is "<run_date>__<check>",
stable and unique per pair.

Same conventions as source_health_report.py: reuses prune_episodes.
list_episode_dirs() rather than re-implementing episode-directory
discovery, standalone and read-only, never called automatically from
orchestrate.py.

stdlib only, matching this repo's other reference tooling."""

import argparse
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prune_episodes  # noqa: E402

DEFAULT_WINDOW_DAYS = 30


def load_qa_checks(data_dir: str, episode_date: str) -> list[dict] | None:
    """Reads episodes/<episode_date>/report.json and returns its
    "qa_checks" field, or None if the report doesn't exist, isn't valid
    JSON, or predates this field — same "a day with no data is not an
    error" convention as source_health_report.load_source_utilization()."""
    report_path = os.path.join(data_dir, "episodes", episode_date, "report.json")
    if not os.path.isfile(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return report.get("qa_checks")


def flatten_qa_checks(episode_date: str, qa_checks: list[dict]) -> list[dict]:
    """Pure transform: qa_gate.py's own {"check", "passed", "detail"}
    shape (see its run_checks()/gate()) into score_eval.py's {"id",
    "score", "category", "rationale"} schema, one row per check. No I/O,
    fully unit-testable — same split every other stage in this pipeline
    already follows between pure logic and disk access."""
    return [
        {
            "id": f"{episode_date}__{c['check']}",
            "score": 1.0 if c["passed"] else 0.0,
            "category": c["check"],
            "rationale": c.get("detail", ""),
        }
        for c in qa_checks
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="Same --data-dir orchestrate.py was run with.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Reference 'today' for the window (default: today).")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help=f"How many days back to look (default: {DEFAULT_WINDOW_DAYS}).")
    parser.add_argument("--out", required=True, help="Path to write the flattened QA-check history, in score_eval.py's JSONL schema.")
    args = parser.parse_args()

    cutoff = date.fromisoformat(args.date) - timedelta(days=args.days)
    episode_dates = [d for d in prune_episodes.list_episode_dirs(args.data_dir) if date.fromisoformat(d) >= cutoff]

    rows = []
    episodes_with_data = 0
    for episode_date in episode_dates:
        qa_checks = load_qa_checks(args.data_dir, episode_date)
        if qa_checks is None:
            continue
        episodes_with_data += 1
        rows.extend(flatten_qa_checks(episode_date, qa_checks))

    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    if not rows:
        print(f"No episodes/<date>/report.json with qa_checks found in the last {args.days} day(s) under {args.data_dir}. Wrote an empty {args.out}.")
        return 0

    print(
        f"Wrote {len(rows)} check result(s) across {episodes_with_data} episode(s) (of {len(episode_dates)} episode "
        f"directories in the last {args.days} day(s)) -> {args.out}\n"
        f"Run skills/agent-eval/scripts/score_eval.py against it for pass rate, per-check breakdown, and regression/CI gating."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
