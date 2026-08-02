// End-to-end tests for the obsidian-vault MCP server.
//
// Drives the compiled server over its real STDIO JSON-RPC transport — no mocks —
// and asserts on write-mode semantics (create/update/upsert), pre-write backups,
// and the ReDoS fix in search. Run with `npm test` (builds first).
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
const VAULT = await fs.mkdtemp(path.join(os.tmpdir(), "obsidian-vault-test-"));

// ── Setup ─────────────────────────────────────────────────────────────────────
await fs.mkdir(`${VAULT}/Knowledge`, { recursive: true });
await fs.mkdir(`${VAULT}/Journal/Daily`, { recursive: true });
await fs.writeFile(`${VAULT}/Knowledge/existing.md`, "---\ntype: concept\nstatus: draft\n---\n# Existing\n\nOriginal content.");

// ── Server harness ────────────────────────────────────────────────────────────
const server = spawn("node", [SERVER], {
  env: { ...process.env, OBSIDIAN_VAULT_PATH: VAULT },
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

await rpc(0, "initialize", {
  protocolVersion: "2024-11-05",
  capabilities: {},
  clientInfo: { name: "test", version: "1" },
});

// ── Test helpers ──────────────────────────────────────────────────────────────
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

function tool(id, name, args) {
  return rpc(id, "tools/call", { name, arguments: args });
}

function parse(r) {
  return JSON.parse(r.result?.content?.[0]?.text ?? "{}");
}

// ── Tests ─────────────────────────────────────────────────────────────────────

console.log("\n── write_note: mode=create ──────────────────────────────────────");

let r = await tool(1, "write_note", { path: "Knowledge/new-create.md", content: "---\ntype: concept\n---\n# New", mode: "create" });
let d = parse(r);
check("create mode — new file succeeds", d.written === true && d.backed_up === false);

r = await tool(2, "write_note", { path: "Knowledge/existing.md", content: "overwrite", mode: "create" });
d = parse(r);
check("create mode — existing file errors", r.result?.isError && d.error?.includes("already exists"));

const afterCreate = await fs.readFile(`${VAULT}/Knowledge/existing.md`, "utf-8");
check("create mode — existing file untouched", afterCreate.includes("Original content"));

console.log("\n── write_note: mode=update ──────────────────────────────────────");

r = await tool(3, "write_note", { path: "Knowledge/existing.md", content: "---\ntype: concept\n---\n# Updated\n\nNew content.", mode: "update" });
d = parse(r);
check("update mode — existing file succeeds", d.written === true && d.backed_up === true);

const trashFiles = await fs.readdir(`${VAULT}/.trash`);
const backup = trashFiles.find(f => f.startsWith("existing_backup_"));
check("update mode — backup created in .trash/", backup !== undefined, `trash: ${JSON.stringify(trashFiles)}`);

if (backup) {
  const backupContent = await fs.readFile(`${VAULT}/.trash/${backup}`, "utf-8");
  check("update mode — backup has original content", backupContent.includes("Original content"));
}

const afterUpdate = await fs.readFile(`${VAULT}/Knowledge/existing.md`, "utf-8");
check("update mode — file updated on disk", afterUpdate.includes("New content"));

r = await tool(4, "write_note", { path: "Knowledge/ghost.md", content: "x", mode: "update" });
d = parse(r);
check("update mode — non-existent file errors", r.result?.isError && d.error?.includes("does not exist"));

console.log("\n── write_note: mode=upsert (default) ───────────────────────────");

r = await tool(5, "write_note", { path: "Knowledge/upsert-new.md", content: "---\ntype: concept\n---\n# Upsert New", mode: "upsert" });
d = parse(r);
check("upsert mode — new file succeeds, backed_up=false", d.written === true && d.backed_up === false);

r = await tool(6, "write_note", { path: "Knowledge/upsert-new.md", content: "---\ntype: concept\n---\n# Upsert Overwrite", mode: "upsert" });
d = parse(r);
check("upsert mode — existing file succeeds, backed_up=true", d.written === true && d.backed_up === true);

r = await tool(7, "write_note", { path: "Knowledge/upsert-new.md", content: "---\ntype: concept\n---\n# No Mode" });
d = parse(r);
check("no mode — defaults to upsert (backward compat)", d.written === true);

console.log("\n── ReDoS regression ─────────────────────────────────────────────");

const reDoSQuery = "a.+a.+a.+b (test) [bracket] {brace}";
const start = Date.now();
r = await tool(8, "search_notes", { query: reDoSQuery });
const elapsed = Date.now() - start;
d = parse(r);
check("ReDoS — special chars in query don't hang server", elapsed < 2000, `took ${elapsed}ms`);
check("ReDoS — search returns valid result shape", Array.isArray(d.results));

console.log("\n── Excerpt selection: whitespace in query ───────────────────────");

// Regression: query tokenization used to be duplicated between scoring (which
// filtered empty terms) and excerpt selection (which did not). Leading/trailing
// whitespace produced an empty-string term, and `line.includes("")` is true for
// every line — so the excerpt silently became the note's FIRST line regardless
// of where the match actually was. Scores stayed correct, which made this hard
// to spot: right notes, wrong excerpts.
await fs.writeFile(
  `${VAULT}/Knowledge/excerpt-fixture.md`,
  "---\ntype: concept\n---\nFiller opening line with no match.\nThe zebrafish appears on the second line."
);

for (const [label, q] of [["exact", "zebrafish"], ["leading space", " zebrafish"], ["trailing space", "zebrafish "]]) {
  r = await tool(20, "search_notes", { query: q });
  d = parse(r);
  const hit = d.results?.find((x) => x.path.includes("excerpt-fixture"));
  check(
    `excerpt — ${label} query returns the matching line, not line 1`,
    hit !== undefined && hit.excerpt.includes("zebrafish"),
    `got: ${JSON.stringify(hit?.excerpt)}`
  );
}

console.log("\n── Multiple backups don't collide ───────────────────────────────");

r = await tool(9, "write_note", { path: "Knowledge/collision-test.md", content: "v1", mode: "upsert" });
await new Promise(res => setTimeout(res, 10));
r = await tool(10, "write_note", { path: "Knowledge/collision-test.md", content: "v2", mode: "upsert" });
await new Promise(res => setTimeout(res, 10));
r = await tool(11, "write_note", { path: "Knowledge/collision-test.md", content: "v3", mode: "upsert" });
const trashAfter = await fs.readdir(`${VAULT}/.trash`);
const collisionBackups = trashAfter.filter(f => f.startsWith("collision-test_backup_"));
check("Multiple backups — timestamps prevent collision", collisionBackups.length === 2, `found ${collisionBackups.length} backups`);

console.log("\n── append_note: frontmatter guard on new-file creation ──────────");

// #10 regression: append_note used to silently create a frontmatter-less
// note if the path didn't exist yet, since it's a raw fs.appendFile with no
// concept of the note schema. Hit a real vault three times on the first
// journal append of a new day before this guard existed.

r = await tool(12, "append_note", { path: "Journal/Daily/no-frontmatter.md", content: "Just a log line, no frontmatter." });
d = parse(r);
check(
  "append_note — new file without frontmatter errors, doesn't create it",
  r.result?.isError && d.error?.includes("frontmatter"),
  `error: ${JSON.stringify(d.error)}`
);
const noFmExists = await fs.access(`${VAULT}/Journal/Daily/no-frontmatter.md`).then(() => true).catch(() => false);
check("append_note — rejected file was not created on disk", noFmExists === false);

r = await tool(13, "append_note", {
  path: "Journal/Daily/2026-08-02.md",
  content: "---\ntype: journal\nstatus: draft\nconfidence: high\nupdated: 2026-08-02\n---\n\n## Log\n\n- First entry.",
});
d = parse(r);
check("append_note — new file WITH frontmatter succeeds", d.appended === true);
const newJournal = await fs.readFile(`${VAULT}/Journal/Daily/2026-08-02.md`, "utf-8");
check("append_note — new file's content matches what was given", newJournal.includes("First entry."));

r = await tool(14, "append_note", { path: "Journal/Daily/2026-08-02.md", content: "- Second entry, no frontmatter needed this time." });
d = parse(r);
check("append_note — appending to an already-existing file never requires frontmatter", d.appended === true);
const appendedJournal = await fs.readFile(`${VAULT}/Journal/Daily/2026-08-02.md`, "utf-8");
check(
  "append_note — second append preserved the first entry (no overwrite)",
  appendedJournal.includes("First entry.") && appendedJournal.includes("Second entry")
);

// ── Results ───────────────────────────────────────────────────────────────────
server.kill();
await fs.rm(VAULT, { recursive: true, force: true });
console.log(`\n${"─".repeat(60)}`);
console.log(`  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
