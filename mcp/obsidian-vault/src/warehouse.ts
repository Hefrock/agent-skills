// Read-only passage retrieval over knowledge-warehouse full text. Optional:
// if WAREHOUSE_PATH is unset, this module's tools are never registered and
// nothing here runs. If set but invalid (missing directory or manifest.json),
// this logs to stderr and leaves the tools unregistered -- it never exits the
// process, since the vault tools must keep working regardless.
import fs from "fs/promises";
import path from "path";
import crypto from "crypto";
import { BM25Index, tokenize } from "./bm25.js";
import { resolveContained } from "./paths.js";
import { chunkText, buildOffsetMaps } from "./chunker.js";
import { findNoteByDocId } from "./vault-index.js";

interface ManifestEntry {
  title: string;
  raw_path: string;
  text_path: string;
  ext: string;
  bytes: number;
  char_count: number;
  extraction_method: string;
  ingested: string;
  source_url?: string;
}
// Keyed by bare hex sha256 (references/warehouse-schema.md's convention).
// Structural typing already tolerates any extra/unrecognized fields an entry
// carries beyond what's declared above -- nothing here errors on them.
type Manifest = Record<string, ManifestEntry>;

let warehouseRoot: string | null = null;
const WAREHOUSE_PATH = process.env.WAREHOUSE_PATH;
if (WAREHOUSE_PATH) {
  const resolved = path.resolve(WAREHOUSE_PATH);
  try {
    const stat = await fs.stat(resolved);
    if (!stat.isDirectory()) {
      console.error(`WAREHOUSE_PATH is not a directory: ${resolved} -- warehouse tools disabled.`);
    } else {
      await fs.access(path.join(resolved, "manifest.json"));
      warehouseRoot = resolved;
    }
  } catch (err) {
    console.error(
      `WAREHOUSE_PATH set but invalid (${resolved}): ${err instanceof Error ? err.message : String(err)} -- warehouse tools disabled.`
    );
  }
}

export function isWarehouseAvailable(): boolean {
  return warehouseRoot !== null;
}

function sha256Hex(s: string): string {
  return crypto.createHash("sha256").update(s, "utf-8").digest("hex");
}

function normalizeDocId(id: string): string {
  return id.startsWith("sha256:") ? id.slice("sha256:".length) : id;
}

// ── Live index: manifest + per-document passages, kept in sync lazily ───────

interface IndexedPassage {
  docId: string; // bare hex
  title: string;
  textPath: string;
  charStart: number;
  charEnd: number;
  text: string;
  hash: string; // bare hex sha256 of `text`
}

let manifest: Manifest = {};
let manifestMtimeMs = -1;
const passages = new Map<string, IndexedPassage>(); // key: `${docId}#${passageIndex}`
const textFileMtimes = new Map<string, number>(); // docId -> last-indexed mtimeMs

// title:body, per the handoff -- title boosted but less dominant than an
// exact vault-note-title match (bm25's title weight is 5); 3 mirrors the
// vault index's own tags weight as a reasonable "boosted, not dominant" value
// since the handoff doesn't pin an exact number for this field.
const warehouseBm25 = new BM25Index({ title: 3, body: 1 });

// Re-reads manifest.json if it changed, then re-chunks and re-indexes only
// the text files whose mtime changed since last sync -- same change-detection
// shape as the vault's syncIndex, and the same reason: cheap on repeat calls,
// and catches a re-extraction (intake.py --force rewrites text_path in
// place, confirmed in production -- see references/data-sources.md) without
// needing a server restart. Called at the top of every warehouse tool call;
// the first call after startup is what does the actual (lazy) initial build.
async function syncWarehouseIndex(): Promise<void> {
  if (!warehouseRoot) return;
  const manifestPath = path.join(warehouseRoot, "manifest.json");
  const stat = await fs.stat(manifestPath).catch(() => null);
  if (!stat) {
    console.error(`warehouse manifest.json disappeared: ${manifestPath}`);
    return;
  }

  if (stat.mtimeMs !== manifestMtimeMs) {
    try {
      manifest = JSON.parse(await fs.readFile(manifestPath, "utf-8"));
      manifestMtimeMs = stat.mtimeMs;
    } catch (err) {
      console.error(`failed to parse warehouse manifest.json: ${err instanceof Error ? err.message : String(err)}`);
      return; // keep the previous (last-good) index rather than wiping it on a transient bad read
    }
  }

  const currentDocIds = new Set(Object.keys(manifest));
  for (const key of [...passages.keys()]) {
    if (!currentDocIds.has(key.split("#")[0])) {
      passages.delete(key);
      warehouseBm25.remove(key);
    }
  }
  for (const docId of [...textFileMtimes.keys()]) {
    if (!currentDocIds.has(docId)) textFileMtimes.delete(docId);
  }

  for (const [docId, entry] of Object.entries(manifest)) {
    let resolvedTextPath: string;
    try {
      // Containment check ONLY -- raw_path is never opened, per spec.
      resolvedTextPath = await resolveContained(warehouseRoot, entry.text_path);
    } catch (err) {
      console.error(`warehouse entry ${docId} rejected: ${err instanceof Error ? err.message : String(err)}`);
      continue; // a malformed/malicious entry must not stop the rest of the corpus from indexing
    }
    const textStat = await fs.stat(resolvedTextPath).catch(() => null);
    if (!textStat) {
      console.error(`warehouse entry ${docId}: text_path not found: ${entry.text_path}`);
      continue;
    }
    if (textFileMtimes.get(docId) === textStat.mtimeMs) continue; // unchanged since last sync

    for (const key of [...passages.keys()]) {
      if (key.startsWith(`${docId}#`)) {
        passages.delete(key);
        warehouseBm25.remove(key);
      }
    }

    const text = await fs.readFile(resolvedTextPath, "utf-8");
    chunkText(text, entry.extraction_method).forEach((chunk, i) => {
      const key = `${docId}#${i}`;
      const passage: IndexedPassage = {
        docId, title: entry.title, textPath: entry.text_path,
        charStart: chunk.charStart, charEnd: chunk.charEnd,
        text: chunk.text, hash: sha256Hex(chunk.text),
      };
      passages.set(key, passage);
      warehouseBm25.upsert(key, { title: tokenize(entry.title), body: tokenize(chunk.text) });
    });
    textFileMtimes.set(docId, textStat.mtimeMs);
  }
}

