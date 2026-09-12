#!/usr/bin/env python3
"""Deterministic reference implementation of /checkin's mechanical logic.

Same rationale as skills/wiki-librarian/scripts/check_vault.py and
skills/wiki-governor/scripts/health_score.py: wiki-teacher is a
prompt-driven skill, not code, so this is an oracle to check a real
/checkin run against, not a replacement for it. This closes wiki-teacher's
share of the gap those two scripts already closed for wiki-librarian and
wiki-governor — see knowledge-os/sitrep.md, P1.

Two pieces are implemented, each chosen because it's genuinely mechanical
(a script can get it exactly right) rather than a judgment call (a script
can't):

  - compute_checkin() — /checkin's narrowing algorithm: which flaggable
    project(s) to surface, which need a `priority` elicited first (all of
    them, in one batch — see below), and, when explicitly asked "what
    should I work on" with nothing overdue, which active project to
    suggest anyway. This is the highest-risk unverified logic in the
    original build — a priority sort with a percentage tie-margin and a
    bootstrap precedence rule, executed from prose alone with zero
    automated check before this file existed.

  - portfolio_breadth() — how many projects are active, and how long
    since any project was last marked complete. Facts only, no threshold
    or verdict — /checkin reports the bare count, passively.

Currently scoped to /checkin only. wiki-teacher's original three-command
design also included /teach and /reflect; both were reverted out of this
skill after a review found the entire second half of the skill — /teach
in full, and most of /reflect's behavior — had been built speculatively,
self-critiqued, and self-fixed in a closed loop with zero real usage
behind any of it. See knowledge-os/sitrep.md for the full history. They
come back once /checkin has real mileage and an actual felt need
surfaces, not a self-generated one.

stdlib only, matching this repo's other reference tooling. Imports
load_vault from wiki-librarian's check_vault.py rather than
re-implementing vault loading a second time — same reasoning
health_score.py already documents for its own import of check_vault.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "wiki-librarian", "scripts"))
from check_vault import load_vault  # noqa: E402

DEFAULT_CHECKIN_INTERVAL = 14
TIE_MARGIN = 0.15
OFF_RAMP_STATUSES = ("paused", "complete")
PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}


def _parse_interval(raw) -> int:
    """checkin_interval, defaulting to 14 for absent/non-positive/unparseable
    values — mirrors this repo's existing "tolerate malformed frontmatter"
    posture (see the MCP server's vault-wide-scan fix) rather than raising."""
    if raw is None:
        return DEFAULT_CHECKIN_INTERVAL
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CHECKIN_INTERVAL
    return value if value > 0 else DEFAULT_CHECKIN_INTERVAL


def _active_projects(notes: dict):
    """Every type: project note that isn't paused/complete, with a parsed
    `updated:` date. Base pool for both the flaggable (overdue) view and
    the "nothing's overdue, suggest anyway" view - the two differ only in
    whether the checkin_interval gate is applied."""
    out = []
    for relpath, note in notes.items():
        fm = note["frontmatter"]
        if fm.get("type") != "project":
            continue
        if fm.get("status") in OFF_RAMP_STATUSES:
            continue
        updated = fm.get("updated")
        if not updated:
            continue
        try:
            updated_dt = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append({"path": relpath, "priority": fm.get("priority"), "updated_dt": updated_dt})
    return out


def portfolio_breadth(notes: dict, now: datetime) -> dict:
    """Structural facts about portfolio breadth: how many projects are
    active right now, and how long since any project was last marked
    complete. Deliberately just facts, no threshold and no verdict - "5
    active projects" isn't inherently too many, and inventing a magic
    number here would be exactly the kind of fabricated-not-elicited
    signal this skill avoids everywhere else (see PRIORITY_RANK's own
    "never default it" rule). /checkin reports the count passively, no
    framing at all.

    Assumes `updated:` reflects the date of the most recent edit to a
    note, including a status change to complete - the same assumption
    wiki-operator's /update step ("set updated: to today's date") already
    relies on for every other date-based signal in this codebase.
    """
    active_count = len(_active_projects(notes))

    complete_dates = []
    for note in notes.values():
        fm = note["frontmatter"]
        if fm.get("type") != "project" or fm.get("status") != "complete":
            continue
        updated = fm.get("updated")
        if not updated:
            continue
        try:
            complete_dates.append(datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc))
        except ValueError:
            continue

    if complete_dates:
        days_since_last_completion = (now - max(complete_dates)).days
    else:
        days_since_last_completion = None

    return {
        "active_count": active_count,
        "days_since_last_completion": days_since_last_completion,
        "never_completed": not complete_dates,
    }


def _flaggable_projects(notes: dict, now: datetime):
    """Active projects overdue for check-in, each annotated with its
    overdue ratio. Notes with a missing or unparseable `updated:` are
    skipped, not treated as infinitely overdue - the same defensive
    posture check_vault.py's stale check already takes."""
    out = []
    for p in _active_projects(notes):
        fm = notes[p["path"]]["frontmatter"]
        interval = _parse_interval(fm.get("checkin_interval"))
        days_since = (now - p["updated_dt"]).days
        if days_since < interval:
            continue
        out.append({**p, "ratio": days_since / interval})
    return out


