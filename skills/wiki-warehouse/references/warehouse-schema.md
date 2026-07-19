# Vault ↔ Warehouse join contract

The vault and the `knowledge-warehouse` repo are two halves of one system, joined by a
**content hash**. This file is the contract between them — freeze it before changing the
Source-note frontmatter or the audit logic, because both sides depend on it.

## The identity: `doc_id`

Every warehoused document is identified by the sha256 of its **content**, written
`sha256:<64 hex chars>`. This is the join key. It is deliberately **not** a path:
- Paths change when the warehouse is reorganized; hashes don't.
- The same file ingested twice produces the same `doc_id` — that's how `intake.py` dedupes
  and how the vault avoids duplicate Source notes.

The warehouse `manifest.json` is keyed by the bare hex (no `sha256:` prefix); the vault
frontmatter stores the prefixed form. Strip/add the prefix when crossing between them.

## Source-note frontmatter (vault side)

A warehoused document's Obsidian Source note carries the standard source fields plus the
warehouse pointer:

```yaml
type: source
status: draft | mature | stale
confidence: high | medium | low
updated: YYYY-MM-DD
warehouse_repo: Hefrock/knowledge-warehouse
doc_id: sha256:9f8a3b21…              # the join key
warehouse_path: raw/2026/slug-shortid.pdf    # convenience; manifest is authoritative
text_path: text/2026/slug-shortid.txt
extraction_method: text-layer:pymupdf | ocr:ocrmypdf | plaintext | html-stripped | …
```

`warehouse_path` and `text_path` are **convenience copies** of what the manifest holds at
ingest time. If they ever disagree with the manifest, the **manifest wins** — the paths in
the note are stale and should be patched to match (this is exactly what `/warehouse-audit`
detects and offers to fix).

The note **body** holds a distilled `## Summary`, a few `## Key excerpts` (verbatim, with
rough locators), and `## Connections` to `Knowledge/` concepts. It never holds the full
extracted text — that lives only in the warehouse's `text_path`.

## Manifest entry (warehouse side)

```json
{
  "<sha256 hex>": {
    "title": "…",
    "raw_path": "raw/2026/slug-shortid.pdf",
    "text_path": "text/2026/slug-shortid.txt",
    "ext": ".pdf",
    "bytes": 12345,
    "char_count": 5000,
    "extraction_method": "text-layer:pymupdf",
    "ingested": "2026-07-19"
  }
}
```

## Pointer integrity (`/warehouse-audit`)

The audit reconciles the two sides. For each vault note with a `doc_id`:

| Condition | Meaning | Fix |
|-----------|---------|-----|
| `doc_id` present in manifest, paths match | healthy | none |
| `doc_id` present, paths differ | **drifted** — warehouse was reorganized | patch the note's `warehouse_path`/`text_path` to the manifest's values (low-risk, confirm) |
| `doc_id` absent from manifest | **dangling** — the original may have been removed from the warehouse | flag; do NOT delete the note without confirmation — the note's summary may still be the only record |
| manifest doc with no vault note | **orphaned-in-warehouse** — stored but never indexed | offer to create a metadata Source note from the existing `raw_path`/`text_path` |

This is the vault→warehouse analogue of `wiki-librarian`'s broken-`[[wikilink]]` check:
same "does the pointer still resolve?" question, across the repo boundary instead of
within the vault.

## Why a content hash, not a URL or path

- **Reorganization-proof:** move or rename warehouse files freely; only the manifest paths
  change, and the audit repairs the notes.
- **Dedup:** identical content ingested from two places collapses to one `doc_id`.
- **Integrity:** the hash also verifies the stored file hasn't been corrupted or silently
  swapped — re-hashing `raw_path` should reproduce the `doc_id`.
