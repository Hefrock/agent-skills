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
`${CLAUDE_PLUGIN_ROOT}/scripts/check_vault_privacy.py` shells out to `privacy-linter/scripts/scan_diff.py
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
   python ${CLAUDE_PLUGIN_ROOT}/scripts/check_vault_privacy.py /path/to/vault
   python ${CLAUDE_PLUGIN_ROOT}/scripts/check_vault_privacy.py /path/to/vault --json
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
   python ${CLAUDE_PLUGIN_ROOT}/scripts/check_vault_privacy.py /path/to/vault --block-on high
   ```
   Same `--block-on`/severity-threshold convention as `scan_diff.py` itself.

## Running unattended, on an actual schedule

Steps 1–2 above (scan and gate) need no MCP or Claude session at all — `${CLAUDE_PLUGIN_ROOT}/scripts/
cron_check.sh /path/to/vault [/path/to/log/file] [retention-days]` wraps
`check_vault_privacy.py --block-on high --json` for exactly this: a plain OS-level
scheduler invocation. It logs one compact JSON line per run (findings only — labels
and file:line locations, never the actual matched PII/secret text, so the log can't
become a second copy of whatever triggered a finding) and best-effort fires a
desktop notification (`notify-send` on Linux, `osascript` on macOS, falling back to
stderr if neither is present) when a `high`-severity finding exists. Always exits 0
regardless of findings — the log/notification carry the signal, not the scheduler's
own success/failure bookkeeping.

Every run also prunes log lines older than `retention-days` (default 90) via
`${CLAUDE_PLUGIN_ROOT}/scripts/prune_log.py`, automatically and with no confirmation needed — unlike
`broadcast`'s `prune_episodes.py`, this log holds zero PII/secret text, so it's
disposable telemetry, not something a human might want to keep. This runs
unattended on a schedule, so there's nobody around to clean it up manually; without
this the log would grow forever.

This is a **detector, not a fixer** — steps 3–4 above (propose a fix, leave a trail
in the journal) genuinely need a live Claude session with the `obsidian-vault` MCP
server connected, which nothing unattended has access to. The intended flow: the
scheduled check notices something → you see the notification (or check the log) →
you run `/privacy-audit` interactively to actually deal with it.

Three ways to actually schedule it, pick per machine:

- **systemd user timer** (any systemd-based Linux, including NixOS) — the idiomatic
  choice on most non-macOS boxes. On NixOS with home-manager specifically, see
  `${CLAUDE_PLUGIN_ROOT}/references/nixos-home-manager-handoff.md` — a self-contained handoff written for
  a *separate* Claude Code session running in your NixOS config repo, since that
  session has no access to this one or this repo.
- **launchd** (macOS) — a `LaunchAgent` plist with a `StartCalendarInterval`, loaded
  via `launchctl load`.
- **cron** (portable fallback, any Unix) — a plain `crontab -e` entry. Note: a bare
  cron job has no `DISPLAY`/D-Bus session, so `notify-send`/`osascript` will silently
  fail to display anything there; the log file is the only reliable signal under
  plain cron.

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

## What's NOT built here

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
- **A verified cross-plugin install path to `privacy-linter`.** `check_vault_privacy.py`
  locates `scan_diff.py` by walking up from its own `__file__` to a sibling
  `privacy-linter/scripts/` directory (`os.path.join(HERE, "..", "..", "privacy-linter",
  "scripts", "scan_diff.py")`) — correct in this repo's own layout, and correct if a
  marketplace is added from a local directory (Claude Code reads relative-path plugins
  in place, per the plugins/marketplace docs). **Not confirmed for the documented
  primary install path** (`/plugin install X@hefrock-agent-skills`, a GitHub-hosted
  marketplace): each plugin very likely installs to its own separate, isolated
  location with no relationship to its siblings, which would break this lookup
  entirely for anyone who installs `wiki-privacy-audit` and `privacy-linter` as two
  independent plugins rather than cloning this repo. Claude Code's plugin docs
  describe symlinks as the mechanism for sharing files across plugins in the same
  marketplace (dereferenced and copied into the referencing plugin's own install at
  install time) — a plausible fix, but its behavior for a GitHub-hosted marketplace
  specifically is not stated in the docs, only implied. Deliberately not built on that
  inference alone (premortem 2026-10-04): the real fix is only one file's worth of
  problem, and committing to an unverified mechanism risks spending effort on
  something that still doesn't work, discoverable only by an actual failed install far
  from this session. Revisit if a real marketplace install of both plugins is ever
  actually confirmed broken — a real failure is a better design input than an inferred
  one.

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
| `scripts/cron_check.sh` | Unattended wrapper for OS-level schedulers — logs + best-effort notifies, never fixes |
| `scripts/test_cron_check.py` | End-to-end tests: real temp vault, real subprocess, fake notifier binaries injected via `PATH` |
| `scripts/prune_log.py` | Retention for `cron_check.sh`'s own log — drops lines older than `retention-days`, automatic, no `--apply` gate |
| `scripts/test_prune_log.py` | Unit tests for the pure pruning function plus the in-place file rewrite |
| `references/nixos-home-manager-handoff.md` | Self-contained handoff for a separate Claude Code session managing NixOS/home-manager config, to actually install the systemd timer |
