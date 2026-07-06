---
name: wiki-operator
description: Operates a Karpathy-style personal knowledge wiki by reading and writing an Obsidian vault directly via MCP — searching before creating, updating existing concept pages over adding new ones, merging duplicates, and keeping all notes linked. Use this skill for any vault operation: processing new learning into wiki notes, improving a concept page, finding connections between ideas, reviewing note quality, generating study prompts, or running maintenance. Triggers on "add this to my wiki," "update my notes on X," "what do I know about Y," "connect these ideas," "clean up my vault," and the commands /learn /update /connect /review /quiz /map /source /clean. Requires an Obsidian MCP server connected with read and write tool access.
---

# Wiki Operator

Claude acts directly on an Obsidian vault via MCP — not as a suggestion engine. The wiki is the source of truth; conversations are ephemeral.

## Prerequisites

The `obsidian-vault` MCP server (in `mcp/obsidian-vault/`) must be running and connected. Set it up once:

```bash
cd mcp/obsidian-vault && npm install && npm run build
```

Add to `~/.claude.json`:
```json
{
  "mcpServers": {
    "obsidian-vault": {
      "command": "node",
      "args": ["/absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js"],
      "env": { "OBSIDIAN_VAULT_PATH": "/absolute/path/to/your/vault" }
    }
  }
}
```

Verify with `/mcp` — should show `obsidian-vault` connected with 8 tools. If MCP tools are unavailable, stop and tell the user — do not simulate vault operations in the conversation.

## Principles

1. **Search before write.** Always search the vault before creating anything. If a relevant page exists, update it — do not create a duplicate.
2. **One canonical page per concept.** If two pages cover the same idea, merge them — preserve both sets of details, do not truncate either.
3. **Prefer durable over ephemeral.** Extract durable knowledge from journals into `Knowledge/`. Leave dates and session context in the journal; promote only the insight.
4. **Preserve uncertainty.** Mark low-confidence claims with `confidence: low` in frontmatter. Never guess and present it as fact.
5. **Keep explanations compositional.** One clear sentence beats a dense paragraph. Link to related concepts instead of re-explaining them inline.
6. **The wiki evolves, it does not reset.** Each update improves an existing page. Orphaned content is either upgraded or merged — not abandoned.

## Note schema

Every note must carry this frontmatter:

```yaml
type: concept | journal | source | map | project
status: mature | draft | stale
confidence: high | medium | low  # accurate=high; uncertain/incomplete=low — not about writing quality
updated: YYYY-MM-DD
```

- `type` determines which template to follow (see `assets/`).
- `status: mature` — stable, clearly written, linked to at least two other pages.
- `status: draft` — new or incomplete. Default for anything just created.
- `status: stale` — hasn't been updated and has no incoming links. Flag, don't delete.
- `confidence` is about accuracy, not polish. Any `low` page must have an `## Open Questions` section.

## Vault structure

```
Knowledge/          ← canonical concept pages  (type: concept)
  AI/
  Systems/
  Math/
  Engineering/
Journal/
  Daily/            ← daily notes              (type: journal)
Sources/
  Papers/           ← one page per source      (type: source)
  Books/
  Videos/
Maps/               ← navigation/index pages   (type: map)
Projects/           ← active project pages     (type: project)
```

## Commands

### /learn
Process new information into the wiki.
1. Search the vault for existing pages on the topic.
2. If a concept page exists: retrieve it, update the explanation, add new context, add any missing links.
3. If no page exists: create one using `assets/concept.md`, set `status: draft`.
4. Append a brief entry to today's journal (`Journal/Daily/YYYY-MM-DD.md`) noting what was learned and linking to the updated concept page(s). Create the journal page from `assets/journal.md` if it doesn't exist yet.
5. Update the relevant map page in `Maps/` if the concept is new to that area.

### /update [page or concept]
Improve a specific page.
1. Retrieve the page.
2. Simplify dense sentences, fix unclear explanations, break up walls of text.
3. Add or repair links to related concepts.
4. Set `updated:` to today's date.
5. Promote `status` from `draft` → `mature` only when: the explanation is clear and self-contained, and the page links to at least two others.

### /connect [concept A] [concept B]
Find and create links between ideas.
1. Retrieve both pages.
2. Identify the relationship type: is-a, uses, contrasts-with, depends-on, extends, or instance-of.
3. Add a link sentence to each page's `## Related` section, naming the relationship explicitly.
4. Set `updated:` on both pages.

### /review [page or area]
Critique wiki quality without rewriting.
1. Retrieve the page or list pages under the area.
2. Flag: vague explanations, missing links, low-confidence claims without an open-questions section, and near-duplicate coverage with other pages.
3. Do not rewrite automatically. Surface the findings and confirm with the user before making changes.
4. If more than three issues are found, list them and ask which to address first.

### /quiz [topic]
Generate study prompts from wiki content.
1. Retrieve the concept page(s) for the topic.
2. Generate 3–5 questions at progressive difficulty: recall → application → synthesis.
3. Do not show answers — wait for the user to respond before discussing.

### /map [area]
Update a navigation/index page for an area of the vault.
1. Retrieve the map page (e.g. `Maps/AI.md`). Create it from `assets/map.md` if it doesn't exist.
2. List all concept pages under `Knowledge/[Area]/`.
3. Group them by sub-theme.
4. Add missing pages to the map; remove dead links.
5. Set `updated:` to today's date.

### /source [title or URL]
Log a paper, book, video, or article to the vault.
1. Create a page in the appropriate `Sources/` subfolder (e.g. `Sources/Papers/title.md`) using `assets/source.md`.
2. Fill in author, link/DOI, and today's read date from whatever the user provides.
3. Summarize the core argument in one paragraph.
4. Link to any concept pages in `Knowledge/` the source references — create stubs with `status: draft` for concepts that don't exist yet.
5. If the source relates to an active project, add a backlink in `Projects/[project].md`.

### /clean
Merge duplicates and repair structure.
1. Search for near-duplicate concept pages (same topic, different naming).
2. Propose the merge plan: which page becomes canonical, which gets absorbed.
3. Wait for confirmation before changing anything.
4. After confirmed: move content into the canonical page, fix or remove the absorbed page, repair backlinks throughout the vault.

## Output discipline

- After any write operation, confirm in one line what changed: "Updated `Knowledge/AI/transformers.md` — added attention-scaling section, linked to `positional-encoding`."
- Never silently create a note. If you are about to create a new page, say so first.
- Never silently merge or delete. Always confirm destructive changes before applying them.
- If MCP tools are unavailable, stop and tell the user — do not simulate vault operations in the conversation.
