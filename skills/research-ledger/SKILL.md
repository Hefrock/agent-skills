---
name: research-ledger
description: >
  Captures a deep-research run's full claim ledger — every claim extracted, its
  verdict, and whatever vote/reasoning detail the run's own output exposes — as a
  structured JSON artifact, before that detail is lost to synthesis or context
  compaction. Hands the ledger to wiki-warehouse for durable, content-hash-addressed
  storage, so a research run's rejected claims and per-source detail survive even
  though only the confirmed subset gets promoted into Knowledge/ pages. Use this
  immediately after any deep-research skill run completes, before its findings are
  synthesized into the vault. Triggers on "save the claims ledger," "archive this
  research run," "warehouse the research," and the command /research-ledger.
  Requires the obsidian-vault MCP server and wiki-warehouse's prerequisites (the
  knowledge-warehouse repo cloned locally).
---

# Research Ledger

`deep-research` runs discard their working state once a run ends — the fetched
sources, the full claim list, and the per-claim verification votes exist only for the
lifetime of that run. Only what a human (or a synthesis step) chooses to promote
survives. This skill exists to catch what would otherwise be **thrown away**: it turns
the run's own output into a durable, warehoused ledger, so a rejected claim or an
unpromoted source can be revisited later instead of re-derived from scratch.

This skill does **not** replace `wiki-synthesizer` — the confirmed-claims synthesis
into `Knowledge/` concept pages still happens exactly as before. Think of it as a
step that runs *before* that: **research run → ledger (this skill) → synthesis
(wiki-synthesizer) → concept pages**.

## Hard limitation — read this before using the skill

`deep-research` is a hosted skill; its internal `Workflow` script and cache are not
inspectable or editable from here. This skill cannot reach into that run's internal
state after the fact — it can only capture what the run's **own final output**
happens to expose (source list, claims, verdicts, sometimes vote counts or
reasoning). If the run's output only reports confirmed claims with no detail on
what was rejected, the ledger will be equally thin — that is a ceiling on what's
recoverable, not a bug in this skill. Never pad the ledger with invented vote
reasoning or claim text to make it look more complete than the source material
actually was; an honest `granularity_note` is better than a fabricated one.

Corollary: **timing matters more than anything else here.** Run this immediately
after `deep-research` returns, in the same turn, before summarizing the result for
the user or handing it to `wiki-synthesizer`. Once that turn ends, whatever detail
wasn't captured is gone the same way yesterday's run's raw claims were.

## /research-ledger

1. **Capture immediately.** As soon as `deep-research` finishes, before doing
   anything else with its output, extract from its final text:
   - Run metadata: the research question/topic, date, and any ops stats it reports
     (source count, claim count, agent/token/time figures).
   - Every source it names: title, URL, and fetch/read status if stated.
   - Every claim it surfaces: text, verdict (confirmed / rejected / split /
     unconfirmed), which source(s) back it, and vote detail *only if the output
     actually states it* — otherwise record `null` and say so in
     `granularity_note` (see `references/ledger-schema.md` for the exact shape).
2. **Write the ledger file** to the scratchpad directory as
   `research-ledger-<topic-slug>-<YYYY-MM-DD>.json`, matching the schema in
   `references/ledger-schema.md`.
3. **Warehouse it.** Hand the file to `wiki-warehouse`'s `/ingest` flow (same
   `intake.py` pathway used for PDFs) so it gets a content-hash `doc_id` and lives
   in `knowledge-warehouse` alongside the sources it references. JSON ledgers need
   `intake.py`'s plaintext-extraction path to index as searchable text — confirm the
   warehouse repo's `bin/intake.py` handles `.json` before relying on this (see
   `references/ledger-schema.md`'s note on this); if it doesn't yet, that's a
   one-line fix to `extract()`'s dispatch, not a reason to skip warehousing.
4. **Point the vault at it.** Write a thin Source note under `Sources/Research/`
   (create the folder if needed) carrying the warehouse frontmatter
   (`doc_id`/`warehouse_path`/`text_path`) plus a one-paragraph summary of what the
   run covered and — critically — what it could **not** confirm. This is the note
   `wiki-synthesizer` and future research passes can search against to see "has this
   already been researched, and what did it fail to confirm" before re-running.
5. **Then continue as normal** — `wiki-synthesizer` promotes the confirmed claims
   into `Knowledge/` concept pages, same as always. The ledger doesn't change that
   step; it just means the discarded 80%+ of the run isn't *silently* discarded.

## What belongs where

| | Ledger (warehouse) | Vault Source note |
|---|---|---|
| Full claim list, incl. rejected | ✅ | ❌ |
| Per-claim vote detail (if available) | ✅ | ❌ |
| Run ops stats (cost/tokens/time) | ✅ | optional, one line |
| Summary of what was/wasn't confirmed | ❌ | ✅ |
| `doc_id` pointer | (manifest) | ✅ (frontmatter) |

## Pairing

- **wiki-warehouse** — owns the actual storage mechanics (`intake.py`, `doc_id`,
  `/warehouse-audit`). This skill produces the document that gets ingested; it
  doesn't duplicate the ingestion logic.
- **wiki-synthesizer** — still the only path from a Source note into `Knowledge/`
  concept pages. This skill runs strictly before it in the pipeline.
- **deep-research** — the source of the run being ledgered. This skill has no
  hook into it and cannot change what it chooses to report; it only preserves what
  is reported.

## Reference files

- `references/ledger-schema.md` — the JSON shape for a claims ledger, and the
  `intake.py` extraction-support note referenced in step 3.
