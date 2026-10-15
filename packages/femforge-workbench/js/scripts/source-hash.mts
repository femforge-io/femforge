import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const HASHED_DIRS = ["src", "public", "scripts"];
export const HASHED_FILES = ["package.json", "package-lock.json", "vite.config.ts", "components.json", "tsconfig.json"];
/** The Tailwind scan roots of styles.css: a utility class written in a Python source lands in the stylesheet, so the dist depends on them too. */
export const SCANNED_DIRS = ["../src/femforge_workbench"];
export const SCANNED_SUFFIX = ".py";
const SCANNED_SKIP = join("widgets", "serve");
const DIGEST_CHARS = 16;

function walk(dir: string, out: string[], suffix?: string): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (path.endsWith(SCANNED_SKIP)) continue;

    if (entry.isDirectory()) walk(path, out, suffix);
    else if (!suffix || path.endsWith(suffix)) out.push(path);
  }
}

const CRLF = Buffer.from("\r\n");
const LF = Buffer.from("\n");

/** The file's bytes with CRLF folded to LF, so a checkout with autocrlf hashes like one without. */
function normalized(path: string): Buffer {
  const bytes = readFileSync(path);
  if (!bytes.includes(CRLF)) return bytes;

  const pieces: Buffer[] = [];
  let start = 0;
  let at = bytes.indexOf(CRLF);
  while (at !== -1) {
    pieces.push(bytes.subarray(start, at), LF);
    start = at + CRLF.length;
    at = bytes.indexOf(CRLF, start);
  }
  pieces.push(bytes.subarray(start));

  return Buffer.concat(pieces);
}

/** sha256 (first 16 hex chars) over the sorted (posix path, CRLF-normalized bytes) of every source the dist depends on. */
export function sourceHash(root: string): string {
  const files: string[] = [];
  for (const dir of HASHED_DIRS) walk(join(root, dir), files);
  for (const dir of SCANNED_DIRS) walk(join(root, dir), files, SCANNED_SUFFIX);
  for (const file of HASHED_FILES) files.push(join(root, file));

  const paths = files.map((file) => relative(root, file).split(sep).join("/")).sort();
  const hash = createHash("sha256");
  for (const path of paths) {
    hash.update(path);
    hash.update("\0");
    hash.update(normalized(join(root, path)));
    hash.update("\0");
  }

  return hash.digest("hex").slice(0, DIGEST_CHARS);
}
