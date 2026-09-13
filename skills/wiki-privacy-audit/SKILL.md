---
name: wiki-privacy-audit
description: Audits an Obsidian vault's notes for Direct PII and Secrets accidentally captured while journaling or researching — reuses privacy-linter's deterministic scan_diff.py rather than reimplementing detection. Reports findings and proposes redaction/removal with confirmation; never edits vault content automatically. Use when the user wants to check their notes/journal for leaked personal information or credentials, asks "did I ever paste something sensitive into my vault," or wants a periodic privacy health check on their knowledge base. Triggers on "audit my vault for PII," "check my notes for secrets," "did I leak anything into my vault," "privacy audit my wiki," and the command /privacy-audit. Requires the obsidian-vault MCP server connected (to resolve the vault's local filesystem path) and privacy-linter's scan_diff.py (a sibling skill, invoked directly as a subprocess, not reimplemented). Does not cover attached-image metadata or vault git history — see "What's NOT covered here."
---

# Wiki Privacy Audit

A personal knowledge vault is exactly the kind of place a stray SSN, phone number, or
API key ends up — pasted into a journal entry, a meeting note, a research capture —
without the deliberate "am I about to commit this" moment a git hook gives you. This
skill runs the same detection `privacy-linter` already uses for commits against every
note in the vault instead.

## Why this reuses privacy-linter instead of its own detection

Two schemes for the same problem drift apart the moment one gets updated and the other
doesn't — the exact failure this repo's own `qa_gate_history.py` and `deid-reid-
harness`'s `score_inference.py` docstrings warn about for their own cross-skill calls.
`scripts/check_vault_privacy.py` shells out to `privacy-linter/scripts/scan_diff.py
--file <note> --json` per note and aggregates the results — `privacy-linter` stays the
single source of truth for what counts as PII or a secret, and a future improvement
there (a new secret pattern, a fixed false positive) applies here automatically, with
no separate patch needed.

## Prerequisites

The `obsidian-vault` MCP server connected (same setup as `wiki-operator`/`wiki-
librarian`) — needed to resolve the vault's local filesystem path, since
`check_vault_privacy.py` operates on the vault as a plain directory of `.md` files, not
through the MCP tool interface. If the vault's local path is already known (e.g. from
`bin/setup-vault.sh`'s output), the MCP server isn't strictly required for a one-off run
of the script itself — only for the live skill's ability to discover that path and to
write the audit-log entry back into the vault afterward.

## /privacy-audit

1. **Resolve the vault's local path**, then run the check directly — this is fully
   mechanical (the same regex scanners `privacy-linter` uses for a git diff), so unlike
   `wiki-librarian`'s near-duplicate/contradiction checks, there's no judgment step that
   needs Claude reasoning over MCP-fetched content one note at a time:
   ```bash
   python scripts/check_vault_privacy.py /path/to/vault
   python scripts/check_vault_privacy.py /path/to/vault --json
   ```
   Walks every `.md` note (skipping `.obsidian/`, `.trash/`, `.git/`, matching `wiki-
   librarian`'s own `check_vault.py` file-discovery convention) and reports findings in
   the exact `[severity] [class] [finding] reason (location)` format `privacy-linter`
   itself uses — not a new report format to learn. `location` is the vault-relative
   path (`Journal/Daily/2026-09-01.md:3`), never an absolute filesystem path.
2. **Report every finding, grouped by note**, severity-first (matching `privacy-
   linter`'s own ordering).
3. **Propose a fix per finding and wait for confirmation** — this skill never edits
   vault content automatically, the same discipline `wiki-librarian` already applies to
   structural fixes:
   - **False positive** (a fictional phone number in a story note, a test SSN in a
     worked example) — offer to add an inline `privacy-linter: ignore` marker on that
     line. **`.privacy-linter-ignore` does NOT work here** — see the limitation below —
     so the inline marker is the only suppression mechanism this skill actually
     supports today.
   - **Real leak** — offer to redact the specific span or remove the affected
     line/section via `wiki-operator`'s `/update`. Never touch the note directly from
     this skill; hand off to `wiki-operator` for the actual edit.
4. **Leave a trail** — same convention as `wiki-librarian`'s Principle 5: append an
   audit summary (notes scanned, findings count, severity mix, what was fixed vs.
   deferred) to today's journal after the run.
5. **Gate it if this is being run unattended** (e.g. a scheduled check rather than an
   interactive session):
   ```bash
   python scripts/check_vault_privacy.py /path/to/vault --block-on high
   ```
   Same `--block-on`/severity-threshold convention as `scan_diff.py` itself.

## A real limitation, not a silent one: `.privacy-linter-ignore` doesn't apply here

`privacy-linter`'s `.privacy-linter-ignore` file (glob patterns for whole paths) is only
consulted by `scan_staged()` — the staged-git-diff code path. `scan_diff.py --file`,
which is what `check_vault_privacy.py` calls per note, never reads it (confirmed by a
real test, not assumed: `test_respects_privacy_linter_ignore_file` in
`test_check_vault_privacy.py` seeds a matching ignore rule and shows the finding still
fires). Concretely: if the vault has a `Templates/` folder full of fictional PII in
worked examples, there is currently no way to blanket-suppress that whole folder —
every occurrence needs its own inline `privacy-linter: ignore` marker. A path-level
suppression mechanism for `check_vault_privacy.py` specifically (its own ignore file, or
reading `.privacy-linter-ignore` itself) is a reasonable follow-up, not built here.

## What's NOT covered here

- **Metadata (EXIF GPS, embedded document properties) in attached images/files.**
  `check_vault_privacy.py` only walks `.md` notes — a photo pasted into a journal entry
  with GPS EXIF intact is not caught by this pass. `privacy-linter`'s own
  `--strip-metadata`/detection already has the mechanism this would reuse; wiring it
  into a vault-wide walk over image attachments is a natural follow-up, not attempted
  here.
- **The vault's own git history**, if it happens to be version-controlled with git
  (some vaults are, for backup/sync). `privacy-linter --scan-history` exists for exactly
  this and could in principle be pointed at the vault directly, but isn't wired into
  this skill's default flow yet.
- **Inference cues and stylometric fingerprinting.** Same reason `privacy-linter` itself
  doesn't build them — genuinely need judgment/a model, which conflicts with running
  locally. Worth naming explicitly here rather than glossing over: a personal knowledge
  vault, with years of journal entries in one person's own voice, is arguably exactly
  the kind of corpus a stylometric or inference-based re-identification attack would
  target. Not addressed by this skill; see `privacy-linter/references/leak-taxonomy.md`
  for why building it today would mean either a local model (real setup work, not done)
  or an external LLM API (the exact disclosure this project exists to avoid).

## Pairing

- **privacy-linter** — owns the actual detection logic entirely; this skill only
  invokes `scan_diff.py` against vault notes instead of a git diff, never reimplements
  a pattern.
- **wiki-operator** — used for the actual fix (`/update`) once a real leak is
  confirmed; this skill never edits note content itself.
- **wiki-librarian** — same "report findings, propose fixes, confirm before touching
  anything" philosophy and "leave a trail" journal convention, run as its own focused
  audit rather than folded into `wiki-librarian`'s checks, so `privacy-linter` stays the
  single source of truth for detection and `wiki-librarian`'s own scope stays purely
  structural (as it's already documented).

## Files

| Path | What it is |
|---|---|
| `scripts/check_vault_privacy.py` | Walks a vault directory, invokes `privacy-linter`'s `scan_diff.py` per note, aggregates findings, CLI gate |
| `scripts/test_check_vault_privacy.py` | Unit + integration tests (temp-directory fixtures, no MCP or real vault needed) |
