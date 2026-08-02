#!/usr/bin/env python3
"""Deterministic reference implementation of wiki-governor's Phase 3 health score.

Same rationale as skills/wiki-librarian/scripts/check_vault.py: wiki-governor
is a prompt-driven skill, not code, so this is an oracle to check a real
`/govern` run against, not a replacement for it. Health scoring is a natural
extension of that pattern — it's arithmetic over the same mechanical facts
check_vault.py already extracts (frontmatter, links, provenance), so this
imports check_vault rather than re-implementing vault loading a second time.
That reuse is deliberate, not just convenient: this project has hit real bugs
twice in one session from the same logic existing in two places that drifted
apart (the MCP server's query tokenization; the constitution's Laws 6/7/8
scope living in four files that had to be updated in lockstep). A duplicated
vault-parser would be the same risk. wiki-governor's own SKILL.md already
documents a hard prerequisite on wiki-librarian being loaded alongside it —
this import makes that existing coupling concrete in code instead of only
in prompt text.

Implements the six sub-metrics from skills/wiki-governor/SKILL.md Phase 3
exactly, including the conditional warehouse-integrity exclusion/renormalization
and the Laws 6/7/8 system-file exemption (reused from check_vault via
is_system_file).

Several scoping questions in the SKILL.md prose are genuinely ambiguous and
required an interpretation call — each is called out in a comment at the
point it's resolved, and summarized in the module-level ASSUMPTIONS constant
below so they're visible without reading the whole file. These are exactly
the kind of thing that should be tightened in the SKILL.md itself, not
silently decided once and forgotten.
"""

import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "wiki-librarian", "scripts"))
from check_vault import load_vault, resolve_link, is_system_file, PROVENANCE_RE  # noqa: E402

STALE_DAYS = 90
OPEN_QUESTIONS_SECTION_RE = re.compile(
    r"^##\s+open questions\b.*?\n(.*?)(?=\n##\s|\Z)", re.IGNORECASE | re.DOTALL | re.MULTILINE
)
LIST_ITEM_RE = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)

# The "knowledge graph" domain per the constitution's "Scope of Laws 6, 7,
# and 8" clause. ASSUMPTION: Maturity and Freshness apply here too, even
# though the SKILL.md prose doesn't repeat a scope qualifier for them the
# way Connectedness/Provenance/Resolution explicitly say "Knowledge/" /
# "concept pages" — Journal/ entries are dated snapshots, not something
# meant to be "kept fresh" or promoted to "mature," so including them would
# make both metrics measure the wrong thing.
KNOWLEDGE_GRAPH_PREFIXES = ("Knowledge/", "Sources/", "Projects/")

DEFAULT_WEIGHTS = {
    "connectedness": 0.20,
    "maturity": 0.15,
    "freshness": 0.10,
    "provenance": 0.20,
    "resolution": 0.15,
    "warehouse_integrity": 0.20,
}


def _total_links(relpath, notes, inbound_count):
    return inbound_count[relpath] + len(set(notes[relpath]["links"]))


def _inbound_counts(notes):
    counts = {relpath: 0 for relpath in notes}
    for relpath, note in notes.items():
        for target in note["links"]:
            resolved = resolve_link(target, notes)
            if resolved and resolved != relpath:
                counts[resolved] += 1
    return counts


def _scoped(notes, prefixes):
    # is_system_file() is currently redundant here: KNOWLEDGE_GRAPH_PREFIXES
    # never includes Maps/ or System/, so prefix filtering alone already
    # excludes system files from every caller. Kept anyway as the same
    # defense-in-depth check_vault.py documents for the identical reason —
    # confirmed redundant, not proven load-bearing, by deliberately removing
    # it in test_health_score.py and seeing no test fail.
    return [p for p in notes if p.startswith(prefixes) and not is_system_file(p)]


def connectedness(notes, inbound_count):
    """% of Knowledge/ pages with >=2 links. Literal SKILL.md scope: Knowledge/ only."""
    knowledge_notes = _scoped(notes, ("Knowledge/",))
    if not knowledge_notes:
        return None
    linked = sum(1 for p in knowledge_notes if _total_links(p, notes, inbound_count) >= 2)
    return linked / len(knowledge_notes)


def maturity(notes):
    """mature / (mature + draft + stale), scoped to the knowledge-graph domain.

    ASSUMPTION: notes with a status outside {mature, draft, stale} (there
    shouldn't be any per the schema, but defensively) are excluded from the
    denominator rather than treated as a fourth bucket.
    """
    scoped = _scoped(notes, KNOWLEDGE_GRAPH_PREFIXES)
    statuses = [notes[p]["frontmatter"].get("status") for p in scoped]
    countable = [s for s in statuses if s in ("mature", "draft", "stale")]
    if not countable:
        return None
    return countable.count("mature") / len(countable)


