// Low-level vault filesystem primitives: path containment, note read/write,
// directory walking. No BM25/index-cache concerns here (see vault-index.ts) —
// this module is the thing BOTH the vault tools and the warehouse's
// source_note lookup can depend on without any circularity between them.
import fs from "fs/promises";
import path from "path";
import matter from "gray-matter";

const VAULT_PATH: string = process.env.OBSIDIAN_VAULT_PATH ?? (() => {
  console.error("OBSIDIAN_VAULT_PATH environment variable is required");
  process.exit(1);
})();

// #9 — validate vault exists at startup
export const vaultRoot = path.resolve(VAULT_PATH);
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
export function vaultPath(notePath: string): string {
  const resolved = path.resolve(vaultRoot, notePath);
  if (!resolved.startsWith(vaultRoot + path.sep) && resolved !== vaultRoot) {
    throw new Error("Path traversal not allowed");
  }
  return resolved;
}

// #7 — symlink-aware check + #8 — .md only + no hidden dirs — used for all writes
export async function vaultPathForWrite(notePath: string): Promise<string> {
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

// ── Core I/O ──────────────────────────────────────────────────────────────────

export async function readNote(notePath: string): Promise<{ frontmatter: Record<string, unknown>; body: string; raw: string }> {
  const full = vaultPath(notePath);
  const raw = await fs.readFile(full, "utf-8");
  const { data, content } = matter(raw);
  return { frontmatter: data, body: content, raw };
}

// #11 — vault-wide scans (search/list/query) tolerate malformed per-file
// frontmatter instead of aborting entirely. One file with broken YAML fencing
// (e.g. `--- type: project` glued onto the opening line, which gray-matter
// misreads as a request for an unregistered custom parser engine) used to
// throw and take down search_notes/list_notes/query_frontmatter/list_links
// for the whole vault, no matter how unrelated the query was. Callers that
// read a single known path still get a normal thrown error — this helper is
// only for loops over `walkVault()` results, where one bad file shouldn't
// hide every other result.
export async function tryReadNote(
  notePath: string
): Promise<
  | { ok: true; frontmatter: Record<string, unknown>; body: string; raw: string }
  | { ok: false; error: string }
> {
  try {
    const note = await readNote(notePath);
    return { ok: true, ...note };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// #3 — atomic write: temp file in same dir → rename
export async function atomicWrite(fullPath: string, content: string): Promise<void> {
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

export async function walkVault(dir: string = vaultRoot): Promise<string[]> {
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

export function extractWikilinks(content: string): string[] {
  return [...content.matchAll(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g)].map((m) => m[1].trim());
}
