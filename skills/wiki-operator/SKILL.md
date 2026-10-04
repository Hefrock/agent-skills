---
name: wiki-operator
description: Operates a Karpathy-style personal knowledge wiki by reading and writing an Obsidian vault directly via MCP — searching before creating, updating existing concept pages over adding new ones, merging duplicates, and keeping all notes linked. Use this skill for any vault operation: processing new learning into wiki notes, improving a concept page, finding connections between ideas, reviewing note quality, generating study prompts, or running maintenance. Triggers on "add this to my wiki," "update my notes on X," "what do I know about Y," "connect these ideas," "clean up my vault," and the commands /learn /update /connect /ask /review /quiz /map /source /clean /health. Requires an Obsidian MCP server connected with read and write tool access.
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

Verify with `/mcp` — should show `obsidian-vault` connected with 10 tools. If MCP tools are unavailable, stop and tell the user — do not simulate vault operations in the conversation.

**Note on commands:** The trigger phrases like `/learn`, `/update`, `/synthesize`, etc. shown in this skill are natural-language shorthand — they work when written in the chat window as plain text (e.g. type `/learn` or "add this to my wiki: ..."). They are NOT Claude Code CLI slash commands. Typing `/learn` in the Claude Code terminal will hit the CLI parser and fail — use the phrase in a chat message instead.

## Session start

At the beginning of any wiki session, read `Maps/_context.md` first if it exists. It contains a compact summary of the wiki's current state — active areas, recently updated pages, open questions — so you don't start cold. After any session that makes significant changes, update `_context.md` to reflect what changed.

## Principles

