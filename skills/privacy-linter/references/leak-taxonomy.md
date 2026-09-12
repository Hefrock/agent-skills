# Leak Taxonomy

The source design (`Projects/Privacy OS - Pre-Disclosure Privacy Linter`) names four
leak classes. This version builds two of them.

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

## Built: Metadata

Two tiers:

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

**Not attempted (yet):** other EXIF fields (timestamp, camera/device model), embedded
document properties (author, revision history in Office files), and non-EXIF embedded
file paths (e.g., a screenshot showing a file-manager path in-frame — that's a *visual*
leak, unreadable by any metadata scanner; would need an image-understanding model).

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
PII + Metadata, that choice is the first design decision to make, not an implementation
detail to default past.
