#!/usr/bin/env python3
"""
track_findings.py -- cross-run fingerprint tracking for repo-pincer's Pass 3 findings.

Why this exists: check_structural_claims.py's mechanical check has no need for
cross-run memory -- it's cheap enough to just re-run every time. Pass 3's actual
findings (Drift, Aspirational, Silent, Confirmed) are the opposite: they cost real
conversational effort to derive (reading entry points, tracing call graphs, judging
severity), and today repo-pincer has zero memory of them -- every run starts cold,
even against a target it already audited last week. A finding whose cited source
hasn't changed since it was recorded doesn't need to be re-derived; only a changed
source needs fresh judgment. This script makes that distinction mechanical: record
a finding once with the file(s) its conclusion depends on, and on a later run it
reports which findings are still backed by unchanged source (carry forward, no new
judgment needed) versus which cite source that has since changed (needs
revalidation) versus which cite source that no longer exists at all.

Deliberately narrow, matching check_structural_claims.py's own scoping: hashes are
whole-file, not line-range -- see SKILL.md's "What's NOT built here" for what that
means in practice. This script never assigns or changes a verdict on its own; it
only tracks whether a previously-recorded verdict's evidence is still intact.

Usage (from this script's own directory, skills/repo-pincer/scripts/):
    # After Pass 3 derives a finding, record it:
    python track_findings.py --ledger-file ../../../findings-ledger.json upsert \
        --fingerprint wiki-warehouse:aspirational:bin-intake-external-repo \
        --verdict aspirational \
        --title "bin/intake.py and bin/audit.py live in a separate repo" \
        --source-ref ../../wiki-warehouse/SKILL.md \
        --notes "Legitimate architecture, not a gap -- the tool can't live in this repo."

    # At the start of a later run, before spending any conversational effort:
    python track_findings.py --ledger-file ../../../findings-ledger.json check
    python track_findings.py --ledger-file ../../../findings-ledger.json check --json

All paths are stored in the ledger relative to --repo-root (default: the current
directory), so the ledger is portable across clones as long as --repo-root is set
correctly on each invocation.
"""

import argparse
import hashlib
import json
import os
import sys

VALID_FINGERPRINT_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/@-"
)
VALID_VERDICTS = ("confirmed", "drift", "aspirational", "silent")


def is_valid_fingerprint(fingerprint: str) -> bool:
    """A stable, human-readable identifier -- not a hash. Must start with an
    alphanumeric and use only a conservative character set, so it's safe to use as
    a lookup key and safe to print without escaping."""
    if not fingerprint or not fingerprint[0].isalnum():
        return False
    return all(c in VALID_FINGERPRINT_CHARS for c in fingerprint)