1. **Search before write.** Always search the vault before creating anything. If a relevant page exists, update it — do not create a duplicate.
2. **One canonical page per concept.** If two pages cover the same idea, merge them — preserve both sets of details, do not truncate either.
3. **Prefer durable over ephemeral.** Extract durable knowledge from journals into `Knowledge/`. Leave dates and session context in the journal; promote only the insight.
4. **Preserve uncertainty.** Mark low-confidence claims with `confidence: low` in frontmatter. Never guess and present it as fact.
5. **Gate confidence on verification, not plausibility.** `confidence: high` requires `verification: vendor` (the claim traces to the vendor/author's own docs) or `verification: multi-source` (≥2 independent sources agree — not two listings of the same registry). A single registry hit, however plausible-sounding, is `verification: single-source` and caps at `confidence: medium` until corroborated.
6. **Flag identity claims resting on a name match.** If what establishes *who made this* or *what this is* is the name/label matching something familiar — not the source's own stated lineage or taxonomy — tag it `verification: name-match` regardless of how confident the prose sounds, cap `confidence` at `medium`, and add an `## Open Questions` line naming the specific identity claim as unverified. A name that reads as official (a familiar project name, a vendor-style version string) is exactly the shape of claim most likely to be a reskin, fork, or community rebrand wearing a borrowed name.
7. **Keep explanations compositional.** One clear sentence beats a dense paragraph. Link to related concepts instead of re-explaining them inline.
8. **The wiki evolves, it does not reset.** Each update improves an existing page. Orphaned content is either upgraded or merged — not abandoned.

## Note schema

Every note must carry this frontmatter:

```yaml
type: concept | journal | source | map | project
status: mature | draft | stale
confidence: high | medium | low  # accurate=high; uncertain/incomplete=low — not about writing quality
verification: vendor | multi-source | single-source | name-match | unverified
verified_against:  # required when verification is vendor or multi-source — see below
updated: YYYY-MM-DD
```

- `type` determines which template to follow (see `assets/`).
- `status: mature` — stable, clearly written, linked to at least two other pages.
- `status: draft` — new or incomplete. Default for anything just created.
- `status: stale` — hasn't been updated and has no incoming links. Flag, don't delete.
- `confidence` is about accuracy, not polish. Any `low` page must have an `## Open Questions` section.
- `verification` records *how* a claim was verified, and gates what `confidence` it's allowed to carry:
  - `vendor` — traced to the vendor/author's own docs, announcement, or site. Only tier that freely supports `confidence: high`.
  - `multi-source` — ≥2 independently-sourced references agree (not two listings pulled from the same registry/search). Also supports `confidence: high`.
  - `single-source` — one source only. Caps `confidence` at `medium` until corroborated.
  - `name-match` — the claim's identity (who made this, what lineage it belongs to) rests on the name/label matching something familiar, not on the source's own stated taxonomy. Caps `confidence` at `medium` *regardless of how certain the writing sounds*, and requires an `## Open Questions` line naming the unverified identity claim — same requirement as `confidence: low`, because this is where reskins, forks, and community rebrands wearing a borrowed vendor name slip through.
  - `unverified` — not checked at all. Caps `confidence` at `medium`, same as `single-source`.
  - Omitting `verification` on an existing older page is fine (don't retrofit on sight) — but any *new* page or *edit* that touches a factual/identity claim should set it.
- `verified_against` is the citation artifact backing `vendor` or `multi-source`: the actual URL(s) or a short quoted source name (e.g. `verified_against: "huggingface.co/NousResearch, nousresearch.com/hermes4"`) — not a restatement of the claim itself. **Required whenever `verification` is `vendor` or `multi-source`** — a bare enum value with nothing behind it is a self-reported tag, not evidence, and reproduces the exact failure this field exists to prevent (asserting confidence without anything to point back to). Leave empty for `single-source`, `name-match`, or `unverified` — the enum already says the evidence is thin, no citation to fabricate.

**`type: project` additionally supports** (added for `wiki-teacher`):
```yaml
status: paused | complete    # extends the base status vocabulary above
priority: high | medium | low    # optional — elicited on first /checkin, not guessed
checkin_interval: <days>         # optional, defaults to 14 if absent
```
- `status: paused` / `status: complete` — an intentional off-ramp. Either excludes the project from `wiki-teacher`'s `/checkin` and from `wiki-librarian`'s staleness check; neither counts toward or against `wiki-governor`'s maturity sub-metric.
- `priority` is deliberately never defaulted — guessing would just reintroduce the ranking problem it exists to solve. `/checkin` asks for it the first time it's needed and writes the answer back.
- `checkin_interval` defaults silently to 14 days — safe to assume since it only ever under-triggers, never over-triggers a check-in.

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
  raw/              ← unprocessed clippings, before /source compiles them
  Papers/           ← one compiled page per source  (type: source)
  Books/
  Videos/
Maps/               ← navigation/index pages   (type: map)
  _context.md       ← hot cache: compact wiki state, read first each session
  _ask_log.md       ← append-only log of unanswered /ask queries (governor reads, never writes)
Projects/           ← active project pages     (type: project)
```

## Commands

### /learn
Process new information into the wiki.
1. Search the vault for existing pages on the topic.
2. If a concept page exists: retrieve it, update the explanation, add new context, add any missing links.
3. If no page exists: create one using `assets/concept.md`, set `status: draft`.
4. Before writing any factual or identity claim (what something is, who made it, what it's built on), set `verification` per the rules above, and let it gate `confidence` — don't default to `confidence: high` because the claim sounds settled or came from a single plausible-looking search result. If claiming `vendor` or `multi-source`, fill in `verified_against` with the actual URL(s)/source name checked — if there's nothing to put there, the tier is lower than you think. If the claim's identity rests on a name/label matching something familiar rather than the source's own stated lineage, this is a `name-match` case: cap at `medium` and log the specific unverified part under `## Open Questions`.
5. Append a brief entry to today's journal (`Journal/Daily/YYYY-MM-DD.md`) noting what was learned and linking to the updated concept page(s). **If the journal page doesn't exist yet, create it first** with `write_note` from `assets/journal.md` (so it starts with proper frontmatter), *then* `append_note` the entry — `append_note` refuses to silently create a frontmatter-less file, by design.
6. Update the relevant map page in `Maps/` if the concept is new to that area.

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

### /ask [question]
Answer a question by retrieving and grounding an answer in the vault — never from general knowledge alone, and never silently.
1. Decompose the question into 1–3 search terms. A question spanning multiple concepts ("how does X relate to Y") gets one search per concept.
2. `search_notes` each term, scoped to `Knowledge/` and `Sources/` first — the vetted material.
3. `read_note` the top 3–5 matches.
4. Optional one-hop expansion: `list_links` on the strongest match to pull in tightly connected notes that complete the answer.
5. If the retrieved content answers the question, compose the answer from it alone:
   - Every substantive claim carries an inline `[[link]]` to its source note.
   - If the only relevant page is `status: draft` or `confidence: low`, say so — don't present it with more authority than the vault itself claims.
   - If sources disagree, surface the disagreement rather than smoothing it over.
   - If the answer draws on two clusters that aren't linked to each other yet, note it and offer to run `/connect`.
6. If nothing vetted is found, check `Journal/Daily/` as a last resort — but flag any journal-sourced content explicitly as unprocessed, not yet promoted to `Knowledge/`.
7. If a retrieved Source note points to a warehoused document (`doc_id` in its frontmatter) but its own distilled summary and excerpts don't have enough detail to answer confidently, go to the primary source: `search_warehouse` (scoped to that `doc_id` if one specific Source note is the target) for passages, `read_warehouse_text` to expand context around a hit if still not enough. Requires `WAREHOUSE_PATH` to be configured — if `search_warehouse` isn't available, skip this hop, same as any other missing tool. Cite a claim that came from this hop by `doc_id` + `char_start`/`char_end`, not just the note's `[[link]]`; the note's own excerpts stay the citation for anything they already cover. Same distill-don't-paste rule as ingestion: pull the fact into the answer, never paste the raw passage.
8. If still nothing relevant exists, or what's found doesn't actually answer the question:
   - Say so plainly — do not reach for general knowledge to fill the gap.
   - Append an entry to `Maps/_ask_log.md` (create from `assets/ask-log.md` if it doesn't exist yet). This logging always happens — no confirmation needed, same as any new-draft creation.
   - Then ask whether the user wants a general-knowledge answer instead, clearly labeled as coming from outside the vault. Never blend the two without saying so.
9. For partial answers, ground what the vault supports and log only the ungrounded portion.

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
1. If raw content exists in `Sources/raw/`, read it first. Otherwise use what the user provides.
2. Create a compiled page in the appropriate `Sources/` subfolder (e.g. `Sources/Papers/title.md`) using `assets/source.md`.
3. Fill in author, link/DOI, and today's read date.
4. Summarize the core argument in one paragraph.
5. Set `verification`: `vendor` if this source *is* the vendor/author's own publication; otherwise `single-source` (or `multi-source` if it corroborates an existing page's claim from a different origin) — gate `confidence` accordingly. If the source is a registry/marketplace listing (package index, model hub, app store) rather than an authored publication, treat its naming/attribution as `name-match` verification, not `vendor`, until the actual publisher is confirmed. For `vendor`/`multi-source`, set `verified_against` to the link/DOI already captured in step 3 (or the additional corroborating link, for `multi-source`) — don't leave it blank while claiming the higher tier.
6. Link to any concept pages in `Knowledge/` the source references — create stubs with `status: draft` for concepts that don't exist yet.
7. If the source relates to an active project, add a backlink in `Projects/[project].md`.
8. Delete or archive the raw file once the compiled page is complete.

### /clean
Merge duplicates and consolidate structure.
1. Search for near-duplicate concept pages (same topic, different naming).
2. Propose the merge plan: which page becomes canonical, which gets absorbed.
3. Wait for confirmation before changing anything.
4. After confirmed: move content into the canonical page, fix or remove the absorbed page, repair backlinks throughout the vault.

### /health
Audit structural integrity of the vault. Run before any major compile session.
1. **Broken links** — find wikilinks pointing to pages that don't exist. List them with their source note.
2. **Orphan pages** — find notes with no inbound links and no outbound links to other wiki pages.
3. **Stale notes** — query `status: stale` and notes with `updated:` older than 90 days.
4. **Missing open questions** — find notes with `confidence: low`, or `verification: name-match`, that lack a `## Open Questions` section.
5. **Confidence/verification mismatch** — find notes with `confidence: high` whose `verification` is `single-source`, `name-match`, `unverified`, or absent entirely; *also* find notes with `verification: vendor` or `multi-source` that have no `verified_against` citation — a self-reported tier with nothing backing it is the same gap wearing a disguise. These are exactly the shape of the "Hermes 4 35B-A3B" error (a claim that looked settled but was never actually corroborated against source) — flag for re-verification, don't just silently downgrade.
6. **Contradictions** — flag pairs of pages that make conflicting claims about the same concept (e.g., opposite definitions, incompatible properties).
7. Present findings as a prioritized list. Do not fix anything automatically — confirm with the user which issues to address.
8. After fixes are applied, update `Maps/_context.md` to reflect current vault state.

## Output discipline

- After any write operation, confirm in one line what changed: "Updated `Knowledge/AI/transformers.md` — added attention-scaling section, linked to `positional-encoding`."
- Never silently create a note. If you are about to create a new page, say so first.
- Never silently merge or delete. Always confirm destructive changes before applying them.
- Never blend a vault-grounded answer with general knowledge without saying so explicitly (see `/ask`).
- If MCP tools are unavailable, stop and tell the user — do not simulate vault operations in the conversation.
