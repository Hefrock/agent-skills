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
import os from "os";

const VAULT_PATH: string = process.env.OBSIDIAN_VAULT_PATH ?? (() => {
  console.error("OBSIDIAN_VAULT_PATH environment variable is required");
  process.exit(1);
})();

// #9 — validate vault exists at startup
const vaultRoot = path.resolve(VAULT_PATH);
try {
  const stat = await fs.stat(vaultRoot);
  if (!stat.isDirectory()) {
    console.error(`OBSIDIAN_VAULT_PATH is not a directory: ${vaultRoot}`);
    process.exit(1);
  }
} catch {
  console.error(`OBSIDIAN_VAULT_PATH does not exist: ${vaultRoot}`);
  process.exit(1);
}

// ── Path helpers ──────────────────────────────────────────────────────────────

// Lexical containment check — used for all operations
function vaultPath(notePath: string): string {
  const resolved = path.resolve(vaultRoot, notePath);
  if (!resolved.startsWith(vaultRoot + path.sep) && resolved !== vaultRoot) {
    throw new Error("Path traversal not allowed");
  }
  return resolved;
}

// #7 — symlink-aware check + #8 — .md only + no hidden dirs — used for all writes
async function vaultPathForWrite(notePath: string): Promise<string> {
  // #8: block hidden path components (.obsidian/, .git/, etc.)
  const parts = notePath.split(/[\\/]/).filter(Boolean);
  if (parts.some((p) => p.startsWith("."))) {
    throw new Error("Access to hidden directories or files is not allowed");
  }
  // #8: .md files only
  if (!notePath.endsWith(".md")) {
    throw new Error("Only .md files are supported");
  }

  const resolved = vaultPath(notePath); // lexical check first

  // #7: realpath check on parent dir (file may not exist yet for creates)
  const parentDir = path.dirname(resolved);
  try {
    const realParent = await fs.realpath(parentDir);
    if (!realParent.startsWith(vaultRoot + path.sep) && realParent !== vaultRoot) {
      throw new Error("Path traversal not allowed (symlink in parent directory)");
    }
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
    // Parent doesn't exist yet — will be created, trust lexical check
  }

  // #7: for existing files, also realpath the file itself
  try {
    const realResolved = await fs.realpath(resolved);
    if (!realResolved.startsWith(vaultRoot + path.sep) && realResolved !== vaultRoot) {
      throw new Error("Path traversal not allowed (symlink)");
    }
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
    // File doesn't exist yet — OK for creates
  }

  return resolved;
}

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

// ── Core I/O ──────────────────────────────────────────────────────────────────

async function readNote(notePath: string): Promise<{ frontmatter: Record<string, unknown>; body: string; raw: string }> {
  const full = vaultPath(notePath);
  const raw = await fs.readFile(full, "utf-8");
  const { data, content } = matter(raw);
  return { frontmatter: data, body: content, raw };
}

// #3 — atomic write: temp file in same dir → rename
async function atomicWrite(fullPath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(fullPath), { recursive: true });
  const tmpPath = fullPath + ".tmp";
  try {
    await fs.writeFile(tmpPath, content, "utf-8");
    await fs.rename(tmpPath, fullPath);
  } catch (err) {
    // Clean up temp file on failure
    await fs.unlink(tmpPath).catch(() => undefined);
    throw err;
  }
}

async function walkVault(dir: string = vaultRoot): Promise<string[]> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...await walkVault(full));
    } else if (entry.name.endsWith(".md")) {
      files.push(path.relative(vaultRoot, full));
    }
  }
  return files;
}

function scoreNote(query: string, notePath: string, body: string, frontmatter: Record<string, unknown>): number {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const title = path.basename(notePath, ".md").toLowerCase();
  const tags = Array.isArray(frontmatter.tags) ? frontmatter.tags.join(" ").toLowerCase() : "";
  const bodyLower = body.toLowerCase();
  let score = 0;
  for (const term of terms) {
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    score += (title.match(new RegExp(escaped, "g")) || []).length * 5;
    score += (tags.match(new RegExp(escaped, "g")) || []).length * 3;
    score += (bodyLower.match(new RegExp(escaped, "g")) || []).length;
  }
  return score;
}

