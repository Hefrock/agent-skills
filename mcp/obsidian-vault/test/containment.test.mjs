// Containment tests for the obsidian-vault MCP server: the vault root given
// as a symlink, and read-side escapes (symlinks out of the vault, hidden
// paths, non-.md files, symlinked folders).
//
// Drives the compiled server over its real STDIO JSON-RPC transport, same as
// server.test.mjs. The server is started with OBSIDIAN_VAULT_PATH pointing at
// a SYMLINK to the real vault directory, so every assertion below also runs
// in the configuration that used to reject all writes.

import { spawn } from "child_process";
import { createInterface } from "readline";
import fs from "fs/promises";
import path from "path";
import os from "os";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.resolve(__dirname, "..", "dist", "index.js");

// ── Setup ─────────────────────────────────────────────────────────────────────
const BASE = await fs.mkdtemp(path.join(os.tmpdir(), "obsidian-vault-containment-"));
const VAULT = path.join(BASE, "real-vault");
const VAULT_LINK = path.join(BASE, "vault-link");
const OUTSIDE = path.join(BASE, "outside");
const SECRET = "outsidesecrettoken";

await fs.mkdir(`${VAULT}/Knowledge`, { recursive: true });
await fs.mkdir(`${VAULT}/.obsidian`, { recursive: true });
await fs.mkdir(OUTSIDE, { recursive: true });
await fs.symlink(VAULT, VAULT_LINK);

await fs.writeFile(`${VAULT}/Knowledge/plain.md`, "---\ntype: concept\n---\n# Plain\n\nordinary insidetoken content.");
await fs.writeFile(`${VAULT}/Knowledge/data.txt`, "not a note");
await fs.writeFile(`${VAULT}/.obsidian/app.json`, '{"hidden":"config"}');
await fs.writeFile(`${VAULT}/.obsidian/hidden.md`, "---\ntype: concept\n---\nhidden note");
await fs.writeFile(`${OUTSIDE}/secret.md`, `---\ntype: concept\n---\n${SECRET} lives outside the vault.`);
await fs.writeFile(`${OUTSIDE}/other.md`, "---\ntype: concept\n---\nanother outside note.");
// A symlinked note escaping the vault, a symlinked note staying inside it,
// and a symlinked folder escaping the vault.
await fs.symlink(`${OUTSIDE}/secret.md`, `${VAULT}/Knowledge/leak.md`);
await fs.symlink(`${VAULT}/Knowledge/plain.md`, `${VAULT}/Knowledge/alias.md`);
await fs.symlink(OUTSIDE, `${VAULT}/Escape`);

// ── Server harness ────────────────────────────────────────────────────────────
const server = spawn("node", [SERVER], {
  env: { ...process.env, OBSIDIAN_VAULT_PATH: VAULT_LINK },
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
const tool = (name, args) => rpc("tools/call", { name, arguments: args });
const text = (r) => r.result?.content?.[0]?.text ?? "";
const data = (r) => { try { return JSON.parse(text(r)); } catch { return {}; } };

await rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "test", version: "1" } });

let passed = 0, failed = 0;
function check(label, condition, detail = "") {
  if (condition) { console.log(`  PASS  ${label}`); passed++; }
  else { console.log(`  FAIL  ${label}${detail ? " — " + detail : ""}`); failed++; }
}

// ── Vault root given as a symlink ─────────────────────────────────────────────
console.log("\n── Vault root is a symlink: writes and reads work ───────────────");
let r = await tool("write_note", { path: "Knowledge/new.md", content: "---\ntype: concept\n---\n# New\n\nfirst.", mode: "create" });
check("symlinked root — write_note create succeeds", !r.result?.isError, text(r));
check(
  "symlinked root — the note landed in the real vault directory",
  await fs.readFile(`${VAULT}/Knowledge/new.md`, "utf-8").then((c) => c.includes("first."), () => false)
);
r = await tool("write_note", { path: "Brand/New/Folder/deep.md", content: "---\ntype: concept\n---\ndeep.", mode: "create" });
check("symlinked root — write into a not-yet-existing folder succeeds", !r.result?.isError, text(r));
r = await tool("append_note", { path: "Knowledge/new.md", content: "\nappended line." });
check("symlinked root — append_note succeeds", !r.result?.isError, text(r));
r = await tool("read_note", { path: "Knowledge/new.md" });
check("symlinked root — read_note returns what was written", !r.result?.isError && data(r).raw?.includes("appended line."), text(r));
r = await tool("write_note", { path: "../outside/escaped.md", content: "---\ntype: concept\n---\nx" });
check(
  "symlinked root — lexical traversal out of the vault is still refused on write",
  r.result?.isError && !(await fs.stat(`${OUTSIDE}/escaped.md`).catch(() => null)),
  text(r)
);
r = await tool("write_note", { path: "Escape/planted.md", content: "---\ntype: concept\n---\nx" });
check(
  "symlinked root — write through a symlinked folder escaping the vault is still refused",
  r.result?.isError && !(await fs.stat(`${OUTSIDE}/planted.md`).catch(() => null)),
  text(r)
);