def freshness(notes, now):
    """% of pages (knowledge-graph domain) updated within 90 days."""
    scoped = _scoped(notes, KNOWLEDGE_GRAPH_PREFIXES)
    if not scoped:
        return None
    fresh = 0
    counted = 0
    for p in scoped:
        updated = notes[p]["frontmatter"].get("updated")
        if not updated:
            continue
        try:
            updated_dt = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        counted += 1
        if (now - updated_dt).days <= STALE_DAYS:
            fresh += 1
    if counted == 0:
        return None
    return fresh / counted


def provenance(notes):
    """% of Knowledge/ concept pages with a provenance backlink (Law 8)."""
    knowledge_notes = _scoped(notes, ("Knowledge/",))
    if not knowledge_notes:
        return None
    has_prov = sum(1 for p in knowledge_notes if PROVENANCE_RE.search(notes[p]["body"]))
    return has_prov / len(knowledge_notes)


def _count_open_question_entries(body):
    """Count individual bullet entries under '## Open questions', not just
    whether the section exists — the SKILL.md formula (1 - ratio, floored
    at 0 but explicitly NOT capped at 1) only makes sense if this can
    exceed 1 per page, i.e. it's counting entries, not presence."""
    m = OPEN_QUESTIONS_SECTION_RE.search(body)
    if not m:
        return 0
    return len(LIST_ITEM_RE.findall(m.group(1)))


def resolution(notes):
    """1 - (total open-question entries / total concept pages), floored at 0."""
    knowledge_notes = _scoped(notes, ("Knowledge/",))
    if not knowledge_notes:
        return None
    total_entries = sum(_count_open_question_entries(notes[p]["body"]) for p in knowledge_notes)
    ratio = total_entries / len(knowledge_notes)
    return max(0.0, 1.0 - ratio)


def warehouse_integrity(warehouse_stats):
    """1 - (corrupt+missing+dangling+0.5*drifted)/total, or None if not in use.

    warehouse_stats: None (no warehouse-linked notes — the caller should
    have checked this and simply not pass stats) or a dict with keys
    total_linked_docs, corrupt, missing, dangling, drifted (all ints).
    """
    if warehouse_stats is None or warehouse_stats.get("total_linked_docs", 0) == 0:
        return None
    penalty = (
        warehouse_stats["corrupt"] + warehouse_stats["missing"]
        + warehouse_stats["dangling"] + 0.5 * warehouse_stats["drifted"]
    )
    return max(0.0, 1.0 - penalty / warehouse_stats["total_linked_docs"])


def compute_health_score(vault_root, now=None, warehouse_stats=None, weights=None):
    """Returns {"score": 0-100, "components": {name: 0-1 or None}, "weights_used": {...}}"""
    now = now or datetime.now(timezone.utc)
    weights = dict(weights or DEFAULT_WEIGHTS)
    notes = load_vault(vault_root)
    inbound_count = _inbound_counts(notes)

    components = {
        "connectedness": connectedness(notes, inbound_count),
        "maturity": maturity(notes),
        "freshness": freshness(notes, now),
        "provenance": provenance(notes),
        "resolution": resolution(notes),
        "warehouse_integrity": warehouse_integrity(warehouse_stats),
    }

    # Conditional exclusion + renormalization — warehouse_integrity only,
    # per SKILL.md. (The other five assume a real vault has Knowledge/
    # pages; None there signals an empty/malformed vault, not "not in use.")
    active = {k: v for k, v in components.items() if v is not None}
    if not active:
        return {"score": None, "components": components, "weights_used": {}}

    active_weight_total = sum(weights[k] for k in active)
    weights_used = {k: weights[k] / active_weight_total for k in active}

    score = sum(active[k] * weights_used[k] for k in active) * 100
    return {"score": round(score, 1), "components": components, "weights_used": weights_used}


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = compute_health_score(args.vault)
    if args.json:
        print(json.dumps(result, indent=2))
        return

    if result["score"] is None:
        print("No scoreable content found in vault.")
        return
    print(f"Health: {result['score']}/100\n")
    for k, v in result["components"].items():
        if v is None:
            print(f"  {k}: n/a (excluded, weight redistributed)")
        else:
            w = result["weights_used"].get(k, 0)
            print(f"  {k}: {v:.2f}  (weight {w:.2f})")


if __name__ == "__main__":
    main()
