// Generic, symlink-aware path-containment check, reusable anywhere a
// relative path must resolve inside a fixed root and nowhere else. This is
// the same lexical-then-realpath pattern vault-io.ts's vaultPathForWrite
// uses, generalized: unlike that function, this one has no vault-specific
// rules (.md-only, no hidden dirs) and no "doesn't exist yet, trust the
// lexical check" case, since every caller here (warehouse manifest entries)
// is resolving a path that's supposed to already exist on disk.
import fs from "fs/promises";
import path from "path";

export async function resolveContained(root: string, relPath: string): Promise<string> {
  const resolved = path.resolve(root, relPath);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    throw new Error(`Path escapes root: ${relPath}`);
  }
  const real = await fs.realpath(resolved);
  if (real !== root && !real.startsWith(root + path.sep)) {
    throw new Error(`Path escapes root via symlink: ${relPath}`);
  }
  return real;
}
