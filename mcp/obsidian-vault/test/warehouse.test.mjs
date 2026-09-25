// End-to-end tests for the warehouse passage-retrieval tools (Phase 2).
// Separate file from server.test.mjs because these tests need a server
// process spawned WITH WAREHOUSE_PATH set, distinct from server.test.mjs's
// warehouse-unset server (which itself covers the "tools absent when unset"
// case just by being the way it already is).
//
// Drives the compiled server over real STDIO JSON-RPC, same harness style as
// server.test.mjs: no mocks, stdlib + built server only.

import { spawn } from "child_process";
import { createInterface } from "readline";
import fs from "fs/promises";
import path from "path";
import os from "os";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.resolve(__dirname, "..", "dist", "index.js");
const VAULT = await fs.mkdtemp(path.join(os.tmpdir(), "obsidian-vault-test-"));
const WAREHOUSE = await fs.mkdtemp(path.join(os.tmpdir(), "warehouse-test-"));

// ── Fixture doc ids (arbitrary, fixed-length hex -- not real hashes of the
// fixture files; this server never re-verifies a doc_id against its raw
// content, that's the warehouse's own audit.py) ──────────────────────────────
const DOC1 = "1".repeat(64); // plaintext, non-BMP characters, unique search term
const DOC2 = "2".repeat(64); // text-layer:pdftotext, a real \f page boundary
const DOC3 = "3".repeat(64); // text-layer:pymupdf, a stray \f that must NOT split
const EVIL_DOTDOT = "e".repeat(64); // text_path escapes the root lexically
const EVIL_SYMLINK = "f".repeat(64); // text_path escapes the root via a symlink

// ── Fixture warehouse ─────────────────────────────────────────────────────────
await fs.mkdir(`${WAREHOUSE}/text`, { recursive: true });
await fs.mkdir(`${WAREHOUSE}/raw`, { recursive: true });

const emoji = "\u{1F600}";
const rareCJK = "\u{20BB7}";
const doc1Text =
  `This document discusses zorblaxterm in detail. ${emoji} an emoji and ${rareCJK} a rare CJK character ` +
  `appear right in this very sentence, before the term that matters for the offset invariant test.\n\n` +
  `A second paragraph continues with more filler content to pad things out a little further than a single short line would.`;
await fs.writeFile(`${WAREHOUSE}/text/doc1.txt`, doc1Text, "utf-8");

const doc2Text =
  "Page one of doc two mentions frobnicatequery right here in the first page's own content, with a little padding besides." +
  "\f" +
  "Page two continues with entirely different, unrelated content that has nothing to do with the first page's topic at all.";
await fs.writeFile(`${WAREHOUSE}/text/doc2.txt`, doc2Text, "utf-8");

const doc3Text =
  "Page-like content with a stray form feed \f embedded mid-sentence, which must never split anything for this " +
  "extraction method, since pymupdf's output doesn't reliably carry \\f as a real page marker at all.";
await fs.writeFile(`${WAREHOUSE}/text/doc3.txt`, doc3Text, "utf-8");

// A directory OUTSIDE the warehouse root, and a symlink inside the warehouse
// pointing at a file in it -- for the symlink-escape containment test.
const OUTSIDE = await fs.mkdtemp(path.join(os.tmpdir(), "warehouse-outside-"));
await fs.writeFile(`${OUTSIDE}/secret.txt`, "this must never be readable through the warehouse tools", "utf-8");
await fs.symlink(`${OUTSIDE}/secret.txt`, `${WAREHOUSE}/text/escape-link.txt`);

const manifest = {
  [DOC1]: {
    title: "Zorblax Reference Document",
    raw_path: "raw/doc1.pdf", ext: ".pdf", bytes: 1000, char_count: doc1Text.length,
    extraction_method: "plaintext", ingested: "2026-01-01",
    text_path: "text/doc1.txt",
  },
  [DOC2]: {
    title: "Frobnicate Manual",
    raw_path: "raw/doc2.pdf", ext: ".pdf", bytes: 1000, char_count: doc2Text.length,
    extraction_method: "text-layer:pdftotext", ingested: "2026-01-02",
    text_path: "text/doc2.txt",
    source_url: "https://example.com/frobnicate", // unrecognized-to-some-readers, extra field -- must not error
  },
  [DOC3]: {
    title: "Pymupdf Extraction Sample",
    raw_path: "raw/doc3.pdf", ext: ".pdf", bytes: 1000, char_count: doc3Text.length,
    extraction_method: "text-layer:pymupdf", ingested: "2026-01-03",
    text_path: "text/doc3.txt",
  },
  [EVIL_DOTDOT]: {
    title: "Malicious entry (lexical traversal)",
    raw_path: "raw/evil.pdf", ext: ".pdf", bytes: 1, char_count: 1,
    extraction_method: "plaintext", ingested: "2026-01-04",
    text_path: "../../../../../../etc/passwd",
  },
  [EVIL_SYMLINK]: {
    title: "Malicious entry (symlink escape)",
    raw_path: "raw/evil2.pdf", ext: ".pdf", bytes: 1, char_count: 1,
    extraction_method: "plaintext", ingested: "2026-01-05",
    text_path: "text/escape-link.txt",
  },
};
await fs.writeFile(`${WAREHOUSE}/manifest.json`, JSON.stringify(manifest, null, 2), "utf-8");

