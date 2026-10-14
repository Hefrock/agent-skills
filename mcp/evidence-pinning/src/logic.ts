// Pure, I/O-free business logic pulled out of index.ts specifically so it's
// unit-testable without spawning the server or hitting a live retracted
// DOI/PMID — the same "classifier vs. network wrapper" split decay.ts uses.

export type ClaimStatus = "pinned" | "flagged" | "unpinned";

export interface ClaimLike {
  claim_id: string;
  status: ClaimStatus;
  source_ids: string[];
}

// Given the full set of claims and a source_id that just transitioned to
// "retracted", return the claims that must be auto-flagged: currently-pinned
// claims citing that source. Claims already flagged or unpinned are left
// alone — this only ever moves a claim pinned -> flagged, never touches one
// that's already in a terminal state.
export function claimsToFlagOnRetraction<T extends ClaimLike>(claims: T[], retractedSourceId: string): T[] {
  return claims.filter((c) => c.status === "pinned" && c.source_ids.includes(retractedSourceId));
}

// ── DOI extraction ───────────────────────────────────────────────────────────
// The character class has to allow "." and "()" because real DOIs contain them
// (e.g. 10.1016/S0140-6736(97)11096-0), which means a greedy match also swallows
// trailing sentence punctuation or the ")" closing a parenthetical around a URL.
// Strip trailing . , ; : / always, and a trailing ")" only when it is unbalanced.
const DOI_PATTERN = /10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+/;

function trimDoi(raw: string): string {
  let doi = raw;
  for (;;) {
    const last = doi[doi.length - 1];
    if (last === "." || last === "," || last === ";" || last === ":" || last === "/") {
      doi = doi.slice(0, -1);
    } else if (last === ")" && (doi.match(/\)/g) ?? []).length > (doi.match(/\(/g) ?? []).length) {
      doi = doi.slice(0, -1);
    } else {
      return doi;
    }
  }
}

export function extractDoi(text: string): string | null {
  const match = text.match(DOI_PATTERN);
  return match ? trimDoi(match[0]) : null;
}

// ── Source status transitions ────────────────────────────────────────────────
// "retracted" is terminal. A later check that comes back unreachable (network
// blip), active, or updated must not erase a retraction that was observed —
// a wrong "retracted" is recoverable by a human, a silently cleared one isn't.
export type SourceStatusLike = "active" | "retracted" | "updated" | "unreachable";

export function resolveSourceStatus(previous: SourceStatusLike, observed: SourceStatusLike): SourceStatusLike {
  return previous === "retracted" ? "retracted" : observed;
}

// ── pin_claim repeat handling ────────────────────────────────────────────────
// claim_id is sha256(run_id|text), so a repeat call with the same text but
// different sources or excerpt used to return "already pinned" and silently
// discard the new inputs. Report a conflict instead; an identical repeat
// (the idempotent retry case) is not a conflict.
export function claimConflict(
  existing: { source_ids: string[]; excerpt: string },
  sourceIds: string[],
  excerpt: string
): string | null {
  const ids = (list: string[]) => [...new Set(list)].sort().join("|");
  if (ids(existing.source_ids) !== ids(sourceIds)) {
    return `source_ids differ from the already-pinned claim (${existing.source_ids.join(", ")})`;
  }
  if (existing.excerpt.trim() !== excerpt.trim()) return "excerpt differs from the already-pinned claim";
  return null;
}
