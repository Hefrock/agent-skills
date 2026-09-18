#!/usr/bin/env python3
"""
check_structural_claims.py — a mechanical Pass-2 accelerant for repo-pincer.

Checks exactly one claim type: "N-test suite" / "N-test regression suite" claims
in a markdown file (this repo's README.md, by convention), verified by actually
running the corresponding tests -- not estimated, not inferred from a prior claim.

Why this exists: running repo-pincer's full three-pass methodology (read every
entry point, trace the call graph, build a complete as-built model) against an
entire repo in one shot is exactly what SKILL.md's own guidance says not to do
("a full pass on a large repo in one shot is rarely what's useful"). But a repo-
wide pass sometimes IS what's asked for, and when it is, exact numeric claims are
the highest-value, lowest-cost thing to check first: they're mechanically
verifiable with one command each, and they drift silently in practice -- nobody
re-counts "26 tests" by hand every time a test gets added. This script automates
exactly that slice, so conversational effort goes to the claims that actually
need judgment (Silent findings, behavioral Drift) instead of arithmetic.

Deliberately narrow -- see SKILL.md's "What's NOT built here" for what this does
NOT attempt (path-existence claims, claims in a differently-formatted doc, any
form of semantic/behavioral checking).

Usage:
    python check_structural_claims.py --claims-file ../../README.md --skills-dir ../../skills
    python check_structural_claims.py --claims-file ../../README.md --skills-dir ../../skills --json

Assumes this repo's own tree-block convention: a claim like "392-test suite"
shares a line with the skill directory name it describes (e.g.
"├── broadcast/  # ... 392-test suite"), and the directory name is the first
"word/" token on that line -- true here because the tree entry itself always
comes before any path mentioned in the trailing comment. A differently
formatted README would need this re-tuned; that's an accepted, named limit,
not a silent one -- every repo phrases these claims differently, so a checker
tuned to one repo's convention was never going to be zero-effort to port anyway.
"""

import argparse
import json
import os
import re
import subprocess
import sys

# Matches "392-test suite", "26-test regression suite", "31-test suite", etc.
TEST_COUNT_RE = re.compile(r"(\d+)-test(?:\s+regression)?\s+suite", re.IGNORECASE)

# The first "word/" token on a line -- assumed to be the skill directory name,
# per this repo's tree-block convention (see module docstring).
DIR_TOKEN_RE = re.compile(r"([A-Za-z0-9_-]+)/(?:\s|$)")

RAN_TESTS_RE = re.compile(r"Ran (\d+) tests?")


def extract_test_count_claims(markdown_text: str) -> list[dict]:
    """One entry per line containing both a test-count claim and a leading
    directory-name token: {skill, claimed_count, line}. A line with a count
    but no recognizable directory token is skipped, not guessed at -- a
    claim this script can't confidently associate with a skill isn't
    reported as a finding either way, silently, rather than risk a wrong
    association looking like a false positive."""
    claims = []
    for line in markdown_text.splitlines():
        count_match = TEST_COUNT_RE.search(line)
        if not count_match:
            continue
        dir_match = DIR_TOKEN_RE.search(line)
        if not dir_match:
            continue
        claims.append({
            "skill": dir_match.group(1),
            "claimed_count": int(count_match.group(1)),
            "line": line.strip(),
        })
    return claims


def count_actual_tests(skills_dir: str, skill: str) -> tuple[int, list[str]]:
    """Sums "Ran N tests" across every test_*.py under skills_dir/skill/,
    summed rather than taken from a single file -- confirmed necessary in
    practice: broadcast's one claimed number represents the sum of 16
    separate test files, not one. Returns (total, [file paths run]); a
    skill directory with zero test files returns (0, []), distinct from a
    numeric mismatch -- the caller reports this case separately since it
    likely means the association found the wrong directory, or the tests
    were deleted entirely, not just under-counted."""
    skill_dir = os.path.join(skills_dir, skill)
    if not os.path.isdir(skill_dir):
        return 0, []

    test_files = []
    for dirpath, _dirnames, filenames in os.walk(skill_dir):
        for name in filenames:
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            if name == "test_check_structural_claims.py":
                # Never shell out to this script's own test file: it contains a
                # live self-check that re-invokes check_structural_claims.py
                # against the real README -- running it here would recurse
                # (verify repo-pincer's claim -> run its test file -> which runs
                # this script again -> which tries to verify the same claim ->
                # ...), caught empirically as an actual runaway subprocess tree
                # during this script's own verification, not a hypothetical.
                # A claim about repo-pincer's own test count is therefore
                # structurally uncheckable by this tool -- see SKILL.md.
                continue
            test_files.append(os.path.join(dirpath, name))
    test_files.sort()

    total = 0
    for path in test_files:
        result = subprocess.run([sys.executable, path], capture_output=True, text=True)
        match = RAN_TESTS_RE.search(result.stdout + result.stderr)
        if match:
            total += int(match.group(1))
    return total, test_files


