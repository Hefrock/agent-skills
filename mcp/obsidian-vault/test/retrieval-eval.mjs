// Retrieval eval: recall@5 and MRR for search_notes against a REAL vault,
// using a hand-written query set with known-correct note paths. This is
// intentionally NOT run in CI and has no fixtures of its own — it exists to
// be run locally, by a human who can judge whether a result is actually
// right, against a real vault whose content nobody else has.
//
// Usage:
//   OBSIDIAN_VAULT_PATH=/path/to/real/vault node test/retrieval-eval.mjs /path/to/queries.json
//
// Run it once on a build from before a search_notes change and once after,
// and compare the two reports -- that comparison is the actual acceptance
// check (see the PR description this ships with), not anything this script
// decides on its own.
//
// Queries file format (an array; keep it OUTSIDE this repo, e.g.
// ~/.local/share/vault-eval/queries.json -- never commit real vault paths
// or queries here):
//   [
//     { "query": "transformer attention mechanism",
//       "expected": ["Knowledge/attention-is-all-you-need.md"] },
//     ...
//   ]
// `expected` is a list because more than one note can legitimately answer a
// query; a query counts as a hit if ANY expected path appears in the
// results.

import { spawn } from "child_process";
import { createInterface } from "readline";
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.resolve(__dirname, "..", "dist", "index.js");

const queriesPath = process.argv[2];
if (!queriesPath) {
  console.error("Usage: OBSIDIAN_VAULT_PATH=/path/to/vault node test/retrieval-eval.mjs /path/to/queries.json");
  process.exit(1);
}
if (!process.env.OBSIDIAN_VAULT_PATH) {
  console.error("OBSIDIAN_VAULT_PATH must be set to the real vault to evaluate against.");
  process.exit(1);
}

const RANK_DEPTH = 25; // ask for more than 5 so MRR can find a hit ranked below 5, not just report "miss"
const RECALL_AT = 5;

let queries;
try {
  queries = JSON.parse(await fs.readFile(queriesPath, "utf-8"));
} catch (err) {
  console.error(`Failed to read/parse queries file ${queriesPath}: ${err.message}`);
  process.exit(1);
}
if (!Array.isArray(queries) || queries.length === 0) {
  console.error(`Queries file must be a non-empty JSON array; got: ${JSON.stringify(queries).slice(0, 100)}`);
  process.exit(1);
}

const server = spawn("node", [SERVER], {
  env: { ...process.env },
  stdio: ["pipe", "pipe", "pipe"],
});
const pending = new Map();
createInterface({ input: server.stdout }).on("line", (line) => {
  try {
    const msg = JSON.parse(line);
    if (msg.id !== undefined && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  } catch {}
});
let serverErr = "";
server.stderr.on("data", (d) => { serverErr += d.toString(); });

function rpc(id, method, params) {
  return new Promise((resolve) => { pending.set(id, resolve); server.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n"); });
}

await rpc(0, "initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "retrieval-eval", version: "1" } });

let recallHits = 0;
let reciprocalRankSum = 0;
const rows = [];

for (let i = 0; i < queries.length; i++) {
  const { query, expected } = queries[i];
  if (typeof query !== "string" || !Array.isArray(expected) || expected.length === 0) {
    console.error(`Skipping malformed entry at index ${i}: ${JSON.stringify(queries[i])}`);
    continue;
  }
  const r = await rpc(i + 1, "tools/call", { name: "search_notes", arguments: { query, limit: RANK_DEPTH } });
  const parsed = JSON.parse(r.result?.content?.[0]?.text ?? "{}");
  if (r.result?.isError) {
    console.error(`Query ${i} ("${query}") errored: ${parsed.error}`);
    rows.push({ query, rank: null, hit5: false });
    continue;
  }
  const paths = (parsed.results ?? []).map((res) => res.path);
  let rank = null;
  for (const exp of expected) {
    const idx = paths.indexOf(exp);
    if (idx !== -1 && (rank === null || idx + 1 < rank)) rank = idx + 1; // best (lowest) rank across all acceptable paths
  }
  const hit5 = rank !== null && rank <= RECALL_AT;
  if (hit5) recallHits++;
  if (rank !== null) reciprocalRankSum += 1 / rank;
  rows.push({ query, rank, hit5 });
}

server.kill();

const n = rows.length;
const recallAt5 = n > 0 ? recallHits / n : 0;
const mrr = n > 0 ? reciprocalRankSum / n : 0;

console.log(`\nRetrieval eval — ${n} queries against ${process.env.OBSIDIAN_VAULT_PATH}\n`);
for (const row of rows) {
  const status = row.rank === null ? "MISS (not in top " + RANK_DEPTH + ")" : row.hit5 ? `HIT  rank ${row.rank}` : `rank ${row.rank} (below top ${RECALL_AT})`;
  console.log(`  ${status.padEnd(28)} ${row.query}`);
}
console.log(`\nrecall@${RECALL_AT}: ${recallAt5.toFixed(3)}  (${recallHits}/${n})`);
console.log(`MRR:        ${mrr.toFixed(3)}`);
if (serverErr.trim()) console.error(`\n(server stderr during the run:\n${serverErr.trim()}\n)`);
