#!/usr/bin/env python3
"""Vault-wide privacy audit — walks an Obsidian vault's notes for Direct PII
and Secrets, reusing privacy-linter's scan_diff.py rather than reimplementing
detection.

wiki-privacy-audit (skills/wiki-privacy-audit/SKILL.md) is this script's live
entry point — unlike wiki-librarian's check_vault.py (a dev-only testing
oracle for checks that otherwise require Claude's judgment via MCP, since
near-duplicates/contradictions need semantic understanding), every check
here is fully mechanical (the exact same regex scanners privacy-linter
already uses for git diffs), so the live skill runs this script directly
rather than looping per-note MCP reads.

Reuses wiki-librarian's exact walk_vault() file-discovery convention
(vault-relative .md paths, skipping dotfiles/dirs) rather than inventing a
second one for the same vault, and privacy-linter's scan_diff.py per note
via subprocess — the same "call the shared script, don't duplicate its
logic" convention qa_gate_history.py and deid-reid-harness's
score_inference.py already use for their own cross-skill invocations.

Does NOT scan Metadata (EXIF/embedded properties in attached images) or the
vault's git history (if it has one) — both real, documented gaps, not silent
ones. See skills/wiki-privacy-audit/SKILL.md's "What's NOT covered here."

Advisory by default (always exits 0). Add a gate:
    check_vault_privacy.py /path/to/vault --block-on high

Usage:
    python check_vault_privacy.py /path/to/vault
    python check_vault_privacy.py /path/to/vault --json

Stdlib only.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_DIFF = os.path.normpath(os.path.join(HERE, "..", "..", "privacy-linter", "scripts", "scan_diff.py"))

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


def walk_vault(root):
    """Vault-relative .md paths, skipping dotfiles/dirs (.trash/, .git/,
    .obsidian/) — same convention as wiki-librarian's check_vault.py, so
    the two audits agree on what counts as "a note" in this vault."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not name.endswith(".md"):
                continue
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root))
    return sorted(out)


def scan_note(vault_root, relpath):
    """Runs privacy-linter's scan_diff.py --file against one note, with
    cwd set to vault_root so scan_diff.py's own `location` field comes
    back as the clean vault-relative path (e.g. "Journal/Daily/2026-09-
    01.md:3") rather than an absolute filesystem path that would leak the
    vault's local location into every finding. Returns scan_diff.py's own
    JSON Finding list verbatim — this script never invents its own
    finding shape or re-derives severity/class, only aggregates. A
    scanner crash (no valid JSON on stdout) is reported to stderr and
    treated as zero findings for that note — visible as a warning, not a
    silent "clean" result standing in for "couldn't check.\""""
    proc = subprocess.run(
        [sys.executable, SCAN_DIFF, "--file", relpath, "--json"],
        capture_output=True, text=True, cwd=vault_root,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"Warning: scan_diff.py produced no valid JSON for {relpath} (stderr: {proc.stderr.strip()})", file=sys.stderr)
        return []


def run(vault_root):
    notes = walk_vault(vault_root)
    findings = []
    for relpath in notes:
        findings.extend(scan_note(vault_root, relpath))
    return {"notes_scanned": len(notes), "findings": findings}


def check_gate(findings, block_on):
    if block_on is None:
        return False
    threshold = SEVERITY_ORDER[block_on]
    return any(SEVERITY_ORDER[f["severity"]] >= threshold for f in findings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--block-on", choices=["low", "medium", "high"], default=None,
                        help="Exit non-zero if any finding at or above this severity exists (default: advisory only, always exits 0)")
    args = parser.parse_args()

    if not os.path.isdir(args.vault):
        print(f"wiki-privacy-audit: {args.vault} is not a directory.", file=sys.stderr)
        sys.exit(2)

    result = run(args.vault)
    findings = result["findings"]

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Scanned {result['notes_scanned']} note(s) in {args.vault}")
        if not findings:
            print("wiki-privacy-audit: no findings.")
        else:
            print(f"wiki-privacy-audit: {len(findings)} finding(s)")
            for f in sorted(findings, key=lambda f: -SEVERITY_ORDER[f["severity"]]):
                print(f"  [severity: {f['severity']}] [class: {f['leak_class']}] [{f['finding']}] {f['reason']} ({f['location']})")

    if check_gate(findings, args.block_on):
        if not args.json:
            print(f"\nBLOCKED: a finding at or above '{args.block_on}' severity was found.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