// ── Tools ─────────────────────────────────────────────────────────────────────

const DEFAULT_LIMIT = 8;
const MAX_LIMIT = 25;
const MAX_HITS_PER_DOC = 3;

export async function searchWarehouse(query: string, limit: number | undefined, docIdFilter: string | undefined): Promise<object> {
  await syncWarehouseIndex();
  const cappedLimit = Math.max(1, Math.min(limit ?? DEFAULT_LIMIT, MAX_LIMIT));
  const terms = tokenize(query);
  const filterDocId = docIdFilter ? normalizeDocId(docIdFilter) : undefined;

  const scored: { key: string; score: number }[] = [];
  for (const key of passages.keys()) {
    if (filterDocId && !key.startsWith(`${filterDocId}#`)) continue;
    const score = warehouseBm25.score(key, terms);
    if (score > 0) scored.push({ key, score });
  }
  scored.sort((a, b) => b.score - a.score);

  // Cap hits per document so one long document can't monopolize results --
  // applied before the overall limit, so the limit slices the already-capped,
  // score-ordered list (not the raw per-passage list).
  const perDocCount = new Map<string, number>();
  const capped: { key: string; score: number }[] = [];
  for (const s of scored) {
    const docId = passages.get(s.key)!.docId;
    const count = perDocCount.get(docId) ?? 0;
    if (count >= MAX_HITS_PER_DOC) continue;
    perDocCount.set(docId, count + 1);
    capped.push(s);
  }

  const hits = [];
  for (const { key, score } of capped.slice(0, cappedLimit)) {
    const p = passages.get(key)!;
    hits.push({
      doc_id: `sha256:${p.docId}`,
      title: p.title,
      text_path: p.textPath,
      char_start: p.charStart,
      char_end: p.charEnd,
      offset_unit: "codepoint",
      passage_hash: `sha256:${p.hash}`,
      score,
      passage: p.text,
      source_note: await findNoteByDocId(`sha256:${p.docId}`),
    });
  }
  return { hits, total: capped.length };
}

const READ_CAP = 8000; // code points, per call

export async function readWarehouseText(docIdInput: string, charStart: number, charEnd: number): Promise<object> {
  await syncWarehouseIndex();
  if (!warehouseRoot) throw new Error("warehouse not configured");
  const docId = normalizeDocId(docIdInput);
  const entry = manifest[docId];
  if (!entry) throw new Error(`Unknown doc_id: ${docIdInput}`);

  const resolvedTextPath = await resolveContained(warehouseRoot, entry.text_path);
  const text = await fs.readFile(resolvedTextPath, "utf-8");
  const maps = buildOffsetMaps(text);
  const totalCps = maps.u16ToCp[text.length];

  const clampedStart = Math.max(0, Math.min(charStart, totalCps));
  let clampedEnd = Math.max(clampedStart, Math.min(charEnd, totalCps));
  if (clampedEnd - clampedStart > READ_CAP) clampedEnd = clampedStart + READ_CAP;

  const span = text.slice(maps.cpToU16[clampedStart], maps.cpToU16[clampedEnd]);
  return {
    doc_id: `sha256:${docId}`,
    char_start: clampedStart,
    char_end: clampedEnd,
    offset_unit: "codepoint",
    text: span,
    text_hash: `sha256:${sha256Hex(span)}`,
  };
}
