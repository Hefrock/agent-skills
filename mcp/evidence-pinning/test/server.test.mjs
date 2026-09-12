// End-to-end tests for the evidence-pinning MCP server.
//
// Drives the compiled server over its real STDIO JSON-RPC transport — no mocks —
// and asserts on source registration/dedup, claim pinning (and its dependency on
// registered sources), provenance logging, and flagging. The one live-network
// case (check_source_decay against a URL) targets a reserved, guaranteed-unroutable
// address (RFC 5737 TEST-NET) so the "unreachable" transition is deterministic
// without depending on any real external service being up.
//
// Stdlib + built server only; no test framework dependency.

import { spawn } from "child_process";
import { createInterface } from "readline";
import fs from "fs/promises";
import path from "path";
import os from "os";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.resolve(__dirname, "..", "dist", "index.js");
const STORE = await fs.mkdtemp(path.join(os.tmpdir(), "evidence-pinning-test-"));

// ── Server harness ────────────────────────────────────────────────────────────
const server = spawn("node", [SERVER], {
  env: { ...process.env, EVIDENCE_STORE_PATH: STORE },
  stdio: ["pipe", "pipe", "pipe"],
});

const pending = new Map();
const rl = createInterface({ input: server.stdout });
rl.on("line", (line) => {
  try {
    const msg = JSON.parse(line);
    if (msg.id !== undefined && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  } catch {}
});

server.stderr.on("data", () => {});

let nextId = 0;
function rpc(method, params) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    server.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
}

await rpc("initialize", {
  protocolVersion: "2024-11-05",
  capabilities: {},
  clientInfo: { name: "test", version: "1" },
});

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

function tool(name, args) {
  return rpc("tools/call", { name, arguments: args });
}

function parse(r) {
  return JSON.parse(r.result?.content?.[0]?.text ?? "{}");
}

// ── Tests ─────────────────────────────────────────────────────────────────────

console.log("\n── register_source ──────────────────────────────────────────");

let r = await tool("register_source", { url: "https://doi.org/10.1000/xyz1", title: "A Paper" });
let d = parse(r);
check("DOI extracted from URL", d.source_id === "doi:10.1000/xyz1" && d.id_type === "doi");
check("new registration reports is_new: true", d.is_new === true);
const doiSourceId = d.source_id;

r = await tool("register_source", { url: "https://doi.org/10.1000/xyz1", title: "A Paper (again)" });
d = parse(r);
check("re-registering the same DOI is idempotent", d.is_new === false && d.source_id === doiSourceId);

r = await tool("register_source", { url: "https://pubmed.ncbi.nlm.nih.gov/12345678", title: "A PubMed Paper" });
d = parse(r);
check("PMID extracted from URL", d.source_id === "pmid:12345678" && d.id_type === "pmid");
const pmidSourceId = d.source_id;

r = await tool("register_source", { url: "https://statnews.com/some-article", title: "A Press Article" });
d = parse(r);
check("plain URL falls back to url: hash id", d.id_type === "url" && d.source_id.startsWith("url:"));
const urlSourceId = d.source_id;

r = await tool("register_source", { url: "https://example.com/report", title: "Forced DOI", id_hint: "doi:10.2000/forced" });
d = parse(r);
check("id_hint forces canonical id over URL parsing", d.source_id === "doi:10.2000/forced");

console.log("\n── pin_claim ─────────────────────────────────────────────────");

r = await tool("pin_claim", { run_id: "run1", text: "X reduces Y by 40%", source_ids: ["doi:nonexistent"], excerpt: "..." });
check("pinning against an unregistered source errors", r.result?.isError, JSON.stringify(r.result));

r = await tool("pin_claim", { run_id: "run1", text: "X reduces Y by 40%", source_ids: [], excerpt: "..." });
check("pinning with zero sources errors", r.result?.isError);

r = await tool("pin_claim", { run_id: "run1", text: "X reduces Y by 40%", source_ids: [doiSourceId], excerpt: "Study found a 40% reduction in Y." });
d = parse(r);
check("pin_claim against a registered source succeeds", d.is_new === true && d.status === "pinned");
const claimId = d.claim_id;

r = await tool("pin_claim", { run_id: "run1", text: "X reduces Y by 40%", source_ids: [doiSourceId], excerpt: "Study found a 40% reduction in Y." });
d = parse(r);
check("re-pinning the same (run_id, text) is idempotent", d.is_new === false && d.claim_id === claimId);

