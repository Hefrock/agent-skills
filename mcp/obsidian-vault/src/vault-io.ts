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
const configuredRoot = path.resolve(VAULT_PATH);
try {
  const stat = await fs.stat(configuredRoot);
  if (!stat.isDirectory()) {
    console.error(`OBSIDIAN_VAULT_PATH is not a directory: ${configuredRoot}`);
    process.exit(1);
  }
} catch {
  console.error(`OBSIDIAN_VAULT_PATH does not exist: ${configuredRoot}`);
  process.exit(1);
}

// The root is canonicalized once, here. Every containment check below
// compares a realpath'd candidate against it, so a root that is itself
// reached through a symlink (a synced-folder link, macOS's /tmp and /var)
// must be realpath'd too -- otherwise no real path ever starts with it and
// every write is rejected as "symlink in parent directory".
export const vaultRoot = await fs.realpath(configuredRoot);

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

  // #7: realpath the nearest ancestor directory that exists, and require it to
  // be inside the vault. Checking only the immediate parent was not enough: for
  // a write into a not-yet-existing subfolder (Escape/new/x.md), the parent
  // realpath fails with ENOENT, the lexical check passed, and atomicWrite's
  // mkdir -p then created the folder through a symlinked "Escape" -> outside.
  // A missing ancestor that is itself a dangling symlink is refused too, since
  // mkdir would otherwise follow or trip over it.
  let ancestor = path.dirname(resolved);
  for (;;) {
    let real: string;
    try {
      real = await fs.realpath(ancestor);
    } catch (err: unknown) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      const link = await fs.lstat(ancestor).catch(() => null);
      if (link?.isSymbolicLink()) {
        throw new Error("Path traversal not allowed (dangling symlink in parent directory)");
      }
      const up = path.dirname(ancestor);
      if (up === ancestor) break;
      ancestor = up;
      continue;
    }
    if (!isInsideVault(real)) {
      throw new Error("Path traversal not allowed (symlink in parent directory)");
    }
    break;
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

function isInsideVault(real: string): boolean {
  return real === vaultRoot || real.startsWith(vaultRoot + path.sep);
}

// Read-side counterpart of vaultPathForWrite: same rules (no hidden path
// components, .md only, lexical containment, then realpath containment), so
// a note that cannot be written through this server cannot be read through
// it either. Without the realpath step a symlink inside the vault pointing
// at a file outside it was readable via read_note and surfaced by scans.
// A path that does not exist is returned as-is so the caller's readFile
// reports the ordinary ENOENT.
export async function vaultPathForRead(notePath: string): Promise<string> {
  const parts = notePath.split(/[\\/]/).filter(Boolean);
  if (parts.some((p) => p.startsWith("."))) {
    throw new Error("Access to hidden directories or files is not allowed");
  }
  if (!notePath.endsWith(".md")) {
    throw new Error("Only .md files are supported");
  }
  const resolved = vaultPath(notePath);
  try {
    if (!isInsideVault(await fs.realpath(resolved))) {
      throw new Error("Path traversal not allowed (symlink)");
    }
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
  }
  return resolved;
}

// Folder arguments for scans (search/list/query): lexical containment, then
// realpath containment, so a symlinked folder cannot be used to enumerate a
// directory outside the vault. A missing folder is returned as-is; callers
// report it the way they did before.
export async function vaultFolderForRead(folder: string): Promise<string> {
  const resolved = vaultPath(folder);
  try {
    if (!isInsideVault(await fs.realpath(resolved))) {
      throw new Error("Path traversal not allowed (symlink)");
    }
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
  }
  return resolved;
}

// ── Core I/O ──────────────────────────────────────────────────────────────────

export async function readNote(notePath: string): Promise<{ frontmatter: Record<string, unknown>; body: string; raw: string }> {
  const full = await vaultPathForRead(notePath);
  const raw = await fs.readFile(full, "utf-8");
  // #206 — pass an options object: gray-matter 4.0.3 caches parses by input
  // string only when called with no options, and a cached entry is returned
  // even for input whose first parse threw, so a malformed note read twice
  // came back the second time with empty frontmatter instead of an error.
  const { data, content } = matter(raw, {});
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
