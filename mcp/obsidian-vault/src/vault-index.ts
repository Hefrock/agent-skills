// The vault's live BM25 search index: a module-level cache kept in sync with
// disk, plus the query-time helpers built on top of it. Separated from
// index.ts (which is tool registration and wiring only, per the project's own
// working agreement) so warehouse.ts can resolve a doc_id to its vault Source
// note (findNoteByDocId) without importing index.ts and creating a circular
// dependency between "the module that registers warehouse tools" and "the
// module the warehouse tools need to call into".
import path from "path";
import fs from "fs/promises";
import { BM25Index, tokenize } from "./bm25.js";
import { vaultRoot, vaultPath, walkVault, tryReadNote } from "./vault-io.js";

// Field weights match the old scorer's boosts (title 5x, tags 3x, body 1x),
// now used as BM25F per-field term-frequency multipliers instead of flat
// match-count multipliers — see bm25.ts for what that means precisely.
export const bm25 = new BM25Index({ title: 5, tags: 3, body: 1 });

interface CachedNote {
  mtimeMs: number;
  size: number;
  frontmatter: Record<string, unknown>;
  body: string;
}
export const noteCache = new Map<string, CachedNote>();

function tokenizeFields(notePath: string, frontmatter: Record<string, unknown>, body: string) {
  const title = path.basename(notePath, ".md");
  const tags = Array.isArray(frontmatter.tags) ? frontmatter.tags.join(" ") : "";
  return { title: tokenize(title), tags: tokenize(tags), body: tokenize(body) };
}

// Walks the vault, stats every file, and reparses only what changed since the
// last sync (by mtimeMs OR size — either changing means content may have
// changed). Deleted files are dropped from both the cache and the BM25 index.
// Called at the top of every search so results are always current; cheap when
// nothing changed (a stat per file, no reads), matching the cost profile the
// handoff asked for. Explicit invalidation on writes (see invalidateCacheEntry)
// is a SEPARATE, faster-than-mtime-resolution mechanism for the server's own
// writes — this sync is the fallback that also catches edits made outside MCP.
export async function syncIndex(): Promise<{ skipped: { path: string; error: string }[] }> {
  const allFiles = await walkVault();
  const current = new Set(allFiles);
  const skipped: { path: string; error: string }[] = [];

  for (const cachedPath of [...noteCache.keys()]) {
    if (!current.has(cachedPath)) {
      noteCache.delete(cachedPath);
      bm25.remove(cachedPath);
    }
  }

  for (const file of allFiles) {
    let stat;
    try {
      stat = await fs.stat(vaultPath(file));
    } catch {
      continue; // vanished between walk and stat; next sync's deletion pass catches it
    }
    const cached = noteCache.get(file);
    if (cached && cached.mtimeMs === stat.mtimeMs && cached.size === stat.size) continue;

    const note = await tryReadNote(file);
    if (!note.ok) {
      skipped.push({ path: file, error: note.error });
      noteCache.delete(file);
      bm25.remove(file);
      continue;
    }
    noteCache.set(file, { mtimeMs: stat.mtimeMs, size: stat.size, frontmatter: note.frontmatter, body: note.body });
    bm25.upsert(file, tokenizeFields(file, note.frontmatter, note.body));
  }

  return { skipped };
}

// Every write tool calls this immediately after a successful write/delete.
// Not just belt-and-suspenders alongside syncIndex's mtime check: mtime
// resolution on some filesystems is coarse enough that a write immediately
// followed by a search can land in the same tick, making the stat-diff in
// syncIndex miss the change. This invalidation doesn't depend on mtime at
// all. A no-op if the path was never indexed (safe to call unconditionally).
export function invalidateCacheEntry(notePath: string): void {
  noteCache.delete(notePath);
  bm25.remove(notePath);
}

export function withinFolder(notePath: string, folderRel: string): boolean {
  const rel = path.relative(folderRel, notePath);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

// Body line containing the most DISTINCT query tokens (token match, not
// substring — "art" no longer matches an excerpt line just because it
// contains "heart"), falling back to the first non-empty line.
export function selectExcerpt(body: string, queryTerms: string[]): string {
  const lines = body.split("\n");
  let best: { line: string; count: number } | null = null;
  for (const line of lines) {
    const lineTokens = new Set(tokenize(line));
    const count = queryTerms.filter((t) => lineTokens.has(t)).length;
    if (count > 0 && (!best || count > best.count)) best = { line, count };
  }
  const chosen = best?.line ?? lines.find((l) => l.trim() !== "") ?? "";
  return chosen.trim().slice(0, 150);
}

// For the warehouse's search_warehouse tool: which vault Source note (if any)
// carries this exact doc_id in its frontmatter? `docId` must already be in
// the "sha256:<hex>" form vault frontmatter actually stores (see
// references/warehouse-schema.md) — callers normalize before calling this.
// Ensures the index is current first, same as a real search would.
export async function findNoteByDocId(docId: string): Promise<string | null> {
  await syncIndex();
  for (const [notePath, cached] of noteCache) {
    if (cached.frontmatter.doc_id === docId) return notePath;
  }
  return null;
}