r = await tool("pin_claim", { run_id: "run1", text: "Z improves W", source_ids: [doiSourceId, pmidSourceId], excerpt: "..." });
d = parse(r);
check("a claim can cite multiple sources", d.is_new === true);
const multiSourceClaimId = d.claim_id;

console.log("\n── get_claims / verify_claim ────────────────────────────────");

r = await tool("get_claims", { run_id: "run1" });
d = parse(r);
check("get_claims returns all claims for the run", d.total === 2);
check("get_claims inlines source records", d.claims.find((c) => c.claim_id === claimId)?.sources?.[0]?.source_id === doiSourceId);

r = await tool("get_claims", { run_id: "run-with-nothing-pinned" });
d = parse(r);
check("get_claims for a run with no claims returns an empty list, not an error", !r.result?.isError && d.total === 0);

r = await tool("verify_claim", { claim_id: multiSourceClaimId });
d = parse(r);
check("verify_claim inlines both backing sources", d.sources?.length === 2);

r = await tool("verify_claim", { claim_id: "nonexistent" });
check("verify_claim on an unknown claim_id errors", r.result?.isError);

console.log("\n── flag_claim ────────────────────────────────────────────────");

r = await tool("flag_claim", { claim_id: claimId, reason: "Excerpt does not actually support this claim" });
d = parse(r);
check("flag_claim marks the claim flagged", d.status === "flagged");

r = await tool("verify_claim", { claim_id: claimId });
d = parse(r);
check("flagged status persists on subsequent lookup", d.status === "flagged" && d.flag_reason?.includes("does not actually support"));

console.log("\n── get_provenance ────────────────────────────────────────────");

r = await tool("get_provenance", { target_type: "claim", target_id: claimId });
d = parse(r);
const actions = d.events.map((e) => e.action);
check("provenance log has pinned then flagged, in order", actions[0] === "pinned" && actions.includes("flagged"));

r = await tool("get_provenance", { target_type: "source", target_id: doiSourceId });
d = parse(r);
check("source provenance log records registration", d.events.some((e) => e.action === "registered"));

console.log("\n── check_source_decay (URL reachability only) ───────────────");

r = await tool("register_source", { url: "http://192.0.2.1/unreachable", title: "Deliberately unroutable (RFC 5737 TEST-NET-1)" });
d = parse(r);
const unreachableSourceId = d.source_id;

r = await tool("check_source_decay", { source_id: unreachableSourceId });
d = parse(r);
check(
  "a URL source on the RFC 5737 test-net range comes back unreachable, not retracted",
  d.status === "unreachable",
  `got status=${d.status} detail=${d.detail}`
);
check("URL-type decay check never claims 'retracted' — no such signal exists for plain URLs", d.status !== "retracted");

r = await tool("get_provenance", { target_type: "source", target_id: unreachableSourceId });
d = parse(r);
check("decay check logs both 'checked' and 'status_changed' (active -> unreachable)", d.events.some((e) => e.action === "checked") && d.events.some((e) => e.action === "status_changed"));

console.log("\n── check_source_decay retraction cascades to pinned claims ──");

r = await tool("register_source", { url: "https://doi.org/10.3000/cascade", title: "Will be manually flipped via a second registration is not possible — see below" });
d = parse(r);
const cascadeSourceId = d.source_id;
r = await tool("pin_claim", { run_id: "run2", text: "Cascade test claim", source_ids: [cascadeSourceId], excerpt: "..." });
d = parse(r);
const cascadeClaimId = d.claim_id;

// This DOI doesn't exist, so check_source_decay hits the real Crossref API and
// gets back a genuine miss — it only proves the network path doesn't crash on
// an unrecognized DOI. The actual retraction -> auto-flag cascade logic (does
// every currently-pinned claim citing a newly-retracted source get flagged) is
// pure and I/O-free by design (src/logic.ts), and is covered directly, with
// synthetic retraction fixtures, in logic.test.mjs.
r = await tool("check_source_decay", { source_id: cascadeSourceId });
d = parse(r);
check(
  "checking a DOI that doesn't exist on Crossref doesn't crash the server",
  !r.result?.isError,
  JSON.stringify(d)
);

r = await tool("check_source_decay", { source_id: "doi:nonexistent" });
check("checking an unregistered source_id errors", r.result?.isError);

// ── Results ───────────────────────────────────────────────────────────────────
server.kill();
await fs.rm(STORE, { recursive: true, force: true });

console.log(`\n${"─".repeat(62)}\n  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
if (failed > 0) process.exit(1);
