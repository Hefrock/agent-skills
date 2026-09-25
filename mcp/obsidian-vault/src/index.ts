#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fs from "fs/promises";
import path from "path";
import matter from "gray-matter";
import { tokenize } from "./bm25.js";
import { vaultRoot, vaultPath, vaultPathForWrite, readNote, tryReadNote, atomicWrite, walkVault, extractWikilinks } from "./vault-io.js";
import { bm25, noteCache, syncIndex, invalidateCacheEntry, withinFolder, selectExcerpt } from "./vault-index.js";
import * as warehouse from "./warehouse.js";

// ── Arg validation helpers ────────────────────────────────────────────────────

// #5 — replace unsafe `as` casts with runtime checks
function requireString(args: Record<string, unknown>, key: string): string {
  const val = args[key];
  if (typeof val !== "string" || val.trim() === "") {
    throw new Error(`Missing or invalid required argument: "${key}"`);
  }
  return val;
}

function requireObject(args: Record<string, unknown>, key: string): Record<string, unknown> {
  const val = args[key];
  if (typeof val !== "object" || val === null || Array.isArray(val)) {
    throw new Error(`Missing or invalid required argument: "${key}" (expected object)`);
  }
  return val as Record<string, unknown>;
}

function optionalString(args: Record<string, unknown>, key: string): string | undefined {
  const val = args[key];
  if (val === undefined || val === null) return undefined;
  if (typeof val !== "string") throw new Error(`Invalid type for argument "${key}" (expected string)`);
  return val || undefined;
}

function optionalNumber(args: Record<string, unknown>, key: string): number | undefined {
  const val = args[key];
  if (val === undefined || val === null) return undefined;
  if (typeof val !== "number") throw new Error(`Invalid type for argument "${key}" (expected number)`);
  return val;
}

function requireNumber(args: Record<string, unknown>, key: string): number {
  const val = args[key];
  if (typeof val !== "number") throw new Error(`Missing or invalid required argument: "${key}" (expected number)`);
  return val;
}

// ── Tool implementations ──────────────────────────────────────────────────────

async function searchNotes(query: string, folder?: string, limit = 10): Promise<object> {
  const { skipped } = await syncIndex();
  const terms = tokenize(query);
  let folderRel: string | undefined;
  if (folder) {
    const resolved = vaultPath(folder);
    // Folder scoping used to walk the folder directly (fs.readdir), which
    // threw on a nonexistent path -- now that the whole vault is always
    // walked and folder scoping is a path-prefix filter instead, that same
    // typo would otherwise silently return zero results rather than erroring.
    const stat = await fs.stat(resolved).catch(() => null);
    if (!stat || !stat.isDirectory()) {
      throw new Error(`Folder not found: ${folder}`);
    }
    folderRel = path.relative(vaultRoot, resolved);
  }

  const results: { path: string; score: number; excerpt: string; frontmatter: Record<string, unknown> }[] = [];
  for (const notePath of noteCache.keys()) {
    if (folderRel && !withinFolder(notePath, folderRel)) continue;
    const score = bm25.score(notePath, terms);
    if (score > 0) {
      const cached = noteCache.get(notePath)!;
      results.push({ path: notePath, score, excerpt: selectExcerpt(cached.body, terms), frontmatter: cached.frontmatter });
    }
  }
  results.sort((a, b) => b.score - a.score);

  const relevantSkipped = folderRel ? skipped.filter((s) => withinFolder(s.path, folderRel)) : skipped;
  return { results: results.slice(0, limit), total: results.length, ...(relevantSkipped.length > 0 ? { skipped: relevantSkipped } : {}) };
}

async function writeNoteContents(notePath: string, content: string, mode: string = "upsert"): Promise<object> {
  const full = await vaultPathForWrite(notePath);
  const exists = await fs.access(full).then(() => true).catch(() => false);

  if (mode === "create" && exists) {
    throw new Error(`File already exists: ${notePath}. Use mode "update" or "upsert" to overwrite.`);
  }
  if (mode === "update" && !exists) {
    throw new Error(`File does not exist: ${notePath}. Use mode "create" or "upsert" to create it.`);
  }

  // Back up existing file to .trash/ before overwriting
  if (exists) {
    const trashDir = path.join(vaultRoot, ".trash");
    await fs.mkdir(trashDir, { recursive: true });
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const trashName = `${path.basename(notePath, ".md")}_backup_${timestamp}.md`;
    await fs.copyFile(full, path.join(trashDir, trashName));
  }

  await atomicWrite(full, content);
  invalidateCacheEntry(notePath);
  return { path: notePath, written: true, backed_up: exists };
}

