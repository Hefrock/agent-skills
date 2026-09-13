---
name: privacy-linter
description: Deterministic pre-disclosure privacy scanner for git-staged changes — flags Direct PII (email, phone, SSN, Luhn-valid credit card, IP address), Secrets (AWS/GitHub/Slack/Stripe/Google/Anthropic tokens, private key blocks, hardcoded-credential assignments), and Metadata risk (image/document file types that commonly carry EXIF/embedded properties, with a confirmed EXIF GPS check when Pillow is available) before a commit lands. `--scan-history` walks the full commit history for PII/secrets ever introduced, even if later removed from HEAD. Can also strip EXIF metadata from a JPEG/TIFF outright with `--strip-metadata`. Runs entirely locally with no model or network call — pattern-matching only. Use when the user wants to check a commit, diff, or file for leaked PII or secrets before it's committed or shared, asks "will this diff leak anything," "did I commit an API key," "did I already leak something in an old commit," wants a pre-commit privacy check, wants to scan a specific file or pasted text for personal information, or wants to strip GPS/EXIF data from a photo before sharing it. Triggers on "check this diff for PII," "will committing this leak anything," "scan this file for personal info," "did I leak a secret/API key," "check git history for leaked secrets," "strip metadata from this photo," "set up a privacy pre-commit hook," and the script `scan_diff.py`. Does not cover inference-cue or stylometric leaks, or text redaction — see references/leak-taxonomy.md for why those are deliberately out of scope for this version.
---

# Privacy Linter

Catches leakage at the one cheap intervention point — the moment of creation — rather
than after content is already committed or posted. Advisory by default: a low
false-positive rate matters more than catching everything, because an ignored linter is
useless.

## Why this is deterministic-only, not model-based

The design this implements (`Projects/Privacy OS - Pre-Disclosure Privacy Linter` in the
source vault) requires local-only analysis: **sending content to an external LLM API for
leak detection is itself a potential disclosure.** Of the four leak classes in that
design — Direct PII, Metadata, Inference cues, Stylometric fingerprint — only the first
two are mechanically detectable without a model. This version implements those two, end
to end, with real tests, the same order `deid-reid-harness` used for its own tracks
(ship the model-independent slice first) — plus **Secrets/credentials**, a class added
afterward that isn't part of the source design's original four but is mechanically
detectable the exact same way (see `references/leak-taxonomy.md` for why it's called
out as an addition, not silently folded into "Direct PII"). Inference cues and
stylometric fingerprinting are documented, not built — see `references/leak-taxonomy.md`
— because building them today would mean either standing up a local-model pipeline
(real setup work, not yet done) or using an external LLM for exactly the analysis this
project exists to keep local. Don't silently reach for an external model to "complete"
those two classes. Text redaction for Direct PII/Secrets is deliberately not built for
a related reason — auto-rewriting is a judgment call (did it redact too much/too
little), unlike `--strip-metadata`'s EXIF removal, which has no partial-credit version.

## How this works

1. **Scan staged changes** (the default, no arguments): reads `git diff --cached -U0`
   for added lines and `git diff --cached --name-only` for the staged file list.
   - **Direct PII** — regex-matched against added lines only (not removed or context
     lines): email, phone, SSN (strict `###-##-####` form), credit card (13-19 digit
     run, Luhn-validated to cut false positives), IPv4 (octet-range validated).
   - **Secrets** — same added-lines-only scope: AWS/GitHub/Slack/Stripe/Google/
     Anthropic token prefixes, private key blocks, and a conservative catch-all for a
     secret in an unrecognized format (a *quoted* string literal assigned to a
     `api_key`/`secret`/`token`/`password`-shaped name — bare/unquoted values are a
     documented gap, too false-positive-prone to catch reliably).
   - **Metadata** — any staged file whose extension commonly carries embedded metadata
     (jpg/png/heic/tiff/bmp/gif/pdf/docx/xlsx/pptx) is flagged as an advisory. If
     Pillow is installed, JPEG/TIFF files additionally get a real EXIF GPS check —
     a confirmed `high` finding if GPS data is actually present, not just a type-based
     guess.
2. **Or scan something specific** instead of the staged diff:
   ```bash
   python scripts/scan_diff.py --file path/to/thing.txt
   python scripts/scan_diff.py --commit-msg .git/COMMIT_EDITMSG
   echo "some text" | python scripts/scan_diff.py --text -
   ```
3. **Report findings** in the format the source project specified: `[severity] [class]
   [finding] reason (location)`. Add `--json` for machine-readable output.
4. **Suppress deliberately**, two ways — both are for real exceptions the user has
   actually reviewed, not a way to make warnings go away:
   - `.privacy-linter-ignore` at the repo root — one glob pattern per line (like
     `.gitignore`), for whole files/paths (test fixtures, sample data).
   - An inline `privacy-linter: ignore` marker on the specific line.
5. **Gate or advise.** Default behavior always exits 0 — advisory only, matching the
   source design's "advisory, not blocking by default." Add a gate when the caller
   wants one:
   ```bash
   python scripts/scan_diff.py --block-on high   # exit 1 if any 'high' finding exists
   ```
   Same `--block-on`-as-CI-gate pattern `agent-eval`'s `score_eval.py` already uses —
   reused here rather than inventing a new convention.
