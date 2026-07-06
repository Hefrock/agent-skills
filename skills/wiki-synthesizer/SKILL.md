---
name: wiki-synthesizer
description: Compiles raw material into the wiki. Reads recent journal entries and promotes flagged ideas into Knowledge/ concept pages. Compiles unprocessed files from Sources/raw/ into compiled source pages. Updates Maps/_context.md after each run. Use this after learning sessions to crystallize what you captured into durable knowledge. Triggers on "synthesize my notes," "compile my wiki," "promote from journal," "process raw sources," and the command /synthesize. Requires the obsidian-vault MCP server connected. Pairs with wiki-operator for on-demand edits and wiki-librarian for maintenance.
---

# Wiki Synthesizer

Runs the raw → wiki compilation pipeline. Journals and raw sources are inputs; concept pages and source pages are outputs. The synthesizer does not invent — it distills what you already captured.

## Prerequisites

The `obsidian-vault` MCP server must be connected (same setup as wiki-operator). Verify with `/mcp` before running.

## Principles

1. **Distill, don't invent.** Only promote ideas explicitly flagged in journals or present in raw sources. Do not add claims that weren't in the input.
2. **Update over create.** Search Knowledge/ before creating any new concept page. If a page exists, enrich it — do not fork it.
3. **Preserve provenance.** Every concept page updated during synthesis gets a link back to the journal entry or source that prompted the update.
4. **One run, one log.** After each synthesis run, append a compact entry to today's journal documenting what was promoted, created, or compiled.
5. **Update the hot cache last.** `Maps/_context.md` is written at the end of every run — never mid-run.

## /synthesize [scope]

Scope options: `journal` (process recent journals only), `sources` (compile raw sources only), or omit scope to run both.

### Phase 1 — Journal promotion

1. Read `Maps/_context.md` to orient. Note the last synthesis date recorded there.
2. List all journal files in `Journal/Daily/` updated since the last synthesis date.
3. For each journal file:
   a. Read the `## Ideas to promote` section.
   b. For each idea listed, search Knowledge/ for an existing concept page.
   c. If found: retrieve the page, add the new context, add a backlink to the journal entry, set `updated:` to today.
   d. If not found: create a new page using the concept template (`assets/concept.md` in wiki-operator), set `status: draft`, link back to the journal entry.
   e. Clear the promoted idea from `## Ideas to promote` and move it to `## What I learned` with a note: `→ promoted to [[ConceptPage]]`.
4. If a journal entry has open questions that map to existing concept pages, add them to that page's `## Open questions` section.

### Phase 2 — Raw source compilation

1. List all files in `Sources/raw/`.
2. For each raw file:
   a. Determine type: paper, book, video, article, or note dump.
   b. Create a compiled source page in the appropriate `Sources/` subfolder using the source template (`assets/source.md` in wiki-operator).
   c. Extract: core argument, key takeaways, quotes worth keeping.
   d. Link to concept pages in Knowledge/ that the source references — create stubs with `status: draft` for any that don't exist yet.
   e. Delete the raw file after the compiled page is written.
3. For any concept stubs created, run a search to check whether a page already exists under a different name — merge if so.

### Phase 3 — Hot cache update

After both phases complete, rewrite `Maps/_context.md`:
- Update `## Recently updated` with pages touched this run.
- Update `## Open threads` — add new drafts, remove anything promoted to mature.
- Update `## Vault stats` counts.
- Set the last synthesis date.

### Phase 4 — Run log

Append to today's journal (`Journal/Daily/YYYY-MM-DD.md`):

```markdown
## Synthesis run — YYYY-MM-DD

- Promoted: [[page]], [[page]]
- Compiled sources: [title], [title]
- New stubs: [[page]], [[page]]
- Skipped (already current): [[page]]
```

## Output discipline

- Report phase-by-phase as you go: "Phase 1: found 3 ideas to promote in 2 journal entries."
- List every page created or updated before writing it — do not write silently.
- If a journal idea is ambiguous (could map to multiple concept pages), surface the options and ask before promoting.
- If a raw source is too sparse to compile meaningfully (less than a paragraph of content), flag it rather than creating a thin page.