// #1 — dedicated append tool so callers never accidentally clobber existing files
async function appendNoteContents(notePath: string, content: string): Promise<object> {
  const full = await vaultPathForWrite(notePath);
  const exists = await fs.access(full).then(() => true).catch(() => false);

  // #10 - append_note creates the file if it doesn't exist, but has no
  // concept of frontmatter of its own: a raw fs.appendFile on a missing
  // path just writes `content` verbatim. Every note needs type/status/
  // confidence/updated frontmatter (see the vault's note schema) - if the
  // caller is creating a brand-new file here without a frontmatter block,
  // that's a malformed note being created silently. Refuse it instead:
  // the caller should use write_note with a template for the first write,
  // then append_note for entries after that. (Found from a real vault
  // hitting this three times on the first append of a new day's journal
  // file before the guard existed.)
  if (!exists && !content.startsWith("---\n")) {
    throw new Error(
      `append_note cannot create "${notePath}": it doesn't exist yet, and the content given has no ` +
      `frontmatter block (doesn't start with "---\\n"). Every note needs type/status/confidence/updated ` +
      `frontmatter. Use write_note with a template to create it first, then append_note for later entries.`
    );
  }

  await fs.mkdir(path.dirname(full), { recursive: true });
  // Ensure content starts on a new line
  const existing = await fs.readFile(full, "utf-8").catch(() => "");
  const separator = existing.length > 0 && !existing.endsWith("\n") ? "\n" : "";
  await fs.appendFile(full, separator + content, "utf-8");
  invalidateCacheEntry(notePath);
  return { path: notePath, appended: true };
}

