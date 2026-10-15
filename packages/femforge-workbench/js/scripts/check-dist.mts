import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { JS_ROOT, SERVE_DIR } from "./paths.mts";
import { sourceHash } from "./source-hash.mts";

const HINT = "run npm run build under packages/femforge-workbench/js";

const versionPath = join(SERVE_DIR, "version.json");
const expected = sourceHash(JS_ROOT);
const actual = existsSync(versionPath)
  ? (JSON.parse(readFileSync(versionPath, "utf8")) as { hash?: string }).hash
  : undefined;

if (actual !== expected) {
  console.error(`stale dist: version.json hash ${actual ?? "missing"} != source ${expected}; ${HINT}`);
  process.exit(1);
}

console.log(`dist fresh: ${expected}`);
