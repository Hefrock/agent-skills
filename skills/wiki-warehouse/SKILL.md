---
name: wiki-warehouse
description: Ingests raw research documents (PDFs, ebooks, scans, articles) into a separate private "cold storage" GitHub repo, then writes a lean pointer note into the Obsidian vault — keeping originals and full extracted text out of the vault so it stays fast and git-diffable. Use when the user wants to file, archive, warehouse, or "add a document/paper/PDF to my knowledge base," or to keep large source files outside the vault. Triggers on "warehouse this," "ingest this document," "add this PDF to cold storage," "file this paper," and the commands /ingest and /warehouse-audit. Requires the obsidian-vault MCP server connected and the knowledge-warehouse repo cloned locally. Pairs with wiki-synthesizer (which compiles the resulting Source note into concepts) and wiki-librarian (structural audits).
---

# Wiki Warehouse

The vault is the **index**; a separate private GitHub repo (`knowledge-warehouse`) is the
**warehouse**. Originals and full extracted text live in the warehouse. The vault gets
only a lean **Source note** — summary, key excerpts, and a content-hash pointer — so it
stays fast and its git history stays small. The two are joined by a `doc_id` (a sha256
content hash), never a path, so warehouse files can be reorganized without breaking the
vault.

This skill owns the intake pathway. It does **not** replace `wiki-synthesizer` — the
synthesizer still compiles the resulting Source note into `Knowledge/` concepts. Think of
it as: **warehouse ingests the artifact → synthesizer distills its meaning.**

## Prerequisites

1. The `obsidian-vault` MCP server connected (same as wiki-operator). Verify with `/mcp`.
2. The `knowledge-warehouse` repo cloned locally, with push access. The intake tool is
   `bin/intake.py` in that repo; the vault side is this skill.
3. For born-digital PDFs (the common case), one extractor available to `intake.py`:
   `pip install pymupdf` is enough. Scanned documents additionally need an OCR tool
   (`apt install ocrmypdf`) — `intake.py` detects and reports this per document.

If the MCP server or the warehouse clone is unavailable, stop and tell the user — do not
simulate ingestion or write a Source note with a pointer to a file that wasn't stored.

## Principles

1. **The binary never enters the vault.** Originals and full text live only in the
   warehouse. The vault holds a summary + excerpts + pointer. If you catch yourself about
   to paste full document text into a note, stop.
2. **Content hash is the identity.** The `doc_id` (sha256) is the join key. Never key the
   vault note to a file path — paths change, hashes don't.
3. **Ingest is idempotent.** `intake.py` dedupes by content hash. Re-ingesting the same
   file is a no-op that reports the existing location; never create a second Source note
   for a doc_id that already has one (search the vault first).
4. **Distill, don't dump.** The Source note's summary and excerpts are curated — a few key
   quotes and a paragraph, not the first N pages. The full text is one click away in the
   warehouse for when it's actually needed.
5. **Confirm before committing to the warehouse.** Pushing to the private warehouse is an
   external, durable action — show what will be stored and get confirmation before the
   push, unless the user said to just do it.

## /ingest [file] [title]

Store a raw document in the warehouse and index it in the vault.

1. **Locate the file** the user provided (a path, or a file they attached). Confirm it
   exists.
2. **Run the warehouse intake tool** from the warehouse repo:
   ```bash
   python bin/intake.py <file> --title "<title>"
   ```
   This hashes the file, dedupes, copies the original into `raw/<year>/`, extracts text
   into `text/<year>/`, updates `manifest.json`, and prints a **frontmatter block** plus a
   short excerpt. Capture that output — the `doc_id`, paths, `extraction_method`, and
   excerpt are what the vault note needs.
   - If intake reports **"already ingested,"** search the vault for a Source note carrying
     that `doc_id`. If one exists, stop and point the user to it. If not (warehoused but
     never indexed), continue from step 4 using the existing paths.
   - If intake reports **little/no text (a scan with no OCR tool)**, tell the user: the
     original is safely stored, but there's no searchable text yet. Offer to proceed with
     a metadata-only note now, or pause until they install `ocrmypdf` and re-run intake to
     backfill the text.
