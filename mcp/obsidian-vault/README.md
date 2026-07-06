# obsidian-vault MCP

MCP server for the wiki-operator skill. Provides 8 tools for reading, writing, searching, and maintaining a local Obsidian vault.

## Tools

| Tool | Purpose |
|---|---|
| `search_notes` | BM25-style full-text search with field boosts (title 5x, tags 3x, body 1x) |
| `read_note` | Read full note content + parsed frontmatter |
| `write_note` | Create or overwrite a note (creates parent folders) |
| `patch_section` | Replace content under a heading without touching the rest |
| `patch_frontmatter` | Update frontmatter fields without touching the body |
| `query_frontmatter` | Find notes where a frontmatter field equals a value |
| `list_links` | Get outbound wikilinks + inbound backlinks for a note |
| `list_notes` | List all notes (optionally in a subfolder) with frontmatter |

## Install

```bash
cd mcp/obsidian-vault
npm install
npm run build
```

## Configure (Claude Code)

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "obsidian-vault": {
      "command": "node",
      "args": ["/absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/absolute/path/to/your/vault"
      }
    }
  }
}
```

Then verify inside Claude Code:
```
/mcp
```

## Security

- Runs locally over STDIO — no network exposure
- Path traversal protection: all paths are resolved and validated against the vault root
- Only reads/writes `.md` files within `OBSIDIAN_VAULT_PATH`
- Recommend enabling git on your vault for reversibility

## Vault structure expected by wiki-operator

```
Vault/
├── Knowledge/    ← concept notes
├── Journal/Daily/← daily notes
├── Sources/      ← papers, books, videos
├── Maps/         ← index/navigation pages
└── Projects/     ← active project notes
```