def compute_checkin(notes: dict, now: datetime, tie_margin: float = TIE_MARGIN, explicit_request: bool = False) -> dict:
    """Returns exactly one of:
      {"status": "nothing_flaggable"}
        Silent case - nothing overdue, request wasn't explicit (e.g. the
        session-start auto-check). Caller should say nothing.
      {"status": "needs_bootstrap", "projects": [relpath, ...]}
        One or more flaggable projects have no valid `priority` - ALL of
        them, batched, not just the first. Staleness alone can't rank
        importance, so this missing field IS the check-in; asking about
        them one per day would make the system nearly useless for anyone
        with several concurrent projects, so every project needing the
        signal is surfaced together in one pass.
      {"status": "surfaced", "top": [relpath, ...], "remainder": N}
        Priorities are known; this is the narrowed 1-2 to act on now.
      {"status": "suggested", "project": relpath}
        explicit_request=True, nothing is overdue, but at least one active
        project has a declared priority - answers "what should I work on"
        even when accountability alone has nothing to flag. Never returned
        for a silent/auto check - a proactive suggestion is only useful
        when actually asked for.
      {"status": "no_signal"}
        explicit_request=True, nothing overdue, and no active project has
        a declared priority either - genuinely nothing to recommend from
        declared signal. The honest answer is to say so and offer to set
        priorities, not to guess.

    Bootstrap for an overdue project always wins over narrowing - staleness
    makes the missing signal time-sensitive. A "what should I work on" pull
    with nothing overdue does NOT force a portfolio-wide bootstrap pass -
    there's no urgency driving it, so priority-less active projects are
    just skipped for that suggestion rather than triggering an ask.
    """
    flaggable = _flaggable_projects(notes, now)

    if flaggable:
        missing_priority = sorted(
            (p for p in flaggable if p["priority"] not in PRIORITY_RANK),
            key=lambda p: p["path"],
        )
        if missing_priority:
            return {"status": "needs_bootstrap", "projects": [p["path"] for p in missing_priority]}

        ranked = sorted(flaggable, key=lambda p: (-PRIORITY_RANK[p["priority"]], -p["ratio"]))
        top = [ranked[0]]
        if len(ranked) > 1:
            first, second = ranked[0], ranked[1]
            same_tier = first["priority"] == second["priority"]
            within_margin = (first["ratio"] - second["ratio"]) / first["ratio"] <= tie_margin
            if same_tier and within_margin:
                top.append(second)

        return {
            "status": "surfaced",
            "top": [p["path"] for p in top],
            "remainder": len(flaggable) - len(top),
        }

    if not explicit_request:
        return {"status": "nothing_flaggable"}

    prioritized = [p for p in _active_projects(notes) if p["priority"] in PRIORITY_RANK]
    if not prioritized:
        return {"status": "no_signal"}

    top_rank = max(PRIORITY_RANK[p["priority"]] for p in prioritized)
    top_tier = [p for p in prioritized if PRIORITY_RANK[p["priority"]] == top_rank]
    # Among equally-prioritized active projects, suggest whichever has sat
    # untouched longest - not overdue, but the most natural next pick.
    top_tier.sort(key=lambda p: p["updated_dt"])
    return {"status": "suggested", "project": top_tier[0]["path"]}


def run(vault_root: str, now: datetime = None, explicit_request: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    notes = load_vault(vault_root)
    return {
        "checkin": compute_checkin(notes, now, explicit_request=explicit_request),
        "breadth": portfolio_breadth(notes, now),
        "notes_scanned": len(notes),
    }


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--explicit", action="store_true", help='Simulate an explicit "what should I work on" pull')
    args = parser.parse_args()

    result = run(args.vault, explicit_request=args.explicit)
    if args.json:
        print(json.dumps(result, indent=2))
        return

    checkin = result["checkin"]
    if checkin["status"] == "nothing_flaggable":
        print("Nothing flaggable - no projects overdue for check-in.")
    elif checkin["status"] == "no_signal":
        print("Nothing overdue and no active project has a declared priority.")
    elif checkin["status"] == "needs_bootstrap":
        print(f"Needs priority ({len(checkin['projects'])}): {checkin['projects']}")
    elif checkin["status"] == "suggested":
        print(f"Suggested (nothing overdue): {checkin['project']}")
    else:
        print(f"Surfaced: {checkin['top']}")
        if checkin["remainder"]:
            print(f"{checkin['remainder']} other project(s) also overdue.")

    breadth = result["breadth"]
    since = breadth["days_since_last_completion"]
    since_str = "never" if breadth["never_completed"] else f"{since}d ago"
    print(f"Active: {breadth['active_count']}, last completion: {since_str}")


if __name__ == "__main__":
    main()