// ── Read-side containment ─────────────────────────────────────────────────────
console.log("\n── read_note refuses what write_note refuses ────────────────────");
r = await tool("read_note", { path: "Knowledge/plain.md" });
check("read_note — an ordinary note still reads", !r.result?.isError && data(r).raw?.includes("insidetoken"), text(r));
r = await tool("read_note", { path: "Knowledge/alias.md" });
check("read_note — a symlink that stays inside the vault still reads", !r.result?.isError && data(r).raw?.includes("insidetoken"), text(r));
r = await tool("read_note", { path: "Knowledge/leak.md" });
check("read_note — a symlink resolving outside the vault is refused", r.result?.isError === true, text(r));
check("read_note — the outside file's content is not returned", !JSON.stringify(r).includes(SECRET));
r = await tool("read_note", { path: "Escape/secret.md" });
check("read_note — a note under a symlinked folder escaping the vault is refused", r.result?.isError === true && !JSON.stringify(r).includes(SECRET), text(r));
r = await tool("read_note", { path: ".obsidian/app.json" });
check("read_note — a hidden non-note file is refused", r.result?.isError === true && !JSON.stringify(r).includes("config"), text(r));
r = await tool("read_note", { path: ".obsidian/hidden.md" });
check("read_note — a .md under a hidden directory is refused", r.result?.isError === true && !text(r).includes("hidden note"), text(r));
r = await tool("read_note", { path: "Knowledge/data.txt" });
check("read_note — a non-.md file is refused", r.result?.isError === true && !text(r).includes("not a note"), text(r));
r = await tool("read_note", { path: "../outside/secret.md" });
check("read_note — lexical traversal is refused", r.result?.isError === true && !JSON.stringify(r).includes(SECRET), text(r));
r = await tool("read_note", { path: "Knowledge/does-not-exist.md" });
check("read_note — a missing note is an ordinary not-found error, not a traversal error", r.result?.isError === true && /ENOENT|no such file/i.test(text(r)), text(r));

console.log("\n── Scans do not surface content from outside the vault ──────────");
r = await tool("search_notes", { query: SECRET });
check("search_notes — the outside file's content is not indexed", !r.result?.isError && (data(r).results ?? []).length === 0 && !JSON.stringify(r).includes(`${SECRET} lives`), text(r));
r = await tool("search_notes", { query: "insidetoken" });
check("search_notes — in-vault notes are still found", (data(r).results ?? []).some((x) => x.path === "Knowledge/plain.md"), text(r));
r = await tool("list_notes", {});
let d = data(r);
check("list_notes — the escaping symlink is reported under skipped, not listed", !d.notes?.some((n) => n.path === "Knowledge/leak.md") && d.skipped?.some((s) => s.path === "Knowledge/leak.md"), text(r));
check("list_notes — in-vault notes (and the in-vault symlink) are still listed", d.notes?.some((n) => n.path === "Knowledge/plain.md") && d.notes?.some((n) => n.path === "Knowledge/alias.md"), text(r));
r = await tool("query_frontmatter", { field: "type", value: "concept" });
d = data(r);
check("query_frontmatter — the escaping symlink is not matched", !d.matches?.some((m) => m.path === "Knowledge/leak.md") && d.matches?.some((m) => m.path === "Knowledge/plain.md"), text(r));

console.log("\n── A symlinked folder cannot be used to enumerate outside ───────");
for (const name of ["list_notes", "search_notes", "query_frontmatter"]) {
  const args = name === "search_notes" ? { query: SECRET, folder: "Escape" }
    : name === "query_frontmatter" ? { field: "type", value: "concept", folder: "Escape" }
    : { folder: "Escape" };
  r = await tool(name, args);
  check(`${name} — folder that is a symlink out of the vault is refused`, r.result?.isError === true && !JSON.stringify(r).includes("other.md") && !JSON.stringify(r).includes(`${SECRET} lives`), text(r));
}
r = await tool("list_notes", { folder: "Knowledge" });
check("list_notes — an ordinary folder still lists", !r.result?.isError && data(r).notes?.some((n) => n.path === "Knowledge/plain.md"), text(r));

// ── Results ───────────────────────────────────────────────────────────────────
server.kill();
await fs.rm(BASE, { recursive: true, force: true });
console.log(`\n${"─".repeat(60)}`);
console.log(`  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
