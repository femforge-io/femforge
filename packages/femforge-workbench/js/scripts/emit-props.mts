import { readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Node, Project, type Type } from "ts-morph";
import { isComponentName, tagName } from "../src/tags.ts";
import { JS_ROOT, SERVE_DIR } from "./paths.mts";

const COMPONENT_DIRS = ["src/components", "src/components/ui"];
const IGNORED_PROPS = new Set(["children", "className", "style"]);
const PROBE = "src/__props__.ts";

interface Entry {
  tag: string;
  module: string;
  name: string;
}

export interface TagProps {
  props: string[];
  literals: Record<string, string[]>;
}

function isComponentType(type: Type): boolean {
  return type.getCallSignatures().length > 0 || type.getProperty("$$typeof") !== undefined;
}

/** The registered tags in registration order: library components first, then the shadcn modules; the first tag wins. */
function registered(project: Project): Entry[] {
  const entries: Entry[] = [];
  const seen = new Set<string>();
  for (const dir of COMPONENT_DIRS) {
    const files = readdirSync(join(JS_ROOT, dir))
      .filter((file) => file.endsWith(".tsx"))
      .sort();
    for (const file of files) {
      const source = project.getSourceFileOrThrow(join(JS_ROOT, dir, file));
      for (const [name, declarations] of source.getExportedDeclarations()) {
        const declaration = declarations[0];
        if (!isComponentName(name) || declaration === undefined) continue;
        if (Node.isInterfaceDeclaration(declaration) || Node.isTypeAliasDeclaration(declaration)) continue;
        if (!isComponentType(declaration.getType())) continue;

        const tag = tagName(name);
        if (seen.has(tag)) continue;

        seen.add(tag);
        entries.push({ tag, module: `./${dir.slice("src/".length)}/${file.slice(0, -".tsx".length)}`, name });
      }
    }
  }

  return entries;
}

/** The union members that carry a value: cva's VariantProps unions carry `null` beside `undefined`, neither of which the Python side sends. */
function valueMembers(type: Type): Type[] {
  return (type.isUnion() ? type.getUnionTypes() : [type]).filter((member) => !member.isUndefined() && !member.isNull());
}

function literalUnion(members: Type[]): string[] | undefined {
  if (members.length === 0 || !members.some((member) => member.isStringLiteral())) return undefined;
  if (!members.every((member) => member.isStringLiteral())) return undefined;

  return members.map((member) => String(member.getLiteralValue())).sort();
}

/** The object type of ours a prop nests (or an array of it), whose literal unions are emitted one level down. */
function nestedOf(members: Type[]): Type | undefined {
  const [only] = members;
  if (members.length !== 1 || only === undefined) return undefined;

  const candidate = only.getArrayElementType() ?? only;
  if (!candidate.isObject() || candidate.isArray() || candidate.getCallSignatures().length > 0) return undefined;

  const declarations = candidate.getSymbol()?.getDeclarations() ?? [];
  const ours = declarations.some((declaration) => {
    const source = declaration.getSourceFile();
    return !source.isDeclarationFile() && !source.isInNodeModules();
  });
  return ours ? candidate : undefined;
}

function propsOf(type: Type, probe: Node): TagProps {
  const props: string[] = [];
  const literals: Record<string, string[]> = {};
  for (const property of type.getProperties()) {
    const name = property.getName();
    if (IGNORED_PROPS.has(name)) continue;

    const members = valueMembers(property.getTypeAtLocation(probe));
    if (members.some((member) => member.getCallSignatures().length > 0)) continue;

    props.push(name);
    const union = literalUnion(members);
    if (union) literals[name] = union;

    const nested = nestedOf(members);
    for (const inner of nested?.getProperties() ?? []) {
      const innerUnion = literalUnion(valueMembers(inner.getTypeAtLocation(probe)));
      if (innerUnion) literals[`${name}.${inner.getName()}`] = innerUnion;
    }
  }

  return {
    props: props.sort(),
    literals: Object.fromEntries(Object.entries(literals).sort(([a], [b]) => a.localeCompare(b))),
  };
}

const project = new Project({ tsConfigFilePath: join(JS_ROOT, "tsconfig.json") });
const entries = registered(project);
const imports = entries.map((entry, index) => `import * as m${index} from "${entry.module}";`);
const aliases = entries.map(
  (entry, index) => `export type P${index} = React.ComponentProps<typeof m${index}.${entry.name}>;`,
);
const probe = project.createSourceFile(
  join(JS_ROOT, PROBE),
  ['import type * as React from "react";', ...imports, ...aliases].join("\n"),
  { overwrite: true },
);

const result: Record<string, TagProps> = {};
entries.forEach((entry, index) => {
  result[entry.tag] = propsOf(probe.getTypeAliasOrThrow(`P${index}`).getType(), probe);
});

const sorted = Object.fromEntries(Object.entries(result).sort(([a], [b]) => a.localeCompare(b)));
writeFileSync(join(SERVE_DIR, "props.json"), `${JSON.stringify(sorted, null, 2)}\n`);
console.log(`props.json: ${entries.length} tags`);
