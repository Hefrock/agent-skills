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
│    wiki-synthesizer    ← journal preprocessing + wiki   │
│    wiki-librarian      ← structural audits              │
│    wiki-governor       ← maintenance loop + compliance  │
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
│  Maps/         ← index pages, hot cache, gap queue       │
│  Projects/     ← active project notes                   │
│  System/       ← constitution + templates (read-only)   │
└─────────────────────────────────────────────────────────┘
```

## Data flow

```
Daily capture
    │
    ▼
Journal/Daily/YYYY-MM-DD.md  (free-form or structured)
    │
    │  wiki-synthesizer (/synthesize)
    │  Phase 0: preprocess if ## Ideas to promote is absent
    │  Phase 1: promote ideas → Knowledge/ pages
    │  Phase 2: compile Sources/raw/ → Sources/ pages
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

## Governance artifacts — one writer per file

Two more `Maps/` files exist purely to feed `wiki-governor`, and each has exactly one writer:

| File | Writer | Reader | Purpose |
|---|---|---|---|
| `Maps/_ask_log.md` | `wiki-operator` `/ask` (append-only) | `wiki-governor` (read-only) | every question `/ask` couldn't ground in the vault |
| `Maps/_gaps.md` | `wiki-governor` (regenerated each run) | the user, next learning session | `## Open questions` + `_ask_log.md`, ranked into a to-learn queue |

No skill writes to a file it doesn't own. `/ask` never touches `_gaps.md`; `wiki-governor` never touches `_ask_log.md` — it only reads and compiles it. This is the same single-owner discipline the MCP server applies to writes (backup-before-overwrite, atomic rename).

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
2. `wiki-synthesizer` — for batch compilation runs (handles preprocessing automatically)
3. `wiki-librarian` — for maintenance passes
4. `wiki-governor` — for the weekly governance loop; orchestrates 2 and 3, then audits compliance and scores health

The MCP server must be connected before any skill is invoked.