function extractWikilinks(content: string): string[] {
  return [...content.matchAll(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g)].map((m) => m[1].trim());
}

// ── Tool implementations ──────────────────────────────────────────────────────

async function searchNotes(query: string, folder?: string, limit = 10): Promise<object> {
  const allFiles = await walkVault(folder ? vaultPath(folder) : undefined);
  const results: { path: string; score: number; excerpt: string; frontmatter: Record<string, unknown> }[] = [];
  for (const file of allFiles) {
    const { frontmatter, body } = await readNote(file);
    const score = scoreNote(query, file, body, frontmatter);
    if (score > 0) {
      const terms = query.toLowerCase().split(/\s+/);
      const matchLine = body.split("\n").find((l) => terms.some((t) => l.toLowerCase().includes(t))) ?? body.split("\n")[0] ?? "";
      results.push({ path: file, score, excerpt: matchLine.trim().slice(0, 150), frontmatter });
    }
  }
  results.sort((a, b) => b.score - a.score);
  return { results: results.slice(0, limit), total: results.length };
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
  return { path: notePath, written: true, backed_up: exists };
}

// #1 — dedicated append tool so callers never accidentally clobber existing files
async function appendNoteContents(notePath: string, content: string): Promise<object> {
  const full = await vaultPathForWrite(notePath);
  await fs.mkdir(path.dirname(full), { recursive: true });
  // Ensure content starts on a new line
  const existing = await fs.readFile(full, "utf-8").catch(() => "");
  const separator = existing.length > 0 && !existing.endsWith("\n") ? "\n" : "";
  await fs.appendFile(full, separator + content, "utf-8");
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
  return { path: notePath, heading, patched: true };
}

async function patchFrontmatter(notePath: string, updates: Record<string, unknown>): Promise<object> {
  const { frontmatter, body } = await readNote(notePath);
  const merged = { ...frontmatter, ...updates };
  const updated = matter.stringify(body, merged);
  const full = await vaultPathForWrite(notePath);
  await atomicWrite(full, updated);
  return { path: notePath, frontmatter: merged, patched: true };
}

async function queryFrontmatter(field: string, value: string, folder?: string): Promise<object> {
  const allFiles = await walkVault(folder ? vaultPath(folder) : undefined);
  const matches: { path: string; frontmatter: Record<string, unknown> }[] = [];
  for (const file of allFiles) {
    const { frontmatter } = await readNote(file);
    if (String(frontmatter[field]) === value) matches.push({ path: file, frontmatter });
  }
  return { matches, total: matches.length };
}

async function listLinks(notePath: string): Promise<object> {
  const { body } = await readNote(notePath);
  const outbound = extractWikilinks(body);
  const allFiles = await walkVault();
  const noteName = path.basename(notePath, ".md");
  const inbound: string[] = [];
  for (const file of allFiles) {
    if (file === notePath) continue;
    const { body: otherBody } = await readNote(file);
    if (extractWikilinks(otherBody).some((l) => l.toLowerCase() === noteName.toLowerCase())) {
      inbound.push(file);
    }
  }
  return { path: notePath, outbound, inbound };
}

async function listNotes(folder?: string): Promise<object> {
  const files = await walkVault(folder ? vaultPath(folder) : undefined);
  const notes = await Promise.all(files.map(async (file) => ({ path: file, frontmatter: (await readNote(file)).frontmatter })));
  return { notes, total: notes.length };
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
  return { path: notePath, moved_to: `.trash/${trashName}`, recoverable: true };
}

// ── MCP Server ────────────────────────────────────────────────────────────────

const server = new Server(
  { name: "obsidian-vault", version: "0.3.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "search_notes",
      description: "Full-text search across vault notes. Returns scored results with excerpts.",
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
      description: "Append content to an existing note without overwriting it. Creates the file if it doesn't exist. Use this for journal entries and running logs.",
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
      description: "Find all notes where a frontmatter field equals a given value (e.g. status=stale, type=concept).",
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
      description: "Get all outbound wikilinks from a note and all inbound backlinks pointing to it.",
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
      description: "List all notes in the vault or a subfolder with their frontmatter.",
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
