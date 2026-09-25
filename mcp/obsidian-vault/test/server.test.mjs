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

console.log("\n── Vault-wide scans tolerate one malformed file ─────────────────");

// #11 regression: gray-matter throws when a file's frontmatter fence is
// malformed (e.g. `--- type: project` glued onto the opening line, which
// gray-matter misreads as a request for an unregistered custom parser
// engine). That used to abort search_notes/list_notes/query_frontmatter/
// list_links entirely — one bad file took down results for the whole vault,
// no matter how unrelated the query was to that file.
await fs.writeFile(
  `${VAULT}/Knowledge/malformed.md`,
  "--- type: project\nstatus: draft\ncreated: {{date}} ---\n\n# Malformed\n"
);

r = await tool(15, "list_notes", {});
d = parse(r);
check(
  "list_notes — doesn't error out when one file has malformed frontmatter",
  !r.result?.isError,
  `error: ${JSON.stringify(d.error)}`
);
check(
  "list_notes — good notes still returned despite the bad one",
  d.notes?.some((n) => n.path === "Knowledge/existing.md")
);
check(
  "list_notes — malformed file reported in `skipped`, not silently dropped",
  d.skipped?.some((s) => s.path === "Knowledge/malformed.md" && s.error?.includes("engine")),
  `skipped: ${JSON.stringify(d.skipped)}`
);

r = await tool(16, "search_notes", { query: "existing" });
d = parse(r);
check(
  "search_notes — doesn't error out when one file has malformed frontmatter",
  !r.result?.isError && Array.isArray(d.results),
  `error: ${JSON.stringify(d.error)}`
);
check(
  "search_notes — matching note still found despite the unrelated bad file",
  d.results?.some((res) => res.path === "Knowledge/existing.md")
);

r = await tool(17, "query_frontmatter", { field: "type", value: "concept" });
d = parse(r);
check(
  "query_frontmatter — doesn't error out when one file has malformed frontmatter",
  !r.result?.isError && Array.isArray(d.matches),
  `error: ${JSON.stringify(d.error)}`
);

r = await tool(18, "list_links", { path: "Knowledge/existing.md" });
d = parse(r);
check(
  "list_links — doesn't error out when one file has malformed frontmatter",
  !r.result?.isError,
  `error: ${JSON.stringify(d.error)}`
);

await fs.unlink(`${VAULT}/Knowledge/malformed.md`);

console.log("\n── list_links: folder-prefixed wikilinks register as backlinks ──");

// Regression: listLinks() computed the target's own name via
// path.basename(notePath, ".md") (a bare filename, no folder) but compared it
// against the *raw*, unstripped bracket text of every other note's outbound
// links. A link written as [[Projects/Target]] (folder-prefixed) never equaled
// bare "Target", so every prefixed-style link silently failed to register as
// an inbound backlink -- even though the same link correctly appeared in the
// linking note's own *outbound* list. Found 2026-09-15 auditing a real vault:
// every Projects/ page showed zero inbound despite being referenced by name
// from half a dozen Knowledge/ pages, all via the folder-prefixed form.
await fs.mkdir(`${VAULT}/Projects`, { recursive: true });
await fs.writeFile(
  `${VAULT}/Projects/Target Project.md`,
  "---\ntype: project\nstatus: draft\n---\n# Target Project\n"
);
await fs.writeFile(
  `${VAULT}/Knowledge/prefixed-linker.md`,
  "---\ntype: concept\nstatus: draft\n---\n# Prefixed Linker\n\nSee [[Projects/Target Project]] for details.\n"
);
await fs.writeFile(
  `${VAULT}/Knowledge/bare-linker.md`,
  "---\ntype: concept\nstatus: draft\n---\n# Bare Linker\n\nSee [[Target Project]] for details.\n"
);

r = await tool(19, "list_links", { path: "Projects/Target Project.md" });
d = parse(r);
check(
  "list_links — a folder-prefixed inbound link ([[Projects/Target Project]]) registers as a backlink",
  d.inbound?.includes("Knowledge/prefixed-linker.md"),
  `inbound: ${JSON.stringify(d.inbound)}`
);
check(
  "list_links — a bare inbound link ([[Target Project]]) still registers as a backlink",
  d.inbound?.includes("Knowledge/bare-linker.md"),
  `inbound: ${JSON.stringify(d.inbound)}`
);

r = await tool(20, "list_links", { path: "Knowledge/prefixed-linker.md" });
d = parse(r);
check(
  "list_links — the prefixed link still appears in its own note's outbound list (this direction always worked)",
  d.outbound?.includes("Projects/Target Project")
);

