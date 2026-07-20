# Wiki Constitution

Non-negotiable rules that govern how Claude operates this wiki. These apply in every session, regardless of other instructions.

## Core laws

### 1. One canonical page per concept
Before creating any note, search `Knowledge/` for an existing page on the same concept. If one exists — even under a different name — update it; never create a duplicate. If a duplicate slips through anyway (naming drift, parallel creation), merge on discovery rather than leaving both: preserve all details from both sources, drop nothing. Enriching an existing page is always preferable to forking a new one.

### 2. Distill, don't invent
Only write claims that appear in the source material (journal entries, raw sources, the conversation). Never add context, examples, or conclusions that weren't in the input. If you're uncertain whether something was said vs. inferred, mark it `confidence: low` and add it to `## Open questions`.

### 3. Preserve uncertainty explicitly
`confidence: low` is not a weakness — it is an accurate representation of the current state of knowledge. Never upgrade a claim to `confidence: high` without evidence. Never present a guess as a fact.

### 4. Never act silently on destructive operations
Before creating, merging, or deleting any note, state what you're about to do and why. Wait for confirmation. The only exception is `write_note` for brand-new pages with `status: draft` — those can proceed without confirmation. Everything else requires a visible statement of intent.

### 5. The wiki is the source of truth — not the conversation
Conversations are ephemeral. The vault is permanent. If the conversation and the wiki contradict each other, the wiki wins unless the user explicitly corrects it. Never hold "knowledge" in the conversation that isn't also in the vault.

### 6. Status reflects reality
- `status: draft` — created today or clearly incomplete
- `status: mature` — stable, linked to ≥2 other pages, explanation is clear and complete
- `status: stale` — not updated in 90+ days, no recent inbound links

Never self-promote a page to `mature` if it lacks outbound links, an explanation, or was just created.

### 7. No islands
A page with zero inbound AND zero outbound links is not connected to the wiki — it is an island. Islands are draft by definition, regardless of what their `status` field claims.

### 8. Preserve provenance
Every concept page links back to the journal entry or source that prompted it: `Captured from [[Journal/Daily/YYYY-MM-DD]]`. Every source page links to the concept pages it informed: `Source: [[Sources/Papers/title]]`. This makes the knowledge graph auditable and traceable — it is what turns "no islands" (Law 7) into "connected to something specific and checkable."

### 9. One run, one log
After any synthesis or audit session, append a compact summary to today's journal. This creates a permanent record of what changed and why.

### 10. Distill, don't dump
Full text and original files live only in cold storage (the `knowledge-warehouse` repo, or wherever raw material is warehoused outside the vault) — never in a note. A note holds a summary, a few key excerpts, and a stable pointer back to the original (a `doc_id` or equivalent), not the whole document. If you're about to paste full source text into a note, stop; distill it instead.

## What this wiki is for

A personal knowledge base that compounds over time. The goal is not to capture everything — it is to capture the right things, connect them well, and make them retrievable when needed.

Quality over quantity. A vault with 50 well-connected, mature concept pages is more valuable than 500 isolated drafts.

## What this wiki is not

- A task manager — TODOs go elsewhere
- A diary — personal events stay in the journal, not Knowledge/
- A bookmark dump — raw sources are preprocessed and discarded, not hoarded
- A scratchpad — the conversation is the scratchpad; the vault is the output
