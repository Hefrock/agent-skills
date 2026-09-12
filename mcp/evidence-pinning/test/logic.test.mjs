// Unit tests for the pure retraction-cascade logic in logic.ts — no I/O, no
// network, no server. This is the one piece of "when a source decays, flag
// everything pinned against it" behavior that server.test.mjs can't exercise
// end-to-end without a real retracted DOI/PMID, so it's covered directly here
// instead. Run with `node logic.test.mjs` (stdlib only, no framework).

import { claimsToFlagOnRetraction } from "../dist/logic.js";

let passed = 0, failed = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  PASS  ${label}`);
    passed++;
  } else {
    console.log(`  FAIL  ${label}${detail ? " — " + detail : ""}`);
    failed++;
  }
}

console.log("── claimsToFlagOnRetraction ──────────────────────────────────");

const claims = [
  { claim_id: "c1", status: "pinned", source_ids: ["doi:retracted-one"] },
  { claim_id: "c2", status: "pinned", source_ids: ["doi:still-fine"] },
  { claim_id: "c3", status: "pinned", source_ids: ["doi:retracted-one", "doi:still-fine"] },
  { claim_id: "c4", status: "flagged", source_ids: ["doi:retracted-one"] },
  { claim_id: "c5", status: "unpinned", source_ids: ["doi:retracted-one"] },
];

const flagged = claimsToFlagOnRetraction(claims, "doi:retracted-one");
const flaggedIds = flagged.map((c) => c.claim_id).sort();

check(
  "flags every currently-pinned claim citing the retracted source",
  flaggedIds.includes("c1") && flaggedIds.includes("c3")
);
check("does not touch a claim that doesn't cite the retracted source", !flaggedIds.includes("c2"));
check("does not re-flag a claim that's already flagged", !flaggedIds.includes("c4"));
check("does not touch a claim that's already unpinned", !flaggedIds.includes("c5"));
check("exactly two claims match this fixture", flagged.length === 2, `got ${flagged.length}`);

const noMatches = claimsToFlagOnRetraction(claims, "doi:never-cited");
check("a source no claim cites flags nothing", noMatches.length === 0);

const empty = claimsToFlagOnRetraction([], "doi:anything");
check("an empty claim set flags nothing (no crash)", empty.length === 0);

console.log(`\n${"─".repeat(62)}\n  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
if (failed > 0) process.exit(1);
