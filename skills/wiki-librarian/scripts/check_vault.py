#!/usr/bin/env python3
"""Deterministic reference checker for wiki-librarian's mechanical rules.

wiki-librarian (skills/wiki-librarian/SKILL.md) is a prompt-driven skill, not
code — Claude reads it and reasons over the vault via MCP tools. There is no
program to unit-test in the usual sense. This script closes that gap for the
subset of the librarian's checks that are actually mechanical: it implements
the SAME rules the prompt describes, so a real audit run (human- or
Claude-driven) can be checked against an objective, re-runnable oracle
instead of "did the model interpret the prompt correctly this one time."

Implements, matching skills/wiki-librarian/SKILL.md exactly:
  - Check 1 — Broken links
  - Check 2 — Orphan pages       (Laws 6/7/8 system-file exemption applied)
  - Check 3 — Stale notes
  - Check 6 — Schema gaps        (including the Law 8 provenance check and
                                   the Law 6 premature-mature check, both
                                   with the system-file exemption applied)

Deliberately NOT implemented — both require semantic judgment a script
cannot make, not just more code:
  - Check 4 — Near-duplicates    (title/section similarity is a judgment
                                   call about meaning, not syntax)
  - Check 5 — Contradictions     (conflicting *claims* requires understanding
                                   what a note asserts, not just its shape)

stdlib only, matching this repo's other reference tooling (knowledge-warehouse's
intake.py/audit.py). No YAML library — frontmatter here is a small, controlled
subset (scalars and inline `[a, b]` lists), so a hand-rolled parser is simpler
and has one fewer moving part than pulling in PyYAML for it.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

REQUIRED_FIELDS = ("type", "status", "confidence", "updated")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
PROVENANCE_RE = re.compile(r"Captured from \[\[|Source:\s*\[\[")
OPEN_QUESTIONS_RE = re.compile(r"^##\s+open questions\b", re.IGNORECASE | re.MULTILINE)
STALE_DAYS = 90

# Scope of Laws 6, 7, and 8 (constitution.md): system files are machine-
# maintained, read by path rather than wikilink, and exempt from the
# island/provenance/premature-mature checks. Hand-authored Maps/ pages
# (e.g. Maps/AI.md) are NOT exempt.
SYSTEM_PREFIXES = ("System/",)


def is_system_file(relpath: str) -> bool:
    if relpath.startswith(SYSTEM_PREFIXES):
        return True
    if relpath.startswith("Maps/") and os.path.basename(relpath).startswith("_"):
        return True
    return False


def parse_frontmatter(raw: str):
    """Split '---\\nyaml\\n---\\nbody' into (fields dict, body str).

    Only supports what this vault's notes actually use: scalar `key: value`
    lines and inline list values like `tags: [a, b]`. No nested maps, no
    block-style lists, no multiline scalars — a real YAML doc would need
    PyYAML; this vault's frontmatter never does.
    """
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end == -1:
        return {}, raw
    yaml_block = raw[4:end]
    body = raw[end + 4:].lstrip("\n")

    fields = {}
    for line in yaml_block.splitlines():
        line = line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fields[key] = [v.strip() for v in inner.split(",")] if inner else []
        else:
            fields[key] = value.strip('"').strip("'")
    return fields, body


def walk_vault(root: str):
    """Yield vault-relative .md paths, skipping dotfiles/dirs (.trash/, .git/)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not name.endswith(".md"):
                continue
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root))
    return sorted(out)


def load_vault(root: str):
    """Return {relpath: {"frontmatter":..., "body":..., "links": [...]}}"""
    notes = {}
    for relpath in walk_vault(root):
        with open(os.path.join(root, relpath), "r", encoding="utf-8") as f:
            raw = f.read()
        frontmatter, body = parse_frontmatter(raw)
        links = [m.strip() for m in WIKILINK_RE.findall(body)]
        notes[relpath] = {"frontmatter": frontmatter, "body": body, "links": links}
    return notes


def resolve_link(target: str, notes: dict):
    """Resolve a wikilink target to a vault-relative path, or None if broken.

    Obsidian resolves by basename when the link isn't a full path — mirror
    that: try an exact relative-path match first, then a case-insensitive
    basename match.
    """
    target_md = target if target.endswith(".md") else target + ".md"
    if target_md in notes:
        return target_md
    target_base = os.path.basename(target_md).lower()
    for relpath in notes:
        if os.path.basename(relpath).lower() == target_base:
            return relpath
    return None


def check_broken_links(notes: dict):
    """Check 1 — every [[wikilink]] must resolve to an existing note."""
    findings = []
    for relpath, note in sorted(notes.items()):
        for target in note["links"]:
            if resolve_link(target, notes) is None:
                findings.append({"note": relpath, "broken_link": target})
    return findings


