---
name: privacy-linter
description: Deterministic pre-disclosure privacy scanner for git-staged changes — flags Direct PII (email, phone, SSN, Luhn-valid credit card, IP address) and Metadata risk (image/document file types that commonly carry EXIF/embedded properties, with a confirmed EXIF GPS check when Pillow is available) before a commit lands. Runs entirely locally with no model or network call — pattern-matching only. Use when the user wants to check a commit, diff, or file for leaked PII before it's committed or shared, asks "will this diff leak anything," wants a pre-commit privacy check, or wants to scan a specific file or pasted text for personal information. Triggers on "check this diff for PII," "will committing this leak anything," "scan this file for personal info," "set up a privacy pre-commit hook," and the script `scan_diff.py`. Does not cover inference-cue or stylometric leaks — see references/leak-taxonomy.md for why those are deliberately out of scope for this version.
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
two are mechanically detectable without a model. This version implements those two,
end to end, with real tests, the same order `deid-reid-harness` used for its own tracks
(ship the model-independent slice first). Inference cues and stylometric fingerprinting
are documented, not built — see `references/leak-taxonomy.md` — because building them
today would mean either standing up a local-model pipeline (real setup work, not yet
done) or using an external LLM for exactly the analysis this project exists to keep
local. Don't silently reach for an external model to "complete" those two classes.

## How this works

1. **Scan staged changes** (the default, no arguments): reads `git diff --cached -U0`
   for added lines and `git diff --cached --name-only` for the staged file list.
   - **Direct PII** — regex-matched against added lines only (not removed or context
     lines): email, phone, SSN (strict `###-##-####` form), credit card (13-19 digit
     run, Luhn-validated to cut false positives), IPv4 (octet-range validated).
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
| `scripts/scan_diff.py` | The scanner — PII regex + metadata heuristics, git integration, suppression, CLI gate |
| `scripts/test_scan_diff.py` | Unit + CLI + real-temp-git-repo test suite (stdlib unittest) |
| `scripts/install_hook.sh` | Installs `scan_diff.py` as a repo's `pre-commit` hook |
| `references/leak-taxonomy.md` | The four leak classes, severity rubric and rationale, what's built vs. deferred and why |
| `examples/` | A worked example: a synthetic diff with seeded PII, and the scan output it produces |
