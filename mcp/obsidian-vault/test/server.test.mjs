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

console.log("\n── Multiple backups don't collide ───────────────────────────────");

r = await tool(9, "write_note", { path: "Knowledge/collision-test.md", content: "v1", mode: "upsert" });
await new Promise(res => setTimeout(res, 10));
r = await tool(10, "write_note", { path: "Knowledge/collision-test.md", content: "v2", mode: "upsert" });
await new Promise(res => setTimeout(res, 10));
r = await tool(11, "write_note", { path: "Knowledge/collision-test.md", content: "v3", mode: "upsert" });
const trashAfter = await fs.readdir(`${VAULT}/.trash`);
const collisionBackups = trashAfter.filter(f => f.startsWith("collision-test_backup_"));
check("Multiple backups — timestamps prevent collision", collisionBackups.length === 2, `found ${collisionBackups.length} backups`);

// ── Results ───────────────────────────────────────────────────────────────────
server.kill();
await fs.rm(VAULT, { recursive: true, force: true });
console.log(`\n${"─".repeat(60)}`);
console.log(`  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
