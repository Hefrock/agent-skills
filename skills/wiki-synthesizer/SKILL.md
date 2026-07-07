---
name: wiki-synthesizer
description: Compiles raw material into the wiki. Automatically preprocesses unstructured journal entries, then promotes ideas into Knowledge/ concept pages and compiles Sources/raw/ into source pages. Updates Maps/_context.md after each run. Use this after learning sessions. Triggers on "synthesize my notes," "compile my wiki," "promote from journal," "process raw sources," and the command /synthesize. Requires the obsidian-vault MCP server connected. Pairs with wiki-operator for on-demand edits and wiki-librarian for maintenance.
---

# Wiki Synthesizer

Runs the full raw → wiki pipeline. Handles both unstructured and structured journal entries — if an entry lacks a populated `## Ideas to promote` section, the Synthesizer preprocesses it first before promoting. The Synthesizer does not invent — it distills what you already captured.

## Prerequisites

The `obsidian-vault` MCP server must be connected. Verify with `/mcp` before running.

**Note on commands:** `/synthesize` is a natural-language trigger — write it in the chat window as plain text. It is NOT a Claude Code CLI slash command.

## Principles

1. **Distill, don't invent.** Only promote ideas explicitly present in journals or raw sources. Never add claims that weren't in the input.
2. **Update over create.** Search `Knowledge/` before creating any new concept page. If a page exists, enrich it — do not fork it.
3. **Preserve provenance.** Every concept page updated during synthesis gets a backlink to the journal entry or source that prompted the update.
4. **Non-destructive.** When preprocessing journal entries, never delete or rewrite original text — only append structured sections.
5. **One run, one log.** After each synthesis run, append a compact entry to today's journal documenting what changed.
6. **Update the hot cache last.** `Maps/_context.md` is written at the end of every run — never mid-run.

## /synthesize [scope]

Scope options: `journal` (journals only), `sources` (raw sources only), or omit to run both.

### Phase 0 — Journal preprocessing (conditional)

Runs automatically for each journal entry that lacks a populated `## Ideas to promote` section.

1. Read the journal file. If `## Ideas to promote` is already present and non-empty, skip to Phase 1.
2. Scan all content outside existing structured sections. For each fragment, classify:

| Class | Description | Action |
|---|---|---|
| **concept** | A durable insight, principle, or model — still useful a month from now | Add to `## Ideas to promote` |
| **question** | Something uncertain or not understood | Add to `## Open questions` |
| **quote** | A verbatim excerpt worth preserving | Add to `## Ideas to promote` with `> ` prefix |
| **task** | Action item or TODO | Leave in place — not wiki content |
| **transient** | Personal, social, or one-off event | Leave in place — not wiki content |

3. Append to the journal entry (never overwrite existing content):

```markdown
## Ideas to promote

- [Concept name] — [one sentence on what the Knowledge/ page should capture]

## Open questions

- [Question as a complete sentence ending in ?]
```

Rules:
- Extract 2–5 high-quality candidates. Thin lists beat padded ones.
- If zero concepts were found, write: `(none — content is tasks or transient)`
- If the entry is fewer than three non-trivial lines, flag and skip.

4. Stamp frontmatter: add `processed: YYYY-MM-DD`. If no frontmatter exists, create a minimal block.

### Phase 1 — Journal promotion

1. Read `Maps/_context.md` to orient. Note the last synthesis date.
2. List all journal files in `Journal/Daily/` updated since the last synthesis date.
3. For each journal file:
   a. Read the `## Ideas to promote` section (populated by Phase 0 or by the user directly).
   b. For each idea, search `Knowledge/` for an existing concept page.
   c. If found: add new context, add a backlink to the journal entry, set `updated:` to today.
   d. If not found: create a new page using the concept template, set `status: draft`, link back to the journal entry.
   e. Mark the promoted idea in the journal: move it to `## What I learned` with `→ promoted to [[ConceptPage]]`.
4. If a journal entry has open questions that map to existing concept pages, add them to that page's `## Open questions` section.

### Phase 2 — Raw source compilation

1. List all files in `Sources/raw/`.
2. For each raw file:
   a. Determine type: paper, book, video, article, or note dump.
   b. Create a compiled source page in the appropriate `Sources/` subfolder using the source template.
   c. Extract: core argument, key takeaways, quotes worth keeping.
   d. Link to concept pages in `Knowledge/` the source references — create stubs (`status: draft`) for any that don't exist yet.
   e. Delete the raw file after the compiled page is written.
3. For any concept stubs created, check whether a page already exists under a different name — merge if so.

### Phase 3 — Hot cache update

Rewrite `Maps/_context.md`:
- Update `## Recently updated` with pages touched this run.
- Update `## Open threads` — add new drafts, remove anything promoted to mature.
- Update `## Vault stats` counts.
- Set the last synthesis date to today.

### Phase 4 — Run log

Append to today's journal:

```markdown
## Synthesis run — YYYY-MM-DD

- Preprocessed: [N entries structured]
- Promoted: [[page]], [[page]]
- Compiled sources: [title], [title]
- New stubs: [[page]], [[page]]
- Skipped (already current): [[page]]
```

## Output discipline

- Report phase-by-phase as you go: "Phase 0: preprocessed 2 entries, found 5 concepts. Phase 1: promoting 5 ideas."
- List every page to be created or updated before writing it — do not write silently.
- If a journal idea is ambiguous (could map to multiple concept pages), surface the options and ask before promoting.
- If a raw source is too sparse to compile meaningfully, flag it rather than creating a thin page.
