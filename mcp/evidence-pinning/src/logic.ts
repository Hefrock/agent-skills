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
