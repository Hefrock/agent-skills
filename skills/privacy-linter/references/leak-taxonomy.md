# Leak Taxonomy

The source design (`Projects/Privacy OS - Pre-Disclosure Privacy Linter`) names four
leak classes. This version builds two of them (Direct PII, Metadata), plus one class
added afterward that isn't part of the original four — **Secrets/credentials** — added
because it's the single most common real "oops I committed X" for a git-hook tool
specifically, and mechanically detectable the same way Direct PII already is (pattern-
match added lines, no model needed). Called out explicitly here so it's clear this
isn't a fifth item from the source spec, just a natural extension of the same
detection mechanism.

## Built: Direct PII

Pattern-matched against added lines in a diff (or a file/stdin's full text). Each
pattern has a default severity — a documented judgment call, not a validated
measurement, the same honesty standard `wiki-governor`'s Law 10 threshold and
`deid-reid-harness`'s ZIP3-uniformity caveat use elsewhere in this repo.

| Pattern | Severity | Why | Known false positives/negatives |
|---|---|---|---|
| SSN (`###-##-####`) | high | Specific format, rarely legitimate in code/text | Undashed 9-digit sequences are NOT matched (false negative, by design — too broad otherwise) |
| Credit card (13-19 digits, Luhn-validated) | high | Luhn validation filters most non-card digit runs | Two adjacent number-like tokens separated by a single space/dash can theoretically bridge into one Luhn-valid-looking run; rare in practice, mitigate with `.privacy-linter-ignore` if it recurs in a specific file |
| Email | medium | Common and often legitimate in code (test fixtures, contact info) — advisory, not alarm | None significant; standard email regex |
| Phone | medium | Format is ambiguous (could be a fictional/example number) | Doesn't match unformatted 10-digit runs (no separators) — false negative, by design, to avoid matching arbitrary numeric IDs |
| IPv4 | low | Extremely common and usually benign in code/config; only sometimes privacy-relevant | Version strings shaped like `x.x.x.x` with valid octet ranges (0-255) will match — a real, known false-positive source |

**Not attempted:** free-text name detection. A regex can't distinguish "Jane Smith" from
any other two capitalized words without NER or a model — shipping it would mean either
a very high false-positive rate (flagging ordinary capitalized phrases) or a very high
false-negative rate (narrow enough to avoid that, narrow enough to miss most real
names). Neither is worth shipping as "detection."

## Built: Secrets / credentials

Not one of the source design's four classes — added because it's a distinct, common,
and high-severity leak category for exactly the surface this tool already scans (a
git-staged diff). Two tiers, same pattern as Direct PII:

| Pattern | Severity | Why |
|---|---|---|
| AWS access key ID (`AKIA`/`ASIA` prefix) | high | Prefix is only ever a real credential — no legitimate reason for this exact shape to appear otherwise |
| GitHub token (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` prefix) | high | Same — prefix + length is GitHub's own token format, not ambiguous |
| Slack token (`xox[baprs]-` prefix) | high | Same reasoning |
| Stripe live secret key (`sk_live_` prefix) | high | Same reasoning — deliberately excludes `sk_test_` (test-mode keys are meant to be shared in docs/examples) |
| Google API key (`AIza` prefix, 39 chars) | high | Same reasoning |
| Anthropic API key (`sk-ant-` prefix) | high | Same reasoning — this repo's own domain |
| Private key block (`-----BEGIN ... PRIVATE KEY-----`) | high | Unambiguous — this exact string only appears at the start of a real private key |
| Generic quoted secret assignment (`api_key`/`secret`/`token`/`password` + `=`/`:` + a quoted 8+ char value) | medium | The deliberately narrow catch-all for a secret in a format none of the above recognize — see below for why it's this narrow |

**Why the generic assignment pattern requires quotes.** An earlier draft also matched
bare/unquoted values (`.env`-style `API_KEY=abcdef123456`) — dropped before shipping
because it couldn't distinguish a real inline secret from a function call
(`token = get_token()`), an environment-variable reference, or any other short
right-hand-side expression, and would have violated this tool's own "low
false-positive rate matters more than coverage" rule from the first commit. A quoted
string literal is a much stronger signal that the value is meant to be a static
credential, not code. Documented as a known gap, not a silent one.

**Also excluded:** a placeholder denylist (`your_`, `example`, `changeme`, `<...>`,
etc.) suppresses the generic assignment pattern specifically — the prefixed-token
patterns above never need it, since a real AWS/GitHub/etc. prefix on a placeholder
string would be a strange thing for anyone to type by hand.

## Built: Commit-history scanning (`--scan-history`)

Direct PII and Secrets, both above, get a second surface: everything the staged-diff
scan already checks, `--scan-history` also checks against every non-merge commit's own
added lines, oldest first. This exists because the default scan structurally can't
answer "did I leak this before the tool existed" — a secret committed and later deleted
is gone from `HEAD` and the working tree, but still sitting in a reachable git object
unless history itself gets rewritten (`git filter-repo`/BFG, outside this tool's scope
entirely — this is a detector, not a history-rewriting tool).

Two explicit exclusions, not silent gaps:

- **Merge commits are skipped.** `git show <merge-commit>` produces a combined-diff
  format by default (no `+++`/`---`/`@@` unified-diff shape), and every merge commit's
  content already arrived via a non-merge ancestor commit that IS scanned directly — so
  skipping merges costs no real coverage, only the complexity of a second diff parser.
- **Metadata is not scanned in history mode.** Direct PII/Secrets work off each commit's
  diff text directly (cheap, already available from `git show`); a historical metadata
  check would need each commit's actual file content (`git show <hash>:<path>` per
  candidate file, per commit) — a materially heavier operation, and real scope beyond
  this pass. A GPS-tagged photo committed and later removed would not be caught by
  `--scan-history` today.

`.privacy-linter-ignore` is read once from the current working tree and applied
uniformly across all of history — not reconstructed per-commit from whatever that file
looked like at the time, since an ignore rule is a forward-looking policy decision, not
something that should vary by which commit happens to be under the microscope.

## Built: Metadata

Two tiers for detection, one for remediation:

1. **File-type heuristic** (`medium`) — any staged file with an extension that commonly
   carries embedded metadata (images: jpg/jpeg/png/heic/tiff/bmp/gif; documents:
   pdf/docx/xlsx/pptx) is flagged as "verify metadata is stripped," regardless of
   whether metadata is actually confirmed present. This is the only check possible
   without a metadata-reading library.
2. **Confirmed EXIF GPS** (`high`) — when Pillow is installed, JPEG/TIFF files get an
   actual `GPSInfo` EXIF tag read. A non-empty result is a *confirmed* finding, not a
   guess, and supersedes the generic heuristic for that file. Mirrors
   `wiki-warehouse`'s pattern of degrading gracefully and *reporting* the degradation
   (missing OCR tool → reported per-document) rather than silently downgrading.

**Remediation: `--strip-metadata`** (JPEG/TIFF only, requires Pillow) — this is the one
place the scanner does more than detect. A hypothetical text-redaction equivalent (auto-
rewriting a matched PII span) would carry real judgment risk — did it redact too much,
too little, or corrupt surrounding non-PII text — and isn't built here for exactly that
reason. Stripping EXIF has none of that risk: there's no partial-credit version of
"remove this well-defined metadata block," so the same caution doesn't apply. It
re-saves the image without ever passing
`exif=` forward (verified empirically against a real GPS-tagged fixture before
shipping — Pillow doesn't propagate EXIF unless the caller explicitly asks it to), then
re-reads the *output* file's EXIF to confirm the strip actually worked rather than
trusting the save call's silence. Writes a separate `.stripped` copy by default —
`--in-place` is opt-in and irreversible, matching the "never silently destroy the
original" convention `install_hook.sh` already follows for an existing hook file.

**Not attempted (yet):** stripping for the other `METADATA_EXTENSIONS` (PNG, HEIC, PDF,
DOCX, XLSX, PPTX) — each needs its own metadata-writing library (Pillow only round-trips
EXIF reliably for JPEG/TIFF here; PDF/Office document properties need a different
library entirely), real scope beyond this pass. Detection still covers all of them via
the file-type heuristic; only remediation is JPEG/TIFF-only for now. Also not attempted:
non-EXIF embedded file paths (e.g., a screenshot showing a file-manager path in-frame —
that's a *visual* leak, unreadable by any metadata scanner; would need an
image-understanding model).

## Deferred: Inference cues

*"Schedule patterns, location routines, relationships, life events derivable from
content."* This is fundamentally a judgment task — recognizing that "I'm traveling for
work next week" discloses an absence pattern requires understanding the sentence, not
matching a pattern. No deterministic implementation is planned; the honest path is a
local model (see below), not a regex approximation that would either miss almost
everything or flag almost everything.

## Deferred: Stylometric fingerprint

*"Writing-style signal that links pseudonymous content back to a known corpus."*
Requires a reference corpus and a similarity model — this is a research problem, not a
regex, and the source project's own open questions list it as unresolved ("how to build
the known-corpus reference... and how to update it").

## The local-model question, left open on purpose

Both deferred classes need a model that reasons about meaning, not shape. The source
project's own open question #2 — "which quantized model has the best accuracy/latency
tradeoff for this specific adversarial-review task" — is still genuinely unanswered.
Building Inference cues or Stylometric fingerprinting today would mean picking one of:

1. **Stand up a local model** (Ollama/LocalAI, per the earlier session research this
   repo's owner already did on that stack) — real setup work, and the quality/latency
   tradeoff is unvalidated.
2. **Use an external LLM API** — directly contradicts this project's own stated threat
   model ("sending content to an external LLM API for leak detection is itself a
   potential disclosure").

Neither should be chosen silently. When there's a concrete need to extend past Direct
PII, Secrets, and Metadata, that choice is the first design decision to make, not an
implementation detail to default past.