console.log("\n── BM25: IDF — a rare term beats a common term repeated ────────");

// A term that appears in nearly every note (low IDF) shouldn't outscore a
// term that appears in almost none of them (high IDF), even at 50x the raw
// frequency. This is the textbook property a flat match-count scorer (the
// old implementation) doesn't have at all.
for (let i = 0; i < 8; i++) {
  await fs.writeFile(
    `${VAULT}/Knowledge/idf-filler-${i}.md`,
    `---\ntype: concept\n---\nThis filler note mentions commonzword several times: commonzword commonzword commonzword.`
  );
}
await fs.writeFile(`${VAULT}/Knowledge/idf-rare.md`, "---\ntype: concept\n---\nThis note mentions rarezword exactly once.");
await fs.writeFile(
  `${VAULT}/Knowledge/idf-common.md`,
  "---\ntype: concept\n---\n" + "commonzword ".repeat(50)
);

r = await tool(21, "search_notes", { query: "rarezword commonzword" });
d = parse(r);
const rareHit = d.results?.find((x) => x.path.includes("idf-rare"));
const commonHit = d.results?.find((x) => x.path.includes("idf-common"));
check(
  "IDF — one hit on a rare term outranks 50 hits on a common one",
  rareHit !== undefined && commonHit !== undefined && rareHit.score > commonHit.score,
  `rare=${rareHit?.score} common=${commonHit?.score}`
);

console.log("\n── BM25: length normalization — equal tf, shorter note wins ────");

const padding = Array.from({ length: 200 }, (_, i) => `paddingword${i}`).join(" ");
await fs.writeFile(`${VAULT}/Knowledge/len-short.md`, "---\ntype: concept\n---\nAbout distinctiveqterm only.");
await fs.writeFile(`${VAULT}/Knowledge/len-long.md`, `---\ntype: concept\n---\nAbout distinctiveqterm too. ${padding}`);

r = await tool(22, "search_notes", { query: "distinctiveqterm" });
d = parse(r);
const shortHit = d.results?.find((x) => x.path.includes("len-short"));
const longHit = d.results?.find((x) => x.path.includes("len-long"));
check(
  "length normalization — same raw term frequency, shorter body ranks higher",
  shortHit !== undefined && longHit !== undefined && shortHit.score > longHit.score,
  `short=${shortHit?.score} long=${longHit?.score}`
);

console.log("\n── BM25: word boundaries — token match, not substring ──────────");

await fs.writeFile(`${VAULT}/Knowledge/boundary-fixture.md`, "---\ntype: concept\n---\nMy heart will start beating.");

r = await tool(23, "search_notes", { query: "art" });
d = parse(r);
check(
  "word boundaries — query 'art' does not match a note containing only 'heart'/'start'",
  !d.results?.some((x) => x.path.includes("boundary-fixture")),
  `results: ${JSON.stringify(d.results?.map((x) => x.path))}`
);

console.log("\n── BM25: field weights — title match beats one body occurrence ─");

await fs.writeFile(`${VAULT}/Knowledge/keystonewordq.md`, "---\ntype: concept\n---\nNo mention of that term here at all.");
await fs.writeFile(`${VAULT}/Knowledge/other-body-holder.md`, "---\ntype: concept\n---\nThis note mentions keystonewordq one time.");

r = await tool(24, "search_notes", { query: "keystonewordq" });
d = parse(r);
const titleHit = d.results?.find((x) => x.path.includes("keystonewordq"));
const bodyHit = d.results?.find((x) => x.path.includes("other-body-holder"));
check(
  "field weights — a title match outranks the same term once in a body",
  titleHit !== undefined && bodyHit !== undefined && titleHit.score > bodyHit.score,
  `title=${titleHit?.score} body=${bodyHit?.score}`
);

console.log("\n── Cache invalidation: write_note and delete_note ───────────────");

r = await tool(25, "search_notes", { query: "unwrittenyetterm" });
d = parse(r);
check("cache invalidation — term absent before the note exists", !d.results?.some((x) => x.path.includes("cache-write-fixture")));

r = await tool(26, "write_note", {
  path: "Knowledge/cache-write-fixture.md",
  content: "---\ntype: concept\n---\nThis contains unwrittenyetterm.",
  mode: "create",
});
check("cache invalidation — write_note succeeded", parse(r).written === true);

