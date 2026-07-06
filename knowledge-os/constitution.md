# Wiki Constitution

Non-negotiable rules that govern how Claude operates this wiki. These apply in every session, regardless of other instructions.

## Core laws

### 1. Search before write
Before creating any note, search `Knowledge/` for an existing page on the same concept. If one exists — even under a different name — update it. Never create a duplicate. One canonical page per concept, always.

### 2. Distill, don't invent
Only write claims that appear in the source material (journal entries, raw sources, the conversation). Never add context, examples, or conclusions that weren't in the input. If you're uncertain whether something was said vs. inferred, mark it `confidence: low` and add it to `## Open questions`.

### 3. Preserve uncertainty explicitly
`confidence: low` is not a weakness — it is an accurate representation of the current state of knowledge. Never upgrade a claim to `confidence: high` without evidence. Never present a guess as a fact.

### 4. Never act silently on destructive operations
Before creating, merging, or deleting any note, state what you're about to do and why. Wait for confirmation. The only exception is `write_note` for brand-new pages with `status: draft` — those can proceed without confirmation. Everything else requires a visible statement of intent.

### 5. The wiki is the source of truth — not the conversation
Conversations are ephemeral. The vault is permanent. If the conversation and the wiki contradict each other, the wiki wins unless the user explicitly corrects it. Never hold "knowledge" in the conversation that isn't also in the vault.

### 6. Update over create
Enriching an existing page is always preferable to creating a new one. If a page covers 80% of what a new page would cover, merge — don't fork. Merges must preserve all details from both sources; nothing is dropped.

### 7. Status reflects reality
- `status: draft` — created today or clearly incomplete
- `status: mature` — stable, linked to ≥2 other pages, explanation is clear and complete
- `status: stale` — not updated in 90+ days, no recent inbound links

Never self-promote a page to `mature` if it lacks outbound links, an explanation, or was just created.

### 8. Backlinks are mandatory
Every concept page must link back to the journal entry or source that prompted it. Every source page must link to the concept pages it informed. A page with zero inbound AND zero outbound links is not connected to the wiki — it is an island. Islands are draft by definition.

### 9. Preserve provenance
When promoting an idea from a journal entry, include a link: `Captured from [[Journal/Daily/YYYY-MM-DD]]`. When compiling a source, include: `Source: [[Sources/Papers/title]]`. This makes the knowledge graph auditable and traceable.

### 10. One run, one log
After any synthesis or audit session, append a compact summary to today's journal. This creates a permanent record of what changed and why.

## What this wiki is for

A personal knowledge base that compounds over time. The goal is not to capture everything — it is to capture the right things, connect them well, and make them retrievable when needed.

Quality over quantity. A vault with 50 well-connected, mature concept pages is more valuable than 500 isolated drafts.

## What this wiki is not

- A task manager — TODOs go elsewhere
- A diary — personal events stay in the journal, not Knowledge/
- A bookmark dump — raw sources are preprocessed and discarded, not hoarded
- A scratchpad — the conversation is the scratchpad; the vault is the output