3. **Commit + push the warehouse.** Confirm with the user, then commit the new `raw/`,
   `text/`, and `manifest.json` (or run intake with `--commit` and push). The original must
   be safely in the remote before the vault points at it.
4. **Read the extracted text** from the warehouse's `text/<year>/…txt` to write the note
   from the actual content (not just the excerpt) — but keep only a short summary + a few
   key excerpts in the note.
5. **Search the vault** (`search_notes`) for an existing page on this topic before
   creating anything — one canonical Source note per document.
6. **Write the Source note** to `Sources/<subfolder>/<title>.md` via the MCP `write_note`,
   using the warehouse frontmatter contract (see `references/warehouse-schema.md`):
   ```yaml
   type: source
   status: draft
   confidence: medium
   updated: <today>
   warehouse_repo: Hefrock/knowledge-warehouse
   doc_id: sha256:<hash>
   warehouse_path: raw/<year>/<slug>-<shortid>.<ext>
   text_path: text/<year>/<slug>-<shortid>.txt
   extraction_method: <method>
   ```
   Body: a `## Summary` (a paragraph you distilled), `## Key excerpts` (a few verbatim
   quotes with rough locators), and `## Connections` linking to relevant `Knowledge/`
   pages — creating `status: draft` stubs for concepts that don't exist yet, exactly as
   the synthesizer does.
7. **Confirm** in one line what happened: what was warehoused (doc_id + path), and which
   vault note now points to it.

## /warehouse-audit

Reconcile the two sides. This runs in two halves — the warehouse-side integrity check is
a script; the vault-side pointer check uses MCP.

**Half 1 — warehouse integrity (run the tool).** From the warehouse repo:
```bash
python bin/audit.py --json
```
`audit.py` re-hashes every stored original against its `doc_id` (catching silent swaps or
bit-rot), confirms every manifest path resolves, and reports `corrupt`, `missing`,
`orphan`, and `drift`. Surface any **CORRUPT** finding prominently — it means a stored
original no longer matches its own hash, which is the one failure the whole content-hash
design exists to detect. Do not "fix" corruption automatically; report it and let the
user restore the file.

**Half 2 — vault pointers (use MCP).** The script can't see the vault, so check the other
direction here:
1. Query the vault for all notes carrying a `doc_id` (`query_frontmatter`).
2. Load the warehouse `manifest.json` (or reuse the audit output).
3. For each vault `doc_id`: confirm it exists in the manifest, and that the note's
   `warehouse_path` / `text_path` match the manifest's current paths (the manifest is the
   source of truth — if they drifted, the note is stale, not the manifest).
4. Report three buckets: **dangling** (doc_id not in manifest — original may have been
   removed), **drifted** (paths in the note no longer match the manifest — offer to patch
   the note's frontmatter to the manifest's current paths), and **orphaned-in-warehouse**
   (manifest docs with no vault note; `audit.py`'s `orphan` list also flags files on disk
   that aren't even in the manifest — offer to index either via a metadata `/ingest`).
5. Propose fixes; apply only with confirmation (drifted-path patches are low-risk;
   anything involving deletion is not).

## What belongs where

| | Warehouse (private repo) | Vault (Obsidian) |
|---|---|---|
| Original file (PDF/scan/…) | ✅ | ❌ never |
| Full extracted text | ✅ | ❌ never |
| Summary + key excerpts | ❌ | ✅ |
| Concept links, curation | ❌ | ✅ |
| `doc_id` pointer | (manifest) | ✅ (frontmatter) |

## Pairing

- **wiki-synthesizer** — after `/ingest`, run `/synthesize sources` to promote the new
  Source note's ideas into `Knowledge/` concept pages.
- **wiki-librarian** — its structural audit plus this skill's `/warehouse-audit` together
  cover both intra-vault links and vault→warehouse pointers.

## Reference files

- `references/warehouse-schema.md` — the vault↔warehouse join contract: the Source-note
  frontmatter fields, the `doc_id` semantics, and how pointer integrity is checked. Read
  before changing the frontmatter shape or the audit logic.