def compute_file_hash(repo_root: str, rel_path: str) -> str | None:
    """sha256 of the file's current bytes, or None if the file doesn't exist.
    Whole-file, not line-range -- a real but disclosed limit (see module docstring):
    an unrelated edit elsewhere in a cited file still reports "changed," which is
    the safe direction to be wrong in (an unnecessary re-check, never a silently
    stale carry-forward)."""
    abs_path = os.path.join(repo_root, rel_path)
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_ledger(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_ledger(path: str, records: list[dict]) -> None:
    records = sorted(records, key=lambda r: r["fingerprint"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
        f.write("\n")


def upsert_finding(
    records: list[dict],
    fingerprint: str,
    verdict: str,
    title: str,
    source_refs: list[str],
    repo_root: str,
    notes: str = "",
    commit: str | None = None,
) -> list[dict]:
    """Adds a new finding record, or replaces the existing one with the same
    fingerprint -- never appends a duplicate. Recomputes every source_ref's hash
    fresh at upsert time, so re-recording a finding after fixing it also refreshes
    its evidence baseline."""
    if not is_valid_fingerprint(fingerprint):
        raise ValueError(
            f"invalid fingerprint {fingerprint!r}: must start with a letter/digit and "
            f"use only letters, digits, and . _ : / @ -"
        )
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"invalid verdict {verdict!r}: must be one of {VALID_VERDICTS}")
    if not source_refs:
        raise ValueError("at least one --source-ref is required")

    hashed_refs = []
    for rel_path in source_refs:
        h = compute_file_hash(repo_root, rel_path)
        if h is None:
            raise ValueError(f"source-ref does not exist: {rel_path}")
        hashed_refs.append({"file": rel_path, "hash": h})

    record = {
        "fingerprint": fingerprint,
        "verdict": verdict,
        "title": title,
        "source_refs": hashed_refs,
        "last_verified_commit": commit,
        "notes": notes,
    }
    records = [r for r in records if r["fingerprint"] != fingerprint]
    records.append(record)
    return records


def check_ledger(records: list[dict], repo_root: str) -> dict:
    """Buckets every ledger record into unchanged / changed / missing_source by
    recomputing each source_ref's current hash. A record with multiple source_refs
    where at least one is missing and another merely changed is reported under
    missing_source, not changed -- a deleted file is the more severe condition and
    should not be masked by a milder sibling reason."""
    unchanged, changed, missing = [], [], []
    for r in records:
        reasons = []
        any_missing = False
        any_changed = False
        for ref in r["source_refs"]:
            current = compute_file_hash(repo_root, ref["file"])
            if current is None:
                any_missing = True
                reasons.append(f"{ref['file']}: no longer exists")
            elif current != ref["hash"]:
                any_changed = True
                reasons.append(f"{ref['file']}: content changed since last verified")
        entry = {**r, "check_reasons": reasons}
        if any_missing:
            missing.append(entry)
        elif any_changed:
            changed.append(entry)
        else:
            unchanged.append(entry)
    return {"unchanged": unchanged, "changed": changed, "missing_source": missing}


def print_check_report(report: dict, json_out: bool) -> None:
    if json_out:
        print(json.dumps(report, indent=2))
        return

    total = len(report["unchanged"]) + len(report["changed"]) + len(report["missing_source"])
    print(f"track_findings: {total} ledger record(s) checked.")
    print(f"  Unchanged (carry forward, no new judgment needed): {len(report['unchanged'])}")
    for r in report["unchanged"]:
        print(f"    [{r['verdict']}] {r['fingerprint']}: {r['title']}")

    if report["changed"]:
        print(f"  Changed (needs revalidation): {len(report['changed'])}")
        for r in report["changed"]:
            print(f"    [{r['verdict']}] {r['fingerprint']}: {r['title']}")
            for reason in r["check_reasons"]:
                print(f"      {reason}")

    if report["missing_source"]:
        print(f"  Missing source (needs revalidation, cited file is gone): {len(report['missing_source'])}")
        for r in report["missing_source"]:
            print(f"    [{r['verdict']}] {r['fingerprint']}: {r['title']}")
            for reason in r["check_reasons"]:
                print(f"      {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger-file", required=True, metavar="FILE",
                         help="JSON findings ledger to read/write.")
    parser.add_argument("--repo-root", default=".", metavar="DIR",
                         help="Root that --source-ref paths are relative to (default: cwd).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Report unchanged/changed/missing-source records.")
    check_parser.add_argument("--json", action="store_true", help="Emit the report as JSON instead of text.")

    upsert_parser = subparsers.add_parser("upsert", help="Add or update one finding record.")
    upsert_parser.add_argument("--fingerprint", required=True)
    upsert_parser.add_argument("--verdict", required=True, choices=VALID_VERDICTS)
    upsert_parser.add_argument("--title", required=True)
    upsert_parser.add_argument("--source-ref", dest="source_refs", action="append", required=True,
                                metavar="PATH", help="Repeatable. At least one required.")
    upsert_parser.add_argument("--notes", default="")
    upsert_parser.add_argument("--commit", default=None, metavar="SHA")

    args = parser.parse_args()
    records = load_ledger(args.ledger_file)

    if args.command == "check":
        report = check_ledger(records, args.repo_root)
        print_check_report(report, args.json)
        return 0

    if args.command == "upsert":
        try:
            records = upsert_finding(
                records, args.fingerprint, args.verdict, args.title, args.source_refs,
                args.repo_root, notes=args.notes, commit=args.commit,
            )
        except ValueError as e:
            print(f"track_findings: {e}", file=sys.stderr)
            return 2
        save_ledger(args.ledger_file, records)
        print(f"track_findings: recorded {args.fingerprint} ({args.verdict}), "
              f"{len(args.source_refs)} source-ref(s).")
        return 0

    return 2  # unreachable: argparse enforces a valid subcommand


if __name__ == "__main__":
    sys.exit(main())