// ── Fixture vault: one Source note whose doc_id matches DOC1, none for DOC2 ──
await fs.mkdir(`${VAULT}/Sources`, { recursive: true });
await fs.writeFile(
  `${VAULT}/Sources/zorblax-reference.md`,
  `---\ntype: source\nstatus: draft\nconfidence: medium\nupdated: 2026-01-01\n` +
  `warehouse_repo: Hefrock/knowledge-warehouse\ndoc_id: sha256:${DOC1}\n` +
  `warehouse_path: raw/doc1.pdf\ntext_path: text/doc1.txt\n---\n\n## Summary\n\nAbout zorblaxterm.\n`
);

// ── Server harness ────────────────────────────────────────────────────────────
const server = spawn("node", [SERVER], {
  env: { ...process.env, OBSIDIAN_VAULT_PATH: VAULT, WAREHOUSE_PATH: WAREHOUSE },
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

function rpc(id, method, params) {
  return new Promise((resolve) => {
    pending.set(id, resolve);
    server.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
}
function tool(id, name, args) { return rpc(id, "tools/call", { name, arguments: args }); }
function parse(r) { return JSON.parse(r.result?.content?.[0]?.text ?? "{}"); }

await rpc(0, "initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "test", version: "1" } });

let passed = 0, failed = 0;
function check(label, condition, detail = "") {
  if (condition) { console.log(`  PASS  ${label}`); passed++; }
  else { console.log(`  FAIL  ${label}${detail ? " — " + detail : ""}`); failed++; }
}

// Code-point-accurate reference slice, for the offset-invariant test --
// deliberately NOT the same implementation as chunker.ts's, so it's a real
// independent check, not the same code confirming itself.
function codepointSlice(text, start, end) {
  return Array.from(text).slice(start, end).join("");
}

// ── Tests ─────────────────────────────────────────────────────────────────────

console.log("\n── search_warehouse: basic retrieval and doc_id form ────────────");

let r = await tool(1, "tools/list", {});
// (tools/list isn't a tools/call — handled directly below instead)

r = await rpc(2, "tools/list", {});
const toolNames = r.result.tools.map((t) => t.name);
check("tools/list — search_warehouse is registered when WAREHOUSE_PATH is set", toolNames.includes("search_warehouse"));
check("tools/list — read_warehouse_text is registered when WAREHOUSE_PATH is set", toolNames.includes("read_warehouse_text"));

r = await tool(3, "search_warehouse", { query: "zorblaxterm" });
let d = parse(r);
check("search_warehouse — finds the matching document", d.hits?.some((h) => h.title === "Zorblax Reference Document"), `hits: ${JSON.stringify(d.hits?.map((h)=>h.title))}`);
const zorblaxHit = d.hits?.find((h) => h.title === "Zorblax Reference Document");
check("search_warehouse — doc_id carries the sha256: prefix", zorblaxHit?.doc_id === `sha256:${DOC1}`, `doc_id: ${zorblaxHit?.doc_id}`);
check("search_warehouse — offset_unit is codepoint", zorblaxHit?.offset_unit === "codepoint");
check("search_warehouse — passage_hash is present and sha256:-prefixed", typeof zorblaxHit?.passage_hash === "string" && zorblaxHit.passage_hash.startsWith("sha256:"));

r = await tool(4, "search_warehouse", { query: "zorblaxterm", doc_id: DOC1 });
d = parse(r);
check("search_warehouse — doc_id filter accepts the BARE hex form", d.hits?.length > 0 && d.hits.every((h) => h.doc_id === `sha256:${DOC1}`));

r = await tool(5, "search_warehouse", { query: "zorblaxterm", doc_id: `sha256:${DOC1}` });
d = parse(r);
check("search_warehouse — doc_id filter accepts the sha256:-PREFIXED form", d.hits?.length > 0 && d.hits.every((h) => h.doc_id === `sha256:${DOC1}`));

console.log("\n── Offset invariant: slicing by codepoint offset reproduces the passage ─");

r = await tool(6, "search_warehouse", { query: "zorblaxterm emoji CJK padding filler" });
d = parse(r);
let allMatch = true;
for (const h of d.hits ?? []) {
  const sourceText = h.doc_id === `sha256:${DOC1}` ? doc1Text : h.doc_id === `sha256:${DOC2}` ? doc2Text : doc3Text;
  const expected = codepointSlice(sourceText, h.char_start, h.char_end);
  if (expected !== h.passage) { allMatch = false; console.log(`    mismatch for ${h.doc_id}: expected=${JSON.stringify(expected.slice(0,60))} got=${JSON.stringify(h.passage.slice(0,60))}`); }
}
check("offset invariant — every hit's passage equals text.slice(char_start, char_end) by codepoint, including around non-BMP chars", allMatch && (d.hits?.length ?? 0) > 0);

console.log("\n── source_note resolution ────────────────────────────────────────");

r = await tool(7, "search_warehouse", { query: "zorblaxterm" });
d = parse(r);
const doc1Hit = d.hits?.find((h) => h.doc_id === `sha256:${DOC1}`);
check("source_note — resolves to the vault note carrying the matching doc_id", doc1Hit?.source_note === "Sources/zorblax-reference.md", `got: ${doc1Hit?.source_note}`);

r = await tool(8, "search_warehouse", { query: "frobnicatequery" });
d = parse(r);
const doc2Hit = d.hits?.find((h) => h.doc_id === `sha256:${DOC2}`);
check("source_note — null when no vault note carries this doc_id", doc2Hit?.source_note === null, `got: ${JSON.stringify(doc2Hit?.source_note)}`);

console.log("\n── Traversal rejection: other documents still index ─────────────");

r = await tool(9, "search_warehouse", { query: "zorblaxterm frobnicatequery pymupdf" });
d = parse(r);
const evilHitDotdot = d.hits?.some((h) => h.doc_id === `sha256:${EVIL_DOTDOT}`);
const evilHitSymlink = d.hits?.some((h) => h.doc_id === `sha256:${EVIL_SYMLINK}`);
check("traversal — a lexically-escaping text_path never produces a hit", !evilHitDotdot);
check("traversal — a symlink-escaping text_path never produces a hit", !evilHitSymlink);
check(
  "traversal — the well-formed documents still index despite the two bad entries",
  d.hits?.some((h) => h.doc_id === `sha256:${DOC1}`) && d.hits?.some((h) => h.doc_id === `sha256:${DOC2}`) && d.hits?.some((h) => h.doc_id === `sha256:${DOC3}`)
);

r = await tool(10, "read_warehouse_text", { doc_id: EVIL_SYMLINK, char_start: 0, char_end: 10 });
check("traversal — read_warehouse_text on the symlink-escaping doc_id errors rather than reading outside the root", r.result?.isError === true);

console.log("\n── read_warehouse_text: clamping and cap ─────────────────────────");

r = await tool(11, "read_warehouse_text", { doc_id: DOC1, char_start: -50, char_end: 20 });
d = parse(r);
check("read_warehouse_text — a negative char_start clamps to 0", d.char_start === 0, `got char_start=${d.char_start}`);

const doc1Cps = Array.from(doc1Text).length;
r = await tool(12, "read_warehouse_text", { doc_id: DOC1, char_start: 0, char_end: doc1Cps + 5000 });
d = parse(r);
check("read_warehouse_text — an out-of-range char_end clamps to the document length", d.char_end === doc1Cps, `got char_end=${d.char_end}, doc length=${doc1Cps}`);

r = await tool(13, "read_warehouse_text", { doc_id: DOC1, char_start: 0, char_end: 50000 });
d = parse(r);
check("read_warehouse_text — a span longer than 8,000 characters is capped", d.char_end - d.char_start <= 8000, `span=${d.char_end - d.char_start}`);

console.log("\n── Per-document hit cap ──────────────────────────────────────────");

r = await tool(14, "search_warehouse", { query: "content page paragraph document", limit: 25 });
d = parse(r);
const perDoc = {};
for (const h of d.hits ?? []) perDoc[h.doc_id] = (perDoc[h.doc_id] ?? 0) + 1;
const overCap = Object.values(perDoc).some((n) => n > 3);
check("per-document cap — no single document contributes more than 3 hits", !overCap, `counts: ${JSON.stringify(perDoc)}`);

console.log("\n── passage_hash round-trip and staleness detection ───────────────");

r = await tool(15, "search_warehouse", { query: "zorblaxterm" });
d = parse(r);
const hitForRoundtrip = d.hits.find((h) => h.doc_id === `sha256:${DOC1}`);

r = await tool(16, "read_warehouse_text", { doc_id: DOC1, char_start: hitForRoundtrip.char_start, char_end: hitForRoundtrip.char_end });
d = parse(r);
check(
  "passage_hash round-trip — matches read_warehouse_text's text_hash for the same offsets",
  d.text_hash === hitForRoundtrip.passage_hash,
  `passage_hash=${hitForRoundtrip.passage_hash} text_hash=${d.text_hash}`
);

// Simulate intake.py --force: rewrite the text file in place, bump mtime
// explicitly so this doesn't depend on filesystem mtime-resolution timing.
await fs.writeFile(`${WAREHOUSE}/text/doc1.txt`, "Completely different re-extracted content, no mention of the old term at all.", "utf-8");
const future = new Date(Date.now() + 5000);
await fs.utimes(`${WAREHOUSE}/text/doc1.txt`, future, future);

r = await tool(17, "read_warehouse_text", { doc_id: DOC1, char_start: hitForRoundtrip.char_start, char_end: hitForRoundtrip.char_end });
d = parse(r);
check(
  "passage_hash round-trip — after a simulated re-extraction, the SAME offsets now produce a DIFFERENT hash (staleness detectable)",
  d.text_hash !== hitForRoundtrip.passage_hash,
  `old=${hitForRoundtrip.passage_hash} new=${d.text_hash}`
);

console.log("\n── Chunking: pdftotext form-feed is a hard boundary, pymupdf's stray \\f is not ─");

r = await tool(18, "search_warehouse", { query: "frobnicatequery", limit: 25 });
d = parse(r);
const doc2Hits = d.hits.filter((h) => h.doc_id === `sha256:${DOC2}`);
const ffIndex = Array.from(doc2Text).indexOf("\f");
const crossesFF = doc2Hits.some((h) => h.char_start < ffIndex && h.char_end > ffIndex);
check("chunking — no doc2 (pdftotext) passage spans across its real \\f boundary", !crossesFF, `ff at ${ffIndex}, hits: ${JSON.stringify(doc2Hits.map(h=>[h.char_start,h.char_end]))}`);

r = await tool(19, "search_warehouse", { query: "pymupdf" });
d = parse(r);
const doc3Hit = d.hits.find((h) => h.doc_id === `sha256:${DOC3}`);
const doc3FFIndex = Array.from(doc3Text).indexOf("\f");
check(
  "chunking — doc3 (pymupdf) is NOT split at its stray \\f -- whole thing is one passage",
  doc3Hit !== undefined && doc3Hit.char_start === 0 && doc3Hit.char_end === Array.from(doc3Text).length,
  `doc3 hit: [${doc3Hit?.char_start},${doc3Hit?.char_end}) ff at ${doc3FFIndex}, doc length ${Array.from(doc3Text).length}`
);

console.log("\n── Invalid WAREHOUSE_PATH: logs and disables tools, never exits ──");

// A directory that exists but has no manifest.json — "set but invalid" per
// the spec, distinct from "unset" (server.test.mjs covers unset).
const INVALID_WAREHOUSE = await fs.mkdtemp(path.join(os.tmpdir(), "warehouse-invalid-"));
const badServer = spawn("node", [SERVER], {
  env: { ...process.env, OBSIDIAN_VAULT_PATH: VAULT, WAREHOUSE_PATH: INVALID_WAREHOUSE },
  stdio: ["pipe", "pipe", "pipe"],
});
const badPending = new Map();
createInterface({ input: badServer.stdout }).on("line", (line) => {
  try {
    const msg = JSON.parse(line);
    if (msg.id !== undefined && badPending.has(msg.id)) { badPending.get(msg.id)(msg); badPending.delete(msg.id); }
  } catch {}
});
badServer.stderr.on("data", () => {});
function badRpc(id, method, params) {
  return new Promise((resolve) => { badPending.set(id, resolve); badServer.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n"); });
}
await badRpc(0, "initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "test", version: "1" } });
const badToolsResp = await badRpc(1, "tools/list", {});
const stillAlive = badServer.exitCode === null && !badServer.killed;
check("invalid WAREHOUSE_PATH — server does not exit", stillAlive);
check(
  "invalid WAREHOUSE_PATH — warehouse tools are not registered, vault tools still are",
  !badToolsResp.result.tools.map((t) => t.name).includes("search_warehouse") &&
    badToolsResp.result.tools.map((t) => t.name).includes("search_notes")
);
badServer.kill();
await fs.rm(INVALID_WAREHOUSE, { recursive: true, force: true });

// ── Results ───────────────────────────────────────────────────────────────────
server.kill();
await fs.rm(VAULT, { recursive: true, force: true });
await fs.rm(WAREHOUSE, { recursive: true, force: true });
await fs.rm(OUTSIDE, { recursive: true, force: true });
console.log(`\n${"─".repeat(60)}`);
console.log(`  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
