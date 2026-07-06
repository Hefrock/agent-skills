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

const VAULT_PATH: string = process.env.OBSIDIAN_VAULT_PATH ?? (() => {
  console.error("OBSIDIAN_VAULT_PATH environment variable is required");
  process.exit(1);
})();

// ── Helpers ──────────────────────────────────────────────────────────────────

function vaultPath(notePath: string): string {
  const resolved = path.resolve(VAULT_PATH, notePath);
  if (!resolved.startsWith(path.resolve(VAULT_PATH))) {
    throw new Error("Path traversal not allowed");
  }
  return resolved;
}

async function readNote(notePath: string): Promise<{ frontmatter: Record<string, unknown>; body: string; raw: string }> {
  const full = vaultPath(notePath);
  const raw = await fs.readFile(full, "utf-8");
  const { data, content } = matter(raw);
  return { frontmatter: data, body: content, raw };
}

async function writeNote(notePath: string, content: string): Promise<void> {
  const full = vaultPath(notePath);
  await fs.mkdir(path.dirname(full), { recursive: true });
  await fs.writeFile(full, content, "utf-8");
}

async function walkVault(dir: string = VAULT_PATH): Promise<string[]> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...await walkVault(full));
    } else if (entry.name.endsWith(".md")) {
      files.push(path.relative(VAULT_PATH, full));
    }
  }
  return files;
}

// Simple BM25-style scoring: term frequency in title (3x) and body (1x)
function scoreNote(query: string, notePath: string, body: string, frontmatter: Record<string, unknown>): number {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const title = path.basename(notePath, ".md").toLowerCase();
  const tags = Array.isArray(frontmatter.tags) ? frontmatter.tags.join(" ").toLowerCase() : "";
  const bodyLower = body.toLowerCase();

  let score = 0;
  for (const term of terms) {
    const titleMatches = (title.match(new RegExp(term, "g")) || []).length;
    const tagMatches = (tags.match(new RegExp(term, "g")) || []).length;
    const bodyMatches = (bodyLower.match(new RegExp(term, "g")) || []).length;
    score += titleMatches * 5 + tagMatches * 3 + bodyMatches;
  }
  return score;
}

function extractWikilinks(content: string): string[] {
  const matches = content.matchAll(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g);
  return [...matches].map((m) => m[1].trim());
}

// ── Tool implementations ──────────────────────────────────────────────────────

async function searchNotes(query: string, folder?: string, limit = 10): Promise<object> {
  const allFiles = await walkVault(folder ? vaultPath(folder) : undefined);
  const results: { path: string; score: number; excerpt: string; frontmatter: Record<string, unknown> }[] = [];

  for (const file of allFiles) {
    const { frontmatter, body } = await readNote(file);
    const score = scoreNote(query, file, body, frontmatter);
    if (score > 0) {
      const lines = body.split("\n");
      const terms = query.toLowerCase().split(/\s+/);
      const matchLine = lines.find((l) => terms.some((t) => l.toLowerCase().includes(t))) ?? lines[0] ?? "";
      results.push({ path: file, score, excerpt: matchLine.trim().slice(0, 150), frontmatter });
    }
  }

  results.sort((a, b) => b.score - a.score);
  return { results: results.slice(0, limit), total: results.length };
}

async function readNoteContents(notePath: string): Promise<object> {
  const { frontmatter, body, raw } = await readNote(notePath);
  return { path: notePath, frontmatter, body, raw };
}

async function writeNoteContents(notePath: string, content: string): Promise<object> {
  await writeNote(notePath, content);
  return { path: notePath, written: true };
}

