// Unit tests for the pure retraction-cascade logic in logic.ts — no I/O, no
// network, no server. This is the one piece of "when a source decays, flag
// everything pinned against it" behavior that server.test.mjs can't exercise
// end-to-end without a real retracted DOI/PMID, so it's covered directly here
// instead. Run with `node logic.test.mjs` (stdlib only, no framework).

import { claimsToFlagOnRetraction, extractDoi, resolveSourceStatus, claimConflict } from "../dist/logic.js";

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

console.log("\n── extractDoi ────────────────────────────────────────────────");

check("plain DOI URL", extractDoi("https://doi.org/10.1000/xyz123") === "10.1000/xyz123");
check("trailing period is not part of the DOI", extractDoi("see https://doi.org/10.1000/xyz.") === "10.1000/xyz");
check("trailing comma/semicolon stripped", extractDoi("(10.1000/abc;") === "10.1000/abc");
check("unbalanced trailing ')' stripped", extractDoi("(see https://doi.org/10.1000/abc)") === "10.1000/abc");
check("balanced parentheses inside a DOI are kept", extractDoi("https://doi.org/10.1016/S0140-6736(97)11096-0") === "10.1016/S0140-6736(97)11096-0");
check("a DOI that legitimately ends in a balanced ')' is kept", extractDoi("10.1002/(SICI)1097-0258") === "10.1002/(SICI)1097-0258");
check("trailing slash is not part of the DOI", extractDoi("https://doi.org/10.1000/abc/") === "10.1000/abc");
check("no DOI in the text returns null", extractDoi("https://example.com/news/story") === null);

console.log("\n── resolveSourceStatus (retracted is sticky) ─────────────────");

check("active -> retracted transitions", resolveSourceStatus("active", "retracted") === "retracted");
check("retracted stays retracted when the check is unreachable", resolveSourceStatus("retracted", "unreachable") === "retracted");
check("retracted stays retracted when a later check says active", resolveSourceStatus("retracted", "active") === "retracted");
check("retracted stays retracted when a later check says updated", resolveSourceStatus("retracted", "updated") === "retracted");
check("active -> unreachable is allowed (not terminal)", resolveSourceStatus("active", "unreachable") === "unreachable");
check("unreachable -> active recovers", resolveSourceStatus("unreachable", "active") === "active");
check("updated -> active is allowed", resolveSourceStatus("updated", "active") === "active");

console.log("\n── claimConflict (pin_claim repeat with different inputs) ────");

const existing = { source_ids: ["doi:a", "pmid:1"], excerpt: "same excerpt" };
check("identical repeat is not a conflict", claimConflict(existing, ["doi:a", "pmid:1"], "same excerpt") === null);
check("source order does not matter", claimConflict(existing, ["pmid:1", "doi:a"], "same excerpt") === null);
check("different sources is a conflict", typeof claimConflict(existing, ["doi:b"], "same excerpt") === "string");
check("different excerpt is a conflict", typeof claimConflict(existing, ["doi:a", "pmid:1"], "other excerpt") === "string");

check("duplicate source ids are not a conflict with the deduped list", claimConflict({ source_ids: ["a"], excerpt: "e" }, ["a", "a"], "e") === null);
check("surrounding whitespace in the excerpt is not a conflict", claimConflict({ source_ids: ["a"], excerpt: "e" }, ["a"], "  e\n") === null);

console.log(`\n${"─".repeat(62)}\n  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
if (failed > 0) process.exit(1);
