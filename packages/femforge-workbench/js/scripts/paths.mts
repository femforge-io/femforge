import { resolve } from "node:path";

export const JS_ROOT = resolve(import.meta.dirname, "..");
export const SERVE_DIR = resolve(JS_ROOT, "../src/femforge_workbench/widgets/serve");
export const BUNDLE = "ff.umd.js";
export const STYLESHEET = "ff.css";
export const LICENSES_JSON = "licenses.json";
export const LICENSES = "THIRD_PARTY_LICENSES.txt";
