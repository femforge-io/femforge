import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Plugin } from "vite";
import { clampChroma, formatHex, parse } from "culori";
import { BUNDLE, JS_ROOT, LICENSES, LICENSES_JSON, SERVE_DIR, STYLESHEET } from "./paths.mts";
import { sourceHash } from "./source-hash.mts";

const PX = /^(-?\d+(?:\.\d+)?)px$/;
const METRIC_PREFIX = "--ff-";
// styles.css @imports their CSS into ff.css, outside the bundled modules Vite's license scan reads.
const BUILD_OUTPUT_PACKAGES = ["tailwindcss", "dockview"];
const LICENSE_FILE = /^(licen[cs]e|copying)/i;
const NOTICE_FILE = /^(notice|thirdpartynotices)/i;
const SECTION_RULE = `\n\n${"-".repeat(80)}\n\n`;

export interface Theme {
  colors: Record<string, string>;
  metrics: Record<string, number>;
  fonts: { sans: string; mono: string };
}

function block(css: string, selector: string): string {
  const pattern = new RegExp(`^${selector.replace(".", "\\.")}\\s*\\{([\\s\\S]*?)^\\}`, "m");
  const match = pattern.exec(css);
  if (!match) throw new Error(`styles.css: no ${selector} block`);

  return match[1];
}

function declarations(body: string): Map<string, string> {
  const map = new Map<string, string>();
  const clean = body.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const statement of clean.split(";")) {
    const colon = statement.indexOf(":");
    if (colon < 0) continue;

    const name = statement.slice(0, colon).trim();
    if (!name.startsWith("--")) continue;

    map.set(
      name,
      statement
        .slice(colon + 1)
        .replace(/\s+/g, " ")
        .trim(),
    );
  }

  return map;
}

function resolveVar(value: string, own: Map<string, string>, root: Map<string, string>): string {
  return value.replace(/var\((--[\w-]+)\)/g, (whole, name: string) => own.get(name) ?? root.get(name) ?? whole);
}

function tokenValue(value: string): string {
  const color = parse(value);
  if (color === undefined) return value;

  return formatHex(clampChroma(color, "oklch"));
}

/** The `:root` custom properties of styles.css: colours as sRGB hex, `--ff-*` px as numbers, the rest verbatim. */
export function themeFrom(css: string): Theme {
  const root = declarations(block(css, ":root"));
  const colors: Record<string, string> = {};
  const metrics: Record<string, number> = {};

  for (const [name, raw] of root) {
    const value = resolveVar(resolveVar(raw, root, root), root, root);
    const px = name.startsWith(METRIC_PREFIX) ? PX.exec(value) : null;
    if (px) metrics[name.slice(METRIC_PREFIX.length)] = Number(px[1]);
    else colors[name.slice(2)] = tokenValue(value);
  }

  return { colors, metrics, fonts: { sans: colors["font-sans"], mono: colors["font-mono"] } };
}

/** The keys of the ICONS map in src/icons.ts. */
export function iconsFrom(source: string): string[] {
  const body = /export const ICONS = \{([\s\S]*?)\} as const/.exec(source);
  if (!body) throw new Error("icons.ts: no ICONS map");

  return [...body[1].matchAll(/^\s*"?([a-z0-9-]+)"?\s*:/gm)].map((match) => match[1]).sort();
}

function writeJson(name: string, value: unknown): void {
  writeFileSync(join(SERVE_DIR, name), `${JSON.stringify(value, null, 2)}\n`);
}

interface BundledLicense {
  name: string;
  version: string;
  identifier: string;
  text: string | null;
}

const packageDir = (name: string): string => join(JS_ROOT, "node_modules", name);

/** The trimmed NOTICE files of a package installed at the top of node_modules; a nested install has none there. */
function notices(name: string): string[] {
  const dir = packageDir(name);
  if (!existsSync(dir)) return [];

  return readdirSync(dir)
    .filter((file) => NOTICE_FILE.test(file))
    .map((file) => readFileSync(join(dir, file), "utf8").trim());
}

/** Renders Vite's licenses.json and the build-output packages as THIRD_PARTY_LICENSES.txt, then deletes the JSON. */
function writeLicenses(): void {
  const json = join(SERVE_DIR, LICENSES_JSON);
  const entries = JSON.parse(readFileSync(json, "utf8")) as BundledLicense[];

  for (const name of BUILD_OUTPUT_PACKAGES) {
    const dir = packageDir(name);
    const meta = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) as { version: string; license: string };
    const licenseFile = readdirSync(dir).find((file) => LICENSE_FILE.test(file));
    if (licenseFile === undefined) throw new Error(`${name}: no license file in ${dir}`);

    const text = readFileSync(join(dir, licenseFile), "utf8");
    entries.push({ name, version: meta.version, identifier: meta.license, text });
  }

  const sections = entries
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((entry) => {
      const header = `${entry.name} ${entry.version} (${entry.identifier})`;
      const text = (entry.text ?? "").trim();
      const parts = [header, text, ...notices(entry.name)];

      return parts.filter((part) => part !== "").join("\n\n");
    });

  const preamble = `Third-party software in ${BUNDLE} and ${STYLESHEET}. The fonts carry their licenses in fonts/.`;
  writeFileSync(join(SERVE_DIR, LICENSES), `${preamble}\n\n${sections.join(SECTION_RULE)}\n`);

  rmSync(json);
}

/** Stamps the bundle with the source hash and writes the version, theme, icons and license files beside it. */
export function dist(): Plugin {
  let hash = "";
  let written = false;

  return {
    name: "ff-dist",
    buildStart() {
      hash = sourceHash(JS_ROOT);
    },
    // After minification, so the banner comment survives it.
    generateBundle(_options, bundle) {
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") output.code = `/* ff ${hash} */\n${output.code}`;
      }
    },
    writeBundle() {
      written = true;
    },
    closeBundle() {
      // Vite also closes a failed build, which must leave the previous dist and its stamp alone.
      if (!written) return;

      written = false;
      mkdirSync(SERVE_DIR, { recursive: true });
      writeJson("version.json", { hash, bundle: BUNDLE, css: STYLESHEET });
      writeJson("theme.json", themeFrom(readFileSync(join(JS_ROOT, "src/styles.css"), "utf8")));
      writeJson("icons.json", iconsFrom(readFileSync(join(JS_ROOT, "src/icons.ts"), "utf8")));
      writeLicenses();
    },
  };
}