6. **Strip EXIF metadata outright**, instead of just flagging it (JPEG/TIFF only,
   requires Pillow):
   ```bash
   python scripts/scan_diff.py --strip-metadata photo.jpg               # -> photo.stripped.jpg
   python scripts/scan_diff.py --strip-metadata photo.jpg --out clean.jpg
   python scripts/scan_diff.py --strip-metadata photo.jpg --in-place    # overwrites photo.jpg
   ```
   Writes a separate copy by default — never overwrites the original unless `--in-place`
   is passed explicitly — and re-reads the *output* file's EXIF afterward to confirm the
   strip actually worked before reporting success. Refuses to silently clobber an
   existing `.stripped` file from a previous run; pass `--out` for a different path.
7. **Check whether something was already leaked before this tool existed**, not just
   what's staged right now:
   ```bash
   python scripts/scan_diff.py --scan-history                  # entire branch history
   python scripts/scan_diff.py --scan-history --max-commits 50 # just the N most recent commits
   ```
   Walks every non-merge commit on the current branch (oldest first, so findings print
   in the order a leak was actually introduced), checking each commit's own added lines
   for Direct PII/Secrets — the same regex scanners as everything above, just pointed at
   history instead of the staged diff. This is the one check the staged/working-tree
   view structurally cannot do: a secret committed and later removed from `HEAD` is
   still sitting in an old commit unless history itself was rewritten, and `--scan-
   history` is what actually looks there instead of assuming a deleted line is a solved
   problem. Each finding's location is `<short-hash> <path>:<line>` — `git show
   <short-hash>` to see the exact commit. Does not cover Metadata (would need reading
   each historical blob's file content, not just its diff — a heavier operation left
   for a future pass) or merge commits (their content was already introduced by a
   non-merge ancestor commit, so nothing is missed by skipping them).

## Installing as a git pre-commit hook

```bash
./scripts/install_hook.sh /path/to/your/repo
```
Writes a `pre-commit` hook that runs `scan_diff.py` with no gate (advisory-only,
matching the design's default) — edit the installed hook to add `--block-on high` if
blocking is wanted for that repo. See the script for what it writes; it never
overwrites an existing hook without confirmation.

## What's NOT built here

- **MCP tool surface.** The source design names this as a second surface alongside the
  git hook. Not implemented in this version — the git-hook surface covers the "highest
  daily value... moment of creation" case the project itself prioritized. A fast
  follow, not a silent scope cut.
- **Inference cues and stylometric fingerprinting.** Genuinely need judgment, not
  pattern-matching. See `references/leak-taxonomy.md` for what "genuinely need a
  model" means here and what the local-model path would require.
- **Free-text name detection.** Regex can't reliably distinguish a name from an
  ordinary capitalized word without NER/a model — deliberately not attempted rather
  than shipped with a high false-positive rate.
- **Text redaction / auto-fix for Direct PII or Secrets.** Detection only reports;
  nothing here rewrites a file or diff. Auto-redacting is a judgment call (did it
  redact too much, too little, or corrupt surrounding content) that this tool's own
  "low false-positive rate matters more than coverage" philosophy argues against
  automating — a human should apply the actual fix. `--strip-metadata` is the one
  exception, because EXIF removal has no such judgment call to get wrong.
- **Metadata stripping for non-EXIF formats** (PNG, HEIC, PDF, DOCX, XLSX, PPTX).
  `--strip-metadata` only covers JPEG/TIFF via Pillow; the others need a different
  metadata-writing library each — detection still flags all of them, just not
  remediation.
- **Metadata in `--scan-history`, and merge commits in `--scan-history`.** History
  scanning covers Direct PII/Secrets only — a historical metadata check would need
  each commit's actual file content, not just its diff, real scope beyond this pass.
  Merge commits are skipped (their content already arrived via a scanned non-merge
  ancestor). See `references/leak-taxonomy.md`.

## Output discipline

- Never claim a `pass`/no-findings result means "definitely clean" — say what was and
  wasn't checked (e.g., no findings from pattern-matching doesn't mean no PII; it means
  no PII shaped like the patterns this version knows).
- Report the Pillow-unavailable case explicitly when it affects a result (a flagged
  JPEG got the weaker file-type heuristic, not the stronger EXIF check) — don't let a
  degraded check look identical to a full one.
- Findings are advisory by default for a reason (false-positive tolerance matters more
  than coverage) — don't recommend `--block-on` as a default without the user asking
  for blocking behavior specifically.

## Files

| Path | What it is |
|---|---|
| `scripts/scan_diff.py` | The scanner — PII/secret regex + metadata heuristics, EXIF stripping, commit-history scanning, git integration, suppression, CLI gate |
| `scripts/test_scan_diff.py` | Unit + CLI + real-temp-git-repo test suite (stdlib unittest) |
| `scripts/install_hook.sh` | Installs `scan_diff.py` as a repo's `pre-commit` hook |
| `references/leak-taxonomy.md` | Every leak class (the source design's four, plus Secrets), severity rubric and rationale, what's built vs. deferred and why |
| `examples/` | A worked example: a synthetic diff with seeded PII, and the scan output it produces |