def check_claims(claims: list[dict], skills_dir: str) -> dict:
    """Runs every claim against reality. Returns {drift: [...], no_tests_found: [...],
    missing_skill: [...], confirmed_count: N} -- Confirmed claims are counted, not
    listed individually, the same "note them only in aggregate" convention
    repo-pincer's own SKILL.md already uses for its Pass 3 report."""
    drift = []
    no_tests_found = []
    missing_skill = []
    confirmed_count = 0

    for claim in claims:
        skill = claim["skill"]
        skill_dir = os.path.join(skills_dir, skill)
        if not os.path.isdir(skill_dir):
            missing_skill.append(claim)
            continue

        actual_count, test_files = count_actual_tests(skills_dir, skill)
        if not test_files:
            no_tests_found.append(claim)
            continue

        if actual_count == claim["claimed_count"]:
            confirmed_count += 1
        else:
            drift.append({
                **claim,
                "actual_count": actual_count,
                "delta": actual_count - claim["claimed_count"],
                "test_file_count": len(test_files),
            })

    return {
        "drift": drift,
        "no_tests_found": no_tests_found,
        "missing_skill": missing_skill,
        "confirmed_count": confirmed_count,
    }


def print_report(report: dict, total_claims: int, json_out: bool) -> None:
    if json_out:
        print(json.dumps(report, indent=2))
        return

    print(f"check_structural_claims: {total_claims} test-count claim(s) checked.")
    print(f"  Confirmed: {report['confirmed_count']}")

    if report["drift"]:
        print(f"  Drift: {len(report['drift'])}")
        for f in report["drift"]:
            sign = "+" if f["delta"] > 0 else ""
            print(f"    {f['skill']}: claimed {f['claimed_count']}, actual {f['actual_count']} "
                  f"({sign}{f['delta']}, across {f['test_file_count']} file(s))")

    if report["no_tests_found"]:
        print(f"  No test files found (association may be wrong, or tests were removed): "
              f"{len(report['no_tests_found'])}")
        for f in report["no_tests_found"]:
            print(f"    {f['skill']}: claimed {f['claimed_count']}, found 0 test_*.py files")

    if report["missing_skill"]:
        print(f"  Claimed skill directory does not exist: {len(report['missing_skill'])}")
        for f in report["missing_skill"]:
            print(f"    {f['skill']}: {f['line']}")

    if not report["drift"] and not report["no_tests_found"] and not report["missing_skill"]:
        print("  No discrepancies found.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claims-file", required=True, metavar="FILE",
                         help="Markdown file to scan for test-count claims (e.g. README.md).")
    parser.add_argument("--skills-dir", required=True, metavar="DIR",
                         help="Root directory containing one subdirectory per skill.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON instead of text.")
    args = parser.parse_args()

    try:
        with open(args.claims_file, encoding="utf-8") as f:
            markdown_text = f.read()
    except OSError as e:
        print(f"check_structural_claims: could not read {args.claims_file}: {e}", file=sys.stderr)
        return 2

    if not os.path.isdir(args.skills_dir):
        print(f"check_structural_claims: --skills-dir {args.skills_dir} is not a directory.", file=sys.stderr)
        return 2

    claims = extract_test_count_claims(markdown_text)
    report = check_claims(claims, args.skills_dir)
    print_report(report, len(claims), args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