async function patchSection(notePath: string, heading: string, newContent: string): Promise<object> {
  const { raw } = await readNote(notePath);
  const lines = raw.split("\n");
  const headingPattern = new RegExp(`^#{1,6}\\s+${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`);

  let start = -1;
  let end = lines.length;
  let headingLevel = 0;

  for (let i = 0; i < lines.length; i++) {
    if (headingPattern.test(lines[i])) {
      start = i;
      headingLevel = (lines[i].match(/^(#+)/) ?? ["", ""])[1].length;
      continue;
    }
    if (start !== -1 && i > start) {
      const match = lines[i].match(/^(#+)\s/);
      if (match && match[1].length <= headingLevel) {
        end = i;
        break;
      }
    }
  }

  if (start === -1) {
    return { error: `Heading "${heading}" not found in ${notePath}` };
  }

  const updated = [
    ...lines.slice(0, start + 1),
    "",
    newContent.trim(),
    "",
    ...lines.slice(end),
  ].join("\n");

  await writeNote(notePath, updated);
  return { path: notePath, heading, patched: true };
}

async function patchFrontmatter(notePath: string, updates: Record<string, unknown>): Promise<object> {
  const { frontmatter, body } = await readNote(notePath);
  const merged = { ...frontmatter, ...updates };
  const updated = matter.stringify(body, merged);
  await writeNote(notePath, updated);
  return { path: notePath, frontmatter: merged, patched: true };
}

async function queryFrontmatter(field: string, value: string, folder?: string): Promise<object> {
  const allFiles = await walkVault(folder ? vaultPath(folder) : undefined);
  const matches: { path: string; frontmatter: Record<string, unknown> }[] = [];

  for (const file of allFiles) {
    const { frontmatter } = await readNote(file);
    const fieldValue = frontmatter[field];
    if (String(fieldValue) === value) {
      matches.push({ path: file, frontmatter });
    }
  }

  return { matches, total: matches.length };
}

async function listLinks(notePath: string): Promise<object> {
  const { body } = await readNote(notePath);
  const outbound = extractWikilinks(body);

  // Find inbound: notes that link to this one
  const allFiles = await walkVault();
  const noteName = path.basename(notePath, ".md");
  const inbound: string[] = [];

  for (const file of allFiles) {
    if (file === notePath) continue;
    const { body: otherBody } = await readNote(file);
    const links = extractWikilinks(otherBody);
    if (links.some((l) => l.toLowerCase() === noteName.toLowerCase())) {
      inbound.push(file);
    }
  }

  return { path: notePath, outbound, inbound };
}

async function listNotes(folder?: string): Promise<object> {
  const files = await walkVault(folder ? vaultPath(folder) : undefined);
  const notes = await Promise.all(
    files.map(async (file) => {
      const { frontmatter } = await readNote(file);
      return { path: file, frontmatter };
    })
  );
  return { notes, total: notes.length };
}

// ── MCP Server ────────────────────────────────────────────────────────────────

const server = new Server(
  { name: "obsidian-vault", version: "0.1.0" },
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
          query: { type: "string", description: "Search query" },
          folder: { type: "string", description: "Restrict search to a vault subfolder (e.g. 'Knowledge')" },
          limit: { type: "number", description: "Max results to return (default 10)" },
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
          path: { type: "string", description: "Vault-relative path (e.g. 'Knowledge/Reinforcement Learning.md')" },
        },
        required: ["path"],
      },
    },
    {
      name: "write_note",
      description: "Create or overwrite a note with the given content. Creates parent folders as needed.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path" },
          content: { type: "string", description: "Full markdown content including frontmatter" },
        },
        required: ["path", "content"],
      },
    },
    {
      name: "patch_section",
      description: "Replace the content under a specific heading without touching the rest of the note.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path" },
          heading: { type: "string", description: "Exact heading text (without # prefix)" },
          content: { type: "string", description: "New content to place under the heading" },
        },
        required: ["path", "heading", "content"],
      },
    },
    {
      name: "patch_frontmatter",
      description: "Update one or more frontmatter fields without touching the note body.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path" },
          updates: { type: "object", description: "Key-value pairs to merge into existing frontmatter" },
        },
        required: ["path", "updates"],
      },
    },
    {
      name: "query_frontmatter",
      description: "Find all notes where a frontmatter field equals a given value (e.g. status=stale, confidence=low).",
      inputSchema: {
        type: "object",
        properties: {
          field: { type: "string", description: "Frontmatter field name (e.g. 'status', 'confidence', 'type')" },
          value: { type: "string", description: "Value to match" },
          folder: { type: "string", description: "Restrict to a vault subfolder" },
        },
        required: ["field", "value"],
      },
    },
    {
      name: "list_links",
      description: "Get all outbound wikilinks from a note and all inbound links pointing to it.",
      inputSchema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Vault-relative path" },
        },
        required: ["path"],
      },
    },
    {
      name: "list_notes",
      description: "List all notes in the vault or a subfolder, with their frontmatter.",
      inputSchema: {
        type: "object",
        properties: {
          folder: { type: "string", description: "Vault subfolder to list (omit for full vault)" },
        },
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
        result = await searchNotes(
          args.query as string,
          args.folder as string | undefined,
          args.limit as number | undefined
        );
        break;
      case "read_note":
        result = await readNoteContents(args.path as string);
        break;
      case "write_note":
        result = await writeNoteContents(args.path as string, args.content as string);
        break;
      case "patch_section":
        result = await patchSection(args.path as string, args.heading as string, args.content as string);
        break;
      case "patch_frontmatter":
        result = await patchFrontmatter(args.path as string, args.updates as Record<string, unknown>);
        break;
      case "query_frontmatter":
        result = await queryFrontmatter(
          args.field as string,
          args.value as string,
          args.folder as string | undefined
        );
        break;
      case "list_links":
        result = await listLinks(args.path as string);
        break;
      case "list_notes":
        result = await listNotes(args.folder as string | undefined);
        break;
      default:
        throw new Error(`Unknown tool: ${name}`);
    }

    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      content: [{ type: "text", text: JSON.stringify({ error: message }) }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