// #4 — patch_section: skip code fences; error on duplicate headings
async function patchSection(notePath: string, heading: string, newContent: string): Promise<object> {
  const { raw } = await readNote(notePath);
  const lines = raw.split("\n");
  const headingPattern = new RegExp(`^#{1,6}\\s+${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`);

  let inFence = false;
  const matchIndices: number[] = [];
  let headingLevel = 0;

  for (let i = 0; i < lines.length; i++) {
    if (/^(`{3,}|~{3,})/.test(lines[i])) { inFence = !inFence; continue; }
    if (inFence) continue;
    if (headingPattern.test(lines[i])) {
      matchIndices.push(i);
      headingLevel = (lines[i].match(/^(#+)/) ?? ["", ""])[1].length;
    }
  }

  if (matchIndices.length === 0) return { error: `Heading "${heading}" not found in ${notePath}` };
  if (matchIndices.length > 1) return { error: `Heading "${heading}" appears ${matchIndices.length} times — provide a more specific heading` };

  const start = matchIndices[0];
  let end = lines.length;
  inFence = false;

  for (let i = start + 1; i < lines.length; i++) {
    if (/^(`{3,}|~{3,})/.test(lines[i])) { inFence = !inFence; continue; }
    if (inFence) continue;
    const match = lines[i].match(/^(#+)\s/);
    if (match && match[1].length <= headingLevel) { end = i; break; }
  }

  const tail = lines.slice(end);
  const updated = [
    ...lines.slice(0, start + 1),
    "",
    newContent.trim(),
    ...(tail.length > 0 && tail[0] === "" ? [] : [""]),
    ...tail,
  ].join("\n");

  const full = await vaultPathForWrite(notePath);
  await atomicWrite(full, updated);
  invalidateCacheEntry(notePath);
  return { path: notePath, heading, patched: true };
}

async function patchFrontmatter(notePath: string, updates: Record<string, unknown>): Promise<object> {
  const { frontmatter, body } = await readNote(notePath);
  const merged = { ...frontmatter, ...updates };
  const updated = matter.stringify(body, merged);
  const full = await vaultPathForWrite(notePath);
  await atomicWrite(full, updated);
  invalidateCacheEntry(notePath);
  return { path: notePath, frontmatter: merged, patched: true };
}

async function queryFrontmatter(field: string, value: string, folder?: string): Promise<object> {
  const allFiles = await walkVault(folder ? vaultPath(folder) : undefined);
  const matches: { path: string; frontmatter: Record<string, unknown> }[] = [];
  const skipped: { path: string; error: string }[] = [];
  for (const file of allFiles) {
    const note = await tryReadNote(file);
    if (!note.ok) { skipped.push({ path: file, error: note.error }); continue; }
    if (String(note.frontmatter[field]) === value) matches.push({ path: file, frontmatter: note.frontmatter });
  }
  return { matches, total: matches.length, ...(skipped.length > 0 ? { skipped } : {}) };
}

async function listLinks(notePath: string): Promise<object> {
  const { body } = await readNote(notePath);
  const outbound = extractWikilinks(body);
  const allFiles = await walkVault();
  const noteName = path.basename(notePath, ".md");
  const inbound: string[] = [];
  const skipped: { path: string; error: string }[] = [];
  for (const file of allFiles) {
    if (file === notePath) continue;
    const other = await tryReadNote(file);
    if (!other.ok) { skipped.push({ path: file, error: other.error }); continue; }
    // A wikilink's bracket text may be a bare filename ("Foo") or a
    // folder-prefixed path ("Projects/Foo") -- Obsidian resolves both to the
    // same note, so the backlink match has to strip any folder prefix off the
    // link text before comparing, the same way `noteName` already strips it
    // off the target note's own path. Comparing the raw, unstripped link text
    // against a bare basename silently missed every prefixed link (all of
    // Projects/'s inbound links, for one) until this fix.
    if (extractWikilinks(other.body).some((l) => path.basename(l).toLowerCase() === noteName.toLowerCase())) {
      inbound.push(file);
    }
  }
  return { path: notePath, outbound, inbound, ...(skipped.length > 0 ? { skipped } : {}) };
}

async function listNotes(folder?: string): Promise<object> {
  const files = await walkVault(folder ? vaultPath(folder) : undefined);
  const results = await Promise.all(files.map(async (file) => ({ file, note: await tryReadNote(file) })));
  const notes: { path: string; frontmatter: Record<string, unknown> }[] = [];
  const skipped: { path: string; error: string }[] = [];
  for (const { file, note } of results) {
    if (!note.ok) { skipped.push({ path: file, error: note.error }); continue; }
    notes.push({ path: file, frontmatter: note.frontmatter });
  }
  return { notes, total: notes.length, ...(skipped.length > 0 ? { skipped } : {}) };
}

// #2 — move to .trash/ instead of permanent unlink
async function deleteNote(notePath: string): Promise<object> {
  const full = await vaultPathForWrite(notePath);
  const trashDir = path.join(vaultRoot, ".trash");
  await fs.mkdir(trashDir, { recursive: true });
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const trashName = `${path.basename(notePath, ".md")}_${timestamp}.md`;
  const trashDest = path.join(trashDir, trashName);
  await fs.rename(full, trashDest);
  invalidateCacheEntry(notePath);
  return { path: notePath, moved_to: `.trash/${trashName}`, recoverable: true };
}

// ── MCP Server ────────────────────────────────────────────────────────────────

const server = new Server(
  { name: "obsidian-vault", version: "0.4.0" },
  { capabilities: { tools: {} } }
);

const CONSTITUTION_NOTE = "Constitution: distill what you find into vault notes -- never paste warehouse text directly into a note (see knowledge-os/constitution.md).";

const warehouseTools = [
  {
    name: "search_warehouse",
    description: `Read-only passage search over warehouse full text (primary sources) -- not the vault's distilled notes. Every hit is pinned to a content-hash doc_id and Unicode code-point character offsets, with a passage_hash so a caller can detect if the underlying text changed since this hit was returned (warehouse text is NOT immutable -- a re-extraction can rewrite it in place). ${CONSTITUTION_NOTE}`,
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        limit: { type: "number", description: "Max hits (default 8, max 25)" },
        doc_id: { type: "string", description: "Restrict to one document (sha256:<hex> or bare hex). At most 3 hits per document regardless of limit." },
      },
      required: ["query"],
    },
  },
  {
    name: "read_warehouse_text",
    description: `Read an exact character span (Unicode code points) from a warehouse document's extracted text -- for expanding context around a search_warehouse hit, not for loading whole documents (capped at 8,000 characters per call). Returns a text_hash to compare against a search_warehouse hit's passage_hash for the same range, to detect drift from a re-extraction. ${CONSTITUTION_NOTE}`,
    inputSchema: {
      type: "object",
      properties: {
        doc_id: { type: "string" },
        char_start: { type: "number" },
        char_end: { type: "number" },
      },
      required: ["doc_id", "char_start", "char_end"],
    },
  },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "search_notes",
      description: "Full-text search across vault notes. Returns scored results with excerpts. Files with unreadable frontmatter are skipped (not fatal) and listed in a `skipped` array when present.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string" },
          folder: { type: "string", description: "Restrict to a vault subfolder (e.g. 'Knowledge')" },
          limit: { type: "number", description: "Max results (default 10)" },
        },
        required: ["query"],
      },
    },
    {
      name: "read_note",
      description: "Read a note's full content, frontmatter, and body.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path (e.g. 'Knowledge/transformers.md')" },
        },
        required: ["path"],
      },
    },
    {
      name: "write_note",
      description: "Create or overwrite a note. Before overwriting an existing file, the previous version is backed up to .trash/ automatically. Use mode to express intent: 'create' fails if the file exists, 'update' fails if it doesn't, 'upsert' (default) always writes. To add content without overwriting, use append_note instead.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path (.md only)" },
          content: { type: "string", description: "Full markdown content including frontmatter" },
          mode: { type: "string", enum: ["create", "update", "upsert"], description: "Write intent. 'create' errors if file exists; 'update' errors if it doesn't; 'upsert' always writes (default)." },
        },
        required: ["path", "content"],
      },
    },
    {
      name: "append_note",
      description: "Append content to an existing note without overwriting it. Creates the file if it doesn't exist, PROVIDED content starts with a frontmatter block (\"---\\n...\\n---\\n\") - if the file is new and content has no frontmatter, this errors rather than silently creating a malformed note. For a genuinely new note, use write_note with a template first, then append_note for later entries. Use this for journal entries and running logs.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path (.md only)" },
          content: { type: "string", description: "Content to append" },
        },
        required: ["path", "content"],
      },
    },
    {
      name: "patch_section",
      description: "Replace the content under a specific heading without touching the rest of the note. Errors if the heading appears more than once or is inside a code block.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string" },
          heading: { type: "string", description: "Exact heading text without # prefix" },
          content: { type: "string", description: "New content to place under the heading" },
        },
        required: ["path", "heading", "content"],
      },
    },
    {
      name: "patch_frontmatter",
      description: "Merge key-value pairs into a note's frontmatter without touching the body. Shallow merge — arrays are replaced wholesale.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string" },
          updates: { type: "object", description: "Fields to merge into existing frontmatter" },
        },
        required: ["path", "updates"],
      },
    },
    {
      name: "query_frontmatter",
      description: "Find all notes where a frontmatter field equals a given value (e.g. status=stale, type=concept). Files with unreadable frontmatter are skipped (not fatal) and listed in a `skipped` array when present.",
      inputSchema: {
        type: "object",
        properties: {
          field: { type: "string" },
          value: { type: "string" },
          folder: { type: "string", description: "Restrict to a subfolder" },
        },
        required: ["field", "value"],
      },
    },
    {
      name: "list_links",
      description: "Get all outbound wikilinks from a note and all inbound backlinks pointing to it. Vault files with unreadable frontmatter are skipped (not fatal) while scanning for backlinks, and listed in a `skipped` array when present.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string" },
        },
        required: ["path"],
      },
    },
    {
      name: "list_notes",
      description: "List all notes in the vault or a subfolder with their frontmatter. Files with unreadable frontmatter are skipped (not fatal) and listed in a `skipped` array when present.",
      inputSchema: {
        type: "object",
        properties: {
          folder: { type: "string" },
        },
      },
    },
    {
      name: "delete_note",
      description: "Move a note to .trash/ (recoverable). Does not permanently delete. Always confirm with the user before calling.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string" },
        },
        required: ["path"],
      },
    },
    ...(warehouse.isWarehouseAvailable() ? warehouseTools : []),
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params;
  try {
    let result: object;
    switch (name) {
      case "search_notes":
        result = await searchNotes(requireString(args, "query"), optionalString(args, "folder"), optionalNumber(args, "limit"));
        break;
      case "read_note":
        result = await (async () => {
          const { frontmatter, body, raw } = await readNote(requireString(args, "path"));
          return { path: args.path, frontmatter, body, raw };
        })();
        break;
      case "write_note":
        result = await writeNoteContents(requireString(args, "path"), requireString(args, "content"), optionalString(args, "mode") ?? "upsert");
        break;
      case "append_note":
        result = await appendNoteContents(requireString(args, "path"), requireString(args, "content"));
        break;
      case "patch_section":
        result = await patchSection(requireString(args, "path"), requireString(args, "heading"), requireString(args, "content"));
        break;
      case "patch_frontmatter":
        result = await patchFrontmatter(requireString(args, "path"), requireObject(args, "updates"));
        break;
      case "query_frontmatter":
        result = await queryFrontmatter(requireString(args, "field"), requireString(args, "value"), optionalString(args, "folder"));
        break;
      case "list_links":
        result = await listLinks(requireString(args, "path"));
        break;
      case "list_notes":
        result = await listNotes(optionalString(args, "folder"));
        break;
      case "delete_note":
        result = await deleteNote(requireString(args, "path"));
        break;
      case "search_warehouse":
        if (!warehouse.isWarehouseAvailable()) throw new Error(`Unknown tool: ${name}`);
        result = await warehouse.searchWarehouse(requireString(args, "query"), optionalNumber(args, "limit"), optionalString(args, "doc_id"));
        break;
      case "read_warehouse_text":
        if (!warehouse.isWarehouseAvailable()) throw new Error(`Unknown tool: ${name}`);
        result = await warehouse.readWarehouseText(requireString(args, "doc_id"), requireNumber(args, "char_start"), requireNumber(args, "char_end"));
        break;
      default:
        throw new Error(`Unknown tool: ${name}`);
    }
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { content: [{ type: "text", text: JSON.stringify({ error: message }) }], isError: true };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