def check_orphans(notes: dict):
    """Check 2 — zero inbound AND fewer than two outbound links.

    Scoped to Knowledge/ (matching SKILL.md's "full audit" scope of
    Knowledge/ + Sources/ + Projects/), excluding system files (Laws 7/8).
    """
    inbound_count = {relpath: 0 for relpath in notes}
    for relpath, note in notes.items():
        for target in note["links"]:
            resolved = resolve_link(target, notes)
            if resolved and resolved != relpath:
                inbound_count[resolved] += 1

    scoped_prefixes = ("Knowledge/", "Sources/", "Projects/")
    findings = []
    for relpath, note in sorted(notes.items()):
        if is_system_file(relpath):
            continue
        if not relpath.startswith(scoped_prefixes):
            continue
        outbound = len(set(note["links"]))
        if inbound_count[relpath] == 0 and outbound < 2:
            findings.append({"note": relpath, "inbound": 0, "outbound": outbound})
    return findings


def check_stale(notes: dict, now: datetime):
    """Check 3 — status: stale, or updated > 90 days ago and not mature."""
    findings = []
    for relpath, note in sorted(notes.items()):
        fm = note["frontmatter"]
        status = fm.get("status")
        if status == "stale":
            findings.append({"note": relpath, "reason": "status: stale"})
            continue
        updated = fm.get("updated")
        if not updated:
            continue
        try:
            updated_dt = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        age_days = (now - updated_dt).days
        if age_days > STALE_DAYS and status != "mature":
            findings.append({"note": relpath, "reason": f"updated {age_days}d ago, status={status}"})
    return findings


def check_schema_gaps(notes: dict):
    """Check 6 — missing fields, confidence:low without Open questions,
    premature mature status, and (6.4) missing provenance backlink."""
    inbound_count = {relpath: 0 for relpath in notes}
    for relpath, note in notes.items():
        for target in note["links"]:
            resolved = resolve_link(target, notes)
            if resolved and resolved != relpath:
                inbound_count[resolved] += 1

    missing_fields, missing_open_questions, premature_mature, missing_provenance = [], [], [], []

    for relpath, note in sorted(notes.items()):
        fm = note["frontmatter"]

        missing = [f for f in REQUIRED_FIELDS if f not in fm]
        if missing:
            missing_fields.append({"note": relpath, "missing": missing})

        if fm.get("confidence") == "low" and not OPEN_QUESTIONS_RE.search(note["body"]):
            missing_open_questions.append({"note": relpath})

        if fm.get("status") == "mature" and not is_system_file(relpath):
            total_links = inbound_count[relpath] + len(set(note["links"]))
            if total_links < 2:
                premature_mature.append({"note": relpath, "total_links": total_links})

        # Law 8, scoped to Knowledge/ concept pages; system files exempt.
        if relpath.startswith("Knowledge/") and not is_system_file(relpath):
            if not PROVENANCE_RE.search(note["body"]):
                missing_provenance.append({"note": relpath})

    return {
        "missing_required_fields": missing_fields,
        "confidence_low_missing_open_questions": missing_open_questions,
        "premature_mature_status": premature_mature,
        "missing_provenance_backlink": missing_provenance,
    }


def run(vault_root: str, now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    notes = load_vault(vault_root)
    return {
        "notes_scanned": len(notes),
        "broken_links": check_broken_links(notes),
        "orphans": check_orphans(notes),
        "stale": check_stale(notes, now),
        "schema_gaps": check_schema_gaps(notes),
        "not_checked": [
            "Check 4 (near-duplicates) — requires semantic judgment, not implemented",
            "Check 5 (contradictions) — requires semantic judgment, not implemented",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    result = run(args.vault)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Scanned {result['notes_scanned']} notes in {args.vault}\n")
    print(f"Broken links: {len(result['broken_links'])}")
    for f in result["broken_links"]:
        print(f"  {f['note']} -> [[{f['broken_link']}]]")
    print(f"\nOrphans: {len(result['orphans'])}")
    for f in result["orphans"]:
        print(f"  {f['note']} (inbound={f['inbound']}, outbound={f['outbound']})")
    print(f"\nStale: {len(result['stale'])}")
    for f in result["stale"]:
        print(f"  {f['note']} — {f['reason']}")
    gaps = result["schema_gaps"]
    print(f"\nMissing required fields: {len(gaps['missing_required_fields'])}")
    for f in gaps["missing_required_fields"]:
        print(f"  {f['note']} — missing {f['missing']}")
    print(f"Confidence:low without Open questions: {len(gaps['confidence_low_missing_open_questions'])}")
    for f in gaps["confidence_low_missing_open_questions"]:
        print(f"  {f['note']}")
    print(f"Premature mature status: {len(gaps['premature_mature_status'])}")
    for f in gaps["premature_mature_status"]:
        print(f"  {f['note']} (total_links={f['total_links']})")
    print(f"Missing provenance backlink: {len(gaps['missing_provenance_backlink'])}")
    for f in gaps["missing_provenance_backlink"]:
        print(f"  {f['note']}")

    total_hard = (
        len(result["broken_links"]) + len(result["orphans"])
        + sum(len(v) for v in gaps.values())
    )
    sys.exit(1 if total_hard > 0 else 0)


if __name__ == "__main__":
    main()
