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

TRUST BOUNDARY WARNING: this script executes every test_*.py file it finds under the
target's skill directories via subprocess, with no sandboxing -- no network isolation,
no resource limits, no filesystem restriction (only a per-file wall-clock timeout). Safe
against a target you already trust (this repo's own skills, a codebase you maintain).
NOT safe as a way to evaluate an unvetted third-party or vendor repo -- one of
repo-pincer's own documented use cases -- since that target's test files are exactly the
untrusted code the audit exists to evaluate, and running them here hands them this
session's full privileges.

Usage (from this script's own directory, skills/repo-pincer/scripts/):
    python check_structural_claims.py --claims-file ../../../README.md --skills-dir ../../../skills
    python check_structural_claims.py --claims-file ../../../README.md --skills-dir ../../../skills --json

    # As a CI gate -- exits 1 on Drift or Errored findings instead of just reporting them:
    python check_structural_claims.py --claims-file ../../../README.md --skills-dir ../../../skills --fail-on-drift

Assumes this repo's own tree-block convention: a claim like "392-test suite"
shares a line with the skill directory name it describes (e.g.
"├── broadcast/  # ... 392-test suite"), and the directory name is the
"word/" token at the START of that line, immediately after any tree
box-drawing characters -- true here because every tree entry begins its own
line. A differently formatted README would need this re-tuned; that's an
accepted, named limit, not a silent one -- every repo phrases these claims
differently, so a checker tuned to one repo's convention was never going to
be zero-effort to port anyway. Only tests one narrow class of runner output
("Ran N tests", stdlib unittest's own summary line) -- see SKILL.md's "What's
NOT built here" for what a differently-instrumented test file would do here.
"""

import argparse
import json
import os
import re
import subprocess
import sys

# Matches "392-test suite", "26-test regression suite", "31-test suite", etc.
TEST_COUNT_RE = re.compile(r"(\d+)-test(?:\s+regression)?\s+suite", re.IGNORECASE)

# The directory-name token at the START of a line, per this repo's tree-block
# convention (see module docstring) -- e.g. "│   ├── broadcast/  # ...". Only
# whitespace and box-drawing characters may precede it, so a distractor
# "word/" token appearing later in a line's trailing prose comment (e.g. "the
# scripts/ directory in broadcast/ has a 392-test suite") can never be picked
# up instead of the real one -- confirmed as a real, silent misattribution
# risk with the old whole-line search before this anchor was added.
DIR_TOKEN_RE = re.compile(r"^[\s│├└─]*([A-Za-z0-9_-]+)/(?:\s|$)")

RAN_TESTS_RE = re.compile(r"Ran (\d+) tests?")

# Wall-clock budget per test file. This is the general guard against the
# whole class of bug the recursion incident (see count_actual_tests) turned
# out to be one instance of: the excluded filename defuses that one specific
# trigger, but nothing else stopped some *other* future test file (a stray
# network call, an accidental input(), an unrelated infinite loop) from
# hanging this "run first, fast, mechanical" checker just as badly. A timeout
# turns any such hang into a reported finding instead of a wedged process.
TEST_FILE_TIMEOUT_SECONDS = 30


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


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def count_actual_tests(
    skills_dir: str, skill: str, timeout_seconds: float = TEST_FILE_TIMEOUT_SECONDS
) -> tuple[int, list[str], list[dict]]:
    """Sums "Ran N tests" across every test_*.py under skills_dir/skill/,
    summed rather than taken from a single file -- confirmed necessary in
    practice: broadcast's one claimed number represents the sum of 16
    separate test files, not one. Returns (total, [file paths run],
    [errored-file details]); a skill directory with zero test files returns
    (0, [], []), distinct from a numeric mismatch -- the caller reports this
    case separately since it likely means the association found the wrong
    directory, or the tests were deleted entirely, not just under-counted.

    A test file that crashes (import error, uncaught exception, any exit
    with no "Ran N tests" line) or hangs past TEST_FILE_TIMEOUT_SECONDS
    contributes nothing to the total and is reported in errored-file
    details, never silently folded into total as a plain 0 -- confirmed as
    a real bug in the previous version: a crashed test file and a genuinely
    shrunk test suite produced an identical "actual_count lower than
    claimed" Drift finding, even though they mean completely different
    things and call for completely different follow-up."""
    skill_dir = os.path.join(skills_dir, skill)
    if not os.path.isdir(skill_dir):
        return 0, [], []

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
    errored_files = []
    for path in test_files:
        try:
            result = subprocess.run(
                [sys.executable, path],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            errored_files.append({
                "path": path,
                "reason": "timed_out",
                "detail": f"exceeded {timeout_seconds}s",
            })
            continue

        output = result.stdout + result.stderr
        match = RAN_TESTS_RE.search(output)
        if match:
            total += int(match.group(1))
        else:
            errored_files.append({
                "path": path,
                "reason": "crashed",
                "detail": _last_nonempty_line(output) or f"exit code {result.returncode}, no output",
            })

    return total, test_files, errored_files


def check_claims(claims: list[dict], skills_dir: str) -> dict:
    """Runs every claim against reality. Returns {drift: [...], no_tests_found: [...],
    missing_skill: [...], errored: [...], confirmed_count: N} -- Confirmed claims
    are counted, not listed individually, the same "note them only in aggregate"
    convention repo-pincer's own SKILL.md already uses for its Pass 3 report.

    A claim whose skill has any crashed or timed-out test file is reported under
    `errored`, never under `drift` -- the actual count from a run with a broken
    or hung test file is not trustworthy enough to call it a numeric mismatch
    against reality, and collapsing the two looks identical to a user unless
    they're kept apart."""
    drift = []
    no_tests_found = []
    missing_skill = []
    errored = []
    confirmed_count = 0

    for claim in claims:
        skill = claim["skill"]
        skill_dir = os.path.join(skills_dir, skill)
        if not os.path.isdir(skill_dir):
            missing_skill.append(claim)
            continue

        actual_count, test_files, errored_files = count_actual_tests(skills_dir, skill)
        if not test_files:
            no_tests_found.append(claim)
            continue

        if errored_files:
            errored.append({**claim, "errored_files": errored_files})
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
        "errored": errored,
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

    if report["errored"]:
        print(f"  Errored (test file crashed or timed out -- count not trustworthy): "
              f"{len(report['errored'])}")
        for f in report["errored"]:
            print(f"    {f['skill']}: claimed {f['claimed_count']}")
            for ef in f["errored_files"]:
                print(f"      {ef['path']}: {ef['reason']} ({ef['detail']})")

    if (not report["drift"] and not report["no_tests_found"]
            and not report["missing_skill"] and not report["errored"]):
        print("  No discrepancies found.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claims-file", required=True, metavar="FILE",
                         help="Markdown file to scan for test-count claims (e.g. README.md).")
    parser.add_argument("--skills-dir", required=True, metavar="DIR",
                         help="Root directory containing one subdirectory per skill.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON instead of text.")
    parser.add_argument("--fail-on-drift", action="store_true",
                         help="Exit 1 if any claim is Drift or Errored (a numeric mismatch, or a "
                              "test file that crashed/timed out and made the count untrustworthy). "
                              "Missing-skill and no-tests-found findings do not gate -- those more "
                              "often mean the line's association is ambiguous than that something "
                              "regressed. Without this flag, findings are only reported, matching "
                              "this script's previous behavior of always exiting 0.")
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

    if args.fail_on_drift and (report["drift"] or report["errored"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
