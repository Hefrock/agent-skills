#!/usr/bin/env python3
"""Deterministic reference implementation of wiki-teacher's mechanical logic.

Same rationale as skills/wiki-librarian/scripts/check_vault.py and
skills/wiki-governor/scripts/health_score.py: wiki-teacher is a
prompt-driven skill, not code, so this is an oracle to check a real
/checkin or /reflect run against, not a replacement for either. This
closes wiki-teacher's share of the gap those two scripts already closed
for wiki-librarian and wiki-governor — see knowledge-os/sitrep.md, P1.

Two pieces are implemented, both chosen because they're genuinely
mechanical (a script can get them exactly right) rather than judgment
calls (a script can't):

  - compute_checkin() — /checkin's narrowing algorithm: which flaggable
    project(s) to surface, or whether a missing `priority` needs to be
    elicited first. This is the highest-risk unverified logic in the
    original build — a priority sort with a percentage tie-margin and a
    bootstrap precedence rule, executed from prose alone with zero
    automated check before this file existed.

  - spans_multiple_compartments() — the mechanical half of /reflect's
    privacy safeguard: whether a set of projects declares more than one
    distinct `compartment` value (see wiki-operator/SKILL.md's note
    schema). Deliberately conservative: an undeclared or invalid
    compartment is never assumed safe, it's treated the same as "might
    cross a boundary." What to DO with that signal — whether a specific
    observation is actually worth surfacing, and how to phrase it — stays
    a judgment call for the model; this only answers the yes/no structural
    question a script can answer reliably.

Deliberately NOT implemented — genuine semantic judgment a script can't
make: identifying an actual throughline or specialization across a
portfolio (the substance of /reflect), and generating teaching questions
(the substance of /teach). Neither is mechanical in the way the two
functions above are.

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
VALID_COMPARTMENTS = {"public-professional", "personal", "sensitive-research"}


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


def _flaggable_projects(notes: dict, now: datetime):
    """Active (not paused/complete) type: project notes overdue for
    check-in, each annotated with its overdue ratio. Notes with a missing
    or unparseable `updated:` are skipped, not treated as infinitely
    overdue - the same defensive posture check_vault.py's stale check
    already takes."""
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
        interval = _parse_interval(fm.get("checkin_interval"))
        days_since = (now - updated_dt).days
        if days_since < interval:
            continue
        out.append({
            "path": relpath,
            "priority": fm.get("priority"),
            "ratio": days_since / interval,
        })
    return out


def compute_checkin(notes: dict, now: datetime, tie_margin: float = TIE_MARGIN) -> dict:
    """Returns exactly one of:
      {"status": "nothing_flaggable"}
      {"status": "needs_bootstrap", "project": relpath}
      {"status": "surfaced", "top": [relpath, ...], "remainder": N}

    A flaggable project with no valid `priority` always wins over
    narrowing - that missing field IS the check-in (wiki-teacher/SKILL.md
    /checkin step 3). Tie-broken by path when more than one qualifies,
    an arbitrary rule used only to pick which to ask about first.
    """
    flaggable = _flaggable_projects(notes, now)
    if not flaggable:
        return {"status": "nothing_flaggable"}

    missing_priority = sorted(
        (p for p in flaggable if p["priority"] not in PRIORITY_RANK),
        key=lambda p: p["path"],
    )
    if missing_priority:
        return {"status": "needs_bootstrap", "project": missing_priority[0]["path"]}

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


def spans_multiple_compartments(project_paths, notes: dict) -> bool:
    """True if the given projects declare 2+ distinct `compartment` values,
    OR if any project's compartment is undeclared/invalid - unknown is
    never assumed safe. False only when every project declares the exact
    same single valid compartment."""
    declared = []
    for path in project_paths:
        compartment = notes.get(path, {}).get("frontmatter", {}).get("compartment")
        if compartment not in VALID_COMPARTMENTS:
            return True
        declared.append(compartment)
    return len(set(declared)) > 1


def run(vault_root: str, now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    notes = load_vault(vault_root)
    return {
        "checkin": compute_checkin(notes, now),
        "notes_scanned": len(notes),
    }


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(args.vault)
    if args.json:
        print(json.dumps(result, indent=2))
        return

    checkin = result["checkin"]
    if checkin["status"] == "nothing_flaggable":
        print("Nothing flaggable - no projects overdue for check-in.")
    elif checkin["status"] == "needs_bootstrap":
        print(f"Needs priority: {checkin['project']}")
    else:
        print(f"Surfaced: {checkin['top']}")
        if checkin["remainder"]:
            print(f"{checkin['remainder']} other project(s) also overdue.")


if __name__ == "__main__":
    main()
