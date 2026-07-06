# System Architecture

How the wiki system fits together — the components, their responsibilities, and how they interact.

## Component map

```
┌─────────────────────────────────────────────────────────┐
│                      agent-skills                       │
│                                                         │
│  knowledge-os/constitution.md ← rules all skills follow │
│                                                         │
│  skills/                                                │
│    wiki-operator       ← on-demand vault operations     │
│    wiki-journal-processor ← journal preprocessing      │
│    wiki-synthesizer    ← journal/source → wiki          │
│    wiki-librarian      ← structural audits              │
└───────────────────┬─────────────────────────────────────┘
                    │ MCP (STDIO)
                    ▼
┌─────────────────────────────────────────────────────────┐
│              obsidian-vault MCP server                  │
│              (agent-skills/mcp/obsidian-vault/)         │
│                                                         │
│  search_notes    read_note      write_note              │
│  append_note     patch_section  patch_frontmatter       │
│  query_frontmatter  list_links  list_notes              │
│  delete_note                                            │
└───────────────────┬─────────────────────────────────────┘
                    │ filesystem
                    ▼
┌─────────────────────────────────────────────────────────┐
│                    Obsidian vault                       │
│                                                         │
│  Knowledge/    ← concept pages (the wiki)               │
│  Journal/Daily/ ← daily capture                        │
│  Sources/raw/  ← unprocessed input                      │
│  Sources/      ← compiled source pages                  │
│  Maps/         ← index pages + hot cache                │
│  Projects/     ← active project notes                   │
│  System/       ← constitution + templates (read-only)   │
└─────────────────────────────────────────────────────────┘
```

## Data flow

```
Daily capture
    │
    ▼
Journal/Daily/YYYY-MM-DD.md  (raw, free-form)
    │
    │  wiki-journal-processor (/process)
    ▼
Journal/Daily/YYYY-MM-DD.md  (structured: ## Ideas to promote)
    │
    │  wiki-synthesizer (/synthesize)
    ▼
Knowledge/<concept>.md  +  Sources/<type>/<title>.md
    │
    │  wiki-librarian (/audit)
    ▼
Healthy, connected knowledge graph
```

## The hot cache

`Maps/_context.md` is a compact summary of the vault's current state — active areas, recently updated pages, open threads, vault stats, and the `last_synthesized` date. Every skill reads it at session start to orient without scanning the full vault.

Update `_context.md` at the end of any session that makes significant changes. wiki-synthesizer does this automatically in Phase 3.

## Note lifecycle

```
Sources/raw/file.txt
    → wiki-synthesizer compiles → Sources/Papers/title.md (status: draft)
    → wiki-operator improves   → (status: draft, confidence: medium)
    → connections added        → (links to ≥2 concept pages)
    → wiki-operator promotes   → (status: mature)
    → no activity for 90 days  → wiki-librarian flags (status: stale)
```

## Skill load order

For a full wiki session, load skills in this order:
1. `wiki-operator` — always required
2. `wiki-journal-processor` — if you have unstructured journals to process
3. `wiki-synthesizer` — for batch compilation runs
4. `wiki-librarian` — for maintenance passes

The MCP server must be connected before any skill is invoked.
