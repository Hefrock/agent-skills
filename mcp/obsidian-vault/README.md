# obsidian-vault MCP

MCP server for the wiki-operator skill: 10 tools to read, write, search, and maintain a local Obsidian vault, plus 2 read-only warehouse-retrieval tools when `WAREHOUSE_PATH` is set (12 total).

## Tools

| Tool | Purpose |
|---|---|
| `search_notes` | Real BM25F-style full-text search — per-field weights (title 5x, tags 3x, body 1x), IDF, length normalization, word-boundary matching — not a flat match-count or substring search |
| `read_note` | Read full note content + parsed frontmatter |
| `write_note` | Create or overwrite a note (creates parent folders) |
| `append_note` | Append to a note without overwriting it; creates the file if missing, but only if `content` starts with a frontmatter block — errors otherwise, never leaves a malformed note |
| `patch_section` | Replace content under a heading without touching the rest |
| `patch_frontmatter` | Merge fields into a note's frontmatter without touching the body |
| `query_frontmatter` | Find notes where a frontmatter field equals a value |
| `list_links` | Get outbound wikilinks + inbound backlinks for a note |
| `list_notes` | List all notes (optionally in a subfolder) with frontmatter |
| `delete_note` | Move a note to `.trash/` (recoverable, not permanent) — always confirm with the user first |
| `search_warehouse` *(requires `WAREHOUSE_PATH`)* | Read-only BM25 passage search over warehouse full text (primary sources, not the vault's distilled notes). Each hit carries a content-hash `doc_id`, Unicode-code-point offsets, and a `passage_hash` for staleness detection. |
| `read_warehouse_text` *(requires `WAREHOUSE_PATH`)* | Read-only: an exact character span from a warehouse document, to expand context around a `search_warehouse` hit. |

## Warehouse retrieval (optional)

Set `WAREHOUSE_PATH` to a local clone of `Hefrock/knowledge-warehouse` to register `search_warehouse` and `read_warehouse_text`. Unset, invalid, or missing `manifest.json` — the two tools just don't register (invalid/missing logs why to stderr); the server never exits and the 10 vault tools keep working regardless.

This is retrieval over **primary sources**, distinct from `search_notes`'s retrieval over the vault's **distilled** notes — see `references/warehouse-schema.md` (in `skills/wiki-warehouse/`) for the join contract between the two. Both tools are read-only; per the constitution, distill what you find into a vault note rather than pasting warehouse text directly into one.

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
  -e WAREHOUSE_PATH=/absolute/path/to/your/knowledge-warehouse/clone \
  -- node /absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js
```
Drop the `WAREHOUSE_PATH` line if you don't want warehouse retrieval. `-s user` registers the server at the user level (available in every project) and writes to `~/.claude.json` for you. If `node` isn't on `PATH` when Claude Code runs it, use `which node`'s output as the command instead of the bare `node`.

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
- Warehouse tools are read-only and apply the same symlink-aware containment check to every manifest entry's `text_path`; a bad entry is rejected and logged, not trusted, without blocking the rest of the corpus from indexing

## Vault structure expected by wiki-operator

```
Vault/
├── Knowledge/    ← concept notes
├── Journal/Daily/← daily notes
├── Sources/      ← papers, books, videos
├── Maps/         ← index/navigation pages
└── Projects/     ← active project notes
```
