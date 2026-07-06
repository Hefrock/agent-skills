---
name: wiki-librarian
description: Maintains the structural health of the wiki. Audits for broken links, orphan pages, stale notes, near-duplicates, and contradictions. Proposes and applies fixes with user confirmation. Run weekly or before a major synthesis session. Triggers on "audit my wiki," "clean up my vault," "check wiki health," "find duplicates," "fix broken links," and the command /audit. Requires the obsidian-vault MCP server connected. Pairs with wiki-operator for on-demand edits and wiki-synthesizer for content compilation.
---

# Wiki Librarian

Keeps the wiki structurally sound. The Librarian does not add knowledge — it preserves what's there by fixing the graph, removing noise, and surfacing what needs attention.

## Prerequisites

The `obsidian-vault` MCP server must be connected (same setup as wiki-operator). Verify with `/mcp` before running.

## Principles

1. **Report before changing.** Always present findings and get confirmation before modifying anything.
2. **Fix structure, not content.** The Librarian repairs links, merges duplicates, and updates metadata — it does not rewrite explanations. Use wiki-operator's `/update` for content improvements.
3. **Prefer repair over deletion.** A broken link gets fixed or redirected. An orphan page gets linked or flagged — not deleted without confirmation.
4. **Batch by risk.** Present low-risk fixes (update dates, fix obvious broken links) separately from high-risk fixes (merges, deletions) and confirm each batch independently.
5. **Leave a trail.** Append an audit log to today's journal after every run.

## /audit [focus]

Focus options: `links` (broken links only), `orphans`, `stale`, `duplicates`, `contradictions`, or omit for a full audit.

### Check 1 — Broken links

1. Walk all notes in Knowledge/, Sources/, Maps/, Projects/.
2. For each `[[wikilink]]` found, verify the target page exists.
3. Collect broken links grouped by source note.
4. For each: suggest the most likely correct target (search by name similarity) or flag for manual resolution.

### Check 2 — Orphan pages

1. List all notes in Knowledge/.
2. For each note, check inbound links (via `list_links`).
3. Flag notes with zero inbound links and fewer than two outbound links.
4. Suggest which existing page should link to the orphan, based on topic overlap.

### Check 3 — Stale notes

1. Query `status: stale` across all notes.
2. Also find notes where `updated:` is older than 90 days and `status` is not `mature`.
3. For each stale note: show its current content summary and ask whether to update, merge, or archive.

### Check 4 — Near-duplicates

1. Search for concept pages with overlapping titles or near-identical `## Core idea` sections.
2. Group suspected duplicates in pairs.
3. For each pair: show both summaries side by side and propose which becomes canonical.
4. Wait for confirmation before merging — never auto-merge.

### Check 5 — Contradictions

1. For each concept page with `confidence: low`, check whether any other page makes a conflicting claim about the same concept.
2. Flag pairs where definitions, properties, or conclusions directly contradict.
3. Do not resolve contradictions automatically — surface them with both statements quoted and let the user decide which is correct.

### Check 6 — Schema gaps

1. Find notes missing any required frontmatter field (`type`, `status`, `confidence`, `updated`).
2. Find notes with `confidence: low` that lack a `## Open questions` section.
3. Find notes with `status: mature` that link to fewer than two other pages (shouldn't be mature yet).

### Fix pass

After presenting all findings grouped by check, ask:
- "Apply all low-risk fixes automatically?" (broken link repairs, missing `updated:` dates, schema gap fills where the value is unambiguous)
- Confirm each medium-risk fix individually (orphan linking, stale note promotion)
- Confirm each high-risk fix explicitly (merges, status demotions)

### Audit log

Append to today's journal:

```markdown
## Librarian audit — YYYY-MM-DD

- Broken links found / fixed:
- Orphans found / linked:
- Stale notes reviewed:
- Duplicates merged:
- Contradictions flagged:
- Schema gaps fixed:
```

Update `Maps/_context.md` after fixes are applied.

## Suggested schedule

| Frequency | Trigger |
|---|---|
| Weekly | Run `/audit` at the start of the week |
| Before synthesis | Run `/audit links orphans` before a `/synthesize` session |
| After major edits | Run `/audit duplicates` after adding 5+ new pages |
