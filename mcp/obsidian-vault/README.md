# obsidian-vault MCP

MCP server for the wiki-operator skill. Provides 10 tools for reading, writing, searching, and maintaining a local Obsidian vault, plus 2 more read-only tools for warehouse passage retrieval when `WAREHOUSE_PATH` is set (see below) — 12 tools total in that configuration.

## Tools

| Tool | Purpose |
|---|---|
| `search_notes` | Real BM25F-style full-text search with per-field weights (title 5x, tags 3x, body 1x), IDF, and length normalization — not a flat match-count score. Word-boundary token matching, not substring. |
| `read_note` | Read full note content + parsed frontmatter |
| `write_note` | Create or overwrite a note (creates parent folders) |
| `append_note` | Append content to an existing note without overwriting it; creates the file if missing, provided `content` starts with a frontmatter block — errors otherwise rather than silently creating a malformed note |
| `patch_section` | Replace content under a heading without touching the rest |
| `patch_frontmatter` | Merge fields into a note's frontmatter without touching the body |
| `query_frontmatter` | Find notes where a frontmatter field equals a value |
| `list_links` | Get outbound wikilinks + inbound backlinks for a note |
| `list_notes` | List all notes (optionally in a subfolder) with frontmatter |
| `delete_note` | Move a note to `.trash/` (recoverable, not permanent) — always confirm with the user first |
| `search_warehouse` *(requires `WAREHOUSE_PATH`)* | Read-only BM25 passage search over warehouse full text (primary sources, not the vault's distilled notes). Every hit carries a content-hash `doc_id`, Unicode-code-point character offsets, and a `passage_hash` for staleness detection. |
| `read_warehouse_text` *(requires `WAREHOUSE_PATH`)* | Read-only: an exact character span from a warehouse document's extracted text, for expanding context around a `search_warehouse` hit. |

## Warehouse retrieval (optional)

Set `WAREHOUSE_PATH` to a local clone of `Hefrock/knowledge-warehouse` to register `search_warehouse` and `read_warehouse_text`. If unset, those two tools simply don't exist — everything else is unaffected. If set but invalid (missing directory, or no `manifest.json`), the server logs why to stderr and still doesn't register them; it never exits, so the 10 vault tools keep working either way.

This is retrieval over **primary sources**, distinct from `search_notes`'s retrieval over the vault's **distilled** notes — see `references/warehouse-schema.md` (in `skills/wiki-warehouse/`) for the join contract between the two. Both warehouse tools are read-only and their descriptions remind the caller of the constitution's rule: distill what you find into a vault note, never paste warehouse text directly into one.

## Install

```bash
cd mcp/obsidian-vault
npm install
npm run build
```

## Configure (Claude Code)

**Recommended — via the CLI:**
```bash
claude mcp add obsidian-vault \
  -s user \
  -e OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault \
  -- node /absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js
```
`-s user` registers the server at the user level (available in every project) and writes to `~/.claude.json` for you. If `node` isn't found on `PATH` when Claude Code runs it, use `which node`'s output as the command instead of the bare `node`.

To also enable warehouse retrieval, add a second `-e` for `WAREHOUSE_PATH`:
```bash
claude mcp add obsidian-vault \
  -s user \
  -e OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault \
  -e WAREHOUSE_PATH=/absolute/path/to/your/knowledge-warehouse/clone \
  -- node /absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js
```

**Manual** — add this to `~/.claude.json` yourself:
```json
{
  "mcpServers": {
    "obsidian-vault": {
      "command": "node",
      "args": ["/absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/absolute/path/to/your/vault",
        "WAREHOUSE_PATH": "/absolute/path/to/your/knowledge-warehouse/clone"
      }
    }
  }
}
```
Omit `WAREHOUSE_PATH` entirely if you don't want warehouse retrieval — it's optional, not "set to empty."
Hand-editing this file directly can behave oddly in a GUI editor if a running Claude Code process has it open — the CLI method above avoids that entirely.

Then verify inside Claude Code:
```
/mcp
```

## Security

- Runs locally over STDIO — no network exposure
- Path traversal protection: all paths are resolved and validated against the vault root
- Only reads/writes `.md` files within `OBSIDIAN_VAULT_PATH`
- Recommend enabling git on your vault for reversibility
- Warehouse tools are read-only (never write to the vault or the warehouse) and apply the same symlink-aware containment check to every manifest entry's `text_path`; a malformed or malicious manifest entry is rejected and logged, not trusted, and never stops the rest of the corpus from indexing

## Vault structure expected by wiki-operator

```
Vault/
├── Knowledge/    ← concept notes
├── Journal/Daily/← daily notes
├── Sources/      ← papers, books, videos
├── Maps/         ← index/navigation pages
└── Projects/     ← active project notes
```