r = await tool(27, "search_notes", { query: "unwrittenyetterm" });
d = parse(r);
check(
  "cache invalidation — search immediately after write_note finds the new note",
  d.results?.some((x) => x.path.includes("cache-write-fixture")),
  `results: ${JSON.stringify(d.results?.map((x) => x.path))}`
);

r = await tool(28, "delete_note", { path: "Knowledge/cache-write-fixture.md" });
check("cache invalidation — delete_note succeeded", parse(r).moved_to !== undefined);

r = await tool(29, "search_notes", { query: "unwrittenyetterm" });
d = parse(r);
check(
  "cache invalidation — search immediately after delete_note no longer finds it",
  !d.results?.some((x) => x.path.includes("cache-write-fixture")),
  `results: ${JSON.stringify(d.results?.map((x) => x.path))}`
);

console.log("\n── Cache sync: an edit made outside MCP is picked up ────────────");

await fs.writeFile(`${VAULT}/Knowledge/external-edit-fixture.md`, "---\ntype: concept\n---\nOriginal content, no special term.");

r = await tool(30, "search_notes", { query: "externallyeditedterm" });
d = parse(r);
check("external edit — term absent before the direct edit", !d.results?.some((x) => x.path.includes("external-edit-fixture")));

// Bypass MCP entirely: write straight to disk, then force the mtime forward
// so this test exercises syncIndex's mtime/size diff even on a filesystem
// with coarse mtime resolution, not invalidateCacheEntry (which only fires
// on writes made THROUGH this server).
await fs.writeFile(`${VAULT}/Knowledge/external-edit-fixture.md`, "---\ntype: concept\n---\nNow mentions externallyeditedterm directly.");
const future = new Date(Date.now() + 5000);
await fs.utimes(`${VAULT}/Knowledge/external-edit-fixture.md`, future, future);

r = await tool(31, "search_notes", { query: "externallyeditedterm" });
d = parse(r);
check(
  "external edit — a direct on-disk edit (bypassing MCP) is reflected on the next search",
  d.results?.some((x) => x.path.includes("external-edit-fixture")),
  `results: ${JSON.stringify(d.results?.map((x) => x.path))}`
);

console.log("\n── Folder scoping: restricted results, unrestricted-comparable score ─");

await fs.mkdir(`${VAULT}/Knowledge`, { recursive: true });
await fs.writeFile(`${VAULT}/Knowledge/scoped-in-folder.md`, "---\ntype: concept\n---\nThis mentions scopedqterm once.");
await fs.writeFile(`${VAULT}/Projects/scoped-outside-folder.md`, "---\ntype: project\n---\nThis also mentions scopedqterm once.");

r = await tool(32, "search_notes", { query: "scopedqterm" });
d = parse(r);
const unrestrictedHit = d.results?.find((x) => x.path === "Knowledge/scoped-in-folder.md");

r = await tool(33, "search_notes", { query: "scopedqterm", folder: "Knowledge" });
d = parse(r);
check(
  "folder scoping — results restricted to the folder exclude a match outside it",
  d.results?.some((x) => x.path === "Knowledge/scoped-in-folder.md") &&
    !d.results?.some((x) => x.path === "Projects/scoped-outside-folder.md"),
  `results: ${JSON.stringify(d.results?.map((x) => x.path))}`
);
const scopedHit = d.results?.find((x) => x.path === "Knowledge/scoped-in-folder.md");
check(
  "folder scoping — the in-folder note's score is unchanged from the unrestricted query (IDF stays vault-wide)",
  unrestrictedHit !== undefined && scopedHit !== undefined && unrestrictedHit.score === scopedHit.score,
  `unrestricted=${unrestrictedHit?.score} scoped=${scopedHit?.score}`
);

r = await tool(34, "search_notes", { query: "scopedqterm", folder: "NoSuchFolder" });
check(
  "folder scoping — a nonexistent folder errors rather than silently returning zero results",
  r.result?.isError,
  `result: ${JSON.stringify(r.result)}`
);

// Test 7 (a note with broken frontmatter still appears in `skipped` and
// doesn't break search) is already covered above by the "Vault-wide scans
// tolerate one malformed file" section — search_notes there is asserted to
// still return valid results and list the bad file in `skipped`, which is
// the same property this item asks for. Not duplicated here.
//
// Test 9 (existing tests, including the ReDoS regression, still pass) is the
// whole file: everything above this new section is unchanged from before the
// BM25 rewrite and is asserted to still pass in the same run.

// ── Results ───────────────────────────────────────────────────────────────────
server.kill();
await fs.rm(VAULT, { recursive: true, force: true });
console.log(`\n${"─".repeat(60)}`);
console.log(`  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
