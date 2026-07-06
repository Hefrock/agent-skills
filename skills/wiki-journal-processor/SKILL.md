---
name: wiki-journal-processor
description: Preprocesses daily journal entries for wiki synthesis — scans free-form notes, extracts durable knowledge fragments, and structures them into a clean "Ideas to promote" section that wiki-synthesizer can reliably act on. Run after a journaling session and before /synthesize when notes are messy or unstructured. Triggers on "process my journal," "structure today's notes," "prep for synthesis," "extract ideas from journal," and the command /process. Requires the obsidian-vault MCP server connected. Upstream of wiki-synthesizer; pairs with wiki-operator for on-demand edits.
---

# Wiki Journal Processor

Turns messy daily capture into structured journal entries that wiki-synthesizer can reliably promote from. The Processor does not add knowledge to the wiki — it organizes what you already captured so the Synthesizer can.

## Prerequisites

The `obsidian-vault` MCP server must be connected (same setup as wiki-operator). Verify with `/mcp` before running.

**Note on commands:** `/process` is a natural-language trigger — write it in the chat window as plain text. It is NOT a Claude Code CLI slash command.

## Principles

1. **Non-destructive.** Never delete or rewrite original journal text. Only append structured sections below existing content.
2. **Conservative extraction.** When uncertain whether a fragment is durable knowledge or transient context, classify it as a question rather than a concept.
3. **Signal over noise.** Extract 2–5 high-quality candidates per entry. Thin concept lists beat long ones padded with tasks and observations.
4. **Temporal context stays in the journal.** Dates, people, events, TODOs, logistics — not wiki content. Only conceptual insights that are true beyond today belong in `## Ideas to promote`.
5. **Idempotent.** If an entry already has `## Ideas to promote` with content, do not overwrite — report what's there and ask before modifying.

## /process [date or range]

**Scope options:**
- `/process` — today's journal (`Journal/Daily/YYYY-MM-DD.md`)
- `/process 2026-07-06` — specific date
- `/process 2026-07-01 2026-07-06` — inclusive date range (processes each day in order)

### Phase 1 — Read and classify

1. Read the target journal file(s). If a file doesn't exist, report and skip — do not create empty journals.
2. Check whether an existing `## Ideas to promote` section is present:
   - If present and non-empty: report its current contents and ask whether to re-process or leave it.
   - If present but empty: proceed to fill it.
   - If absent: proceed to create it.
3. Scan all content outside existing structured sections (`## Ideas to promote`, `## Open questions`, `## What I learned`). For each fragment, classify:

| Class | Description | Action |
|---|---|---|
| **concept** | A durable insight, principle, mechanism, or model | Add to `## Ideas to promote` |
| **question** | Something uncertain, surprising, or not understood | Add to `## Open questions` |
| **quote** | A verbatim excerpt worth preserving verbatim | Add to `## Ideas to promote` with `> ` prefix and attribution |
| **task** | An action item or TODO | Leave in place — not wiki content |
| **transient** | Personal, social, logistical, one-off event | Leave in place — not wiki content |

A fragment qualifies as **concept** only if it expresses something that would still be useful to know a month from now.

### Phase 2 — Structure

Append to the journal entry without modifying existing content:

```markdown
## Ideas to promote

- [Concept name] — [one sentence on what the Knowledge/ page should capture]

## Open questions

- [Question as a complete sentence ending in ?]
```

Rules:
- Each concept entry is `name — rationale`, not a raw quote or fragment
- Each question is a complete sentence ending with `?`
- If zero concepts were found, write: `## Ideas to promote\n\n(none — content is tasks or transient)`
- If the entry is fewer than three non-trivial lines, flag it as too sparse and skip

### Phase 3 — Annotate frontmatter

Add a processing marker so wiki-synthesizer knows this entry has reliable structure:

```yaml
processed: YYYY-MM-DD
```

If no frontmatter exists yet, create a minimal block:

```yaml
---
type: journal
updated: YYYY-MM-DD
processed: YYYY-MM-DD
---
```

## Output discipline

- Report phase by phase: "Phase 1: found 3 concepts, 2 questions, 1 task in 2026-07-06.md."
- List every candidate before writing — do not write silently.
- If a fragment could be either a concept or a question, surface both readings and ask.
- If the journal is mostly tasks with no durable knowledge, say so explicitly rather than manufacturing thin candidates.
- After processing a range, summarize: total entries processed, total concepts extracted, total questions flagged.
