export const PREFIX = "ff-";

/** Whether an export name denotes a component (uppercase first letter). */
export function isComponentName(name: string): boolean {
  return /^[A-Z]/.test(name);
}

/** The trame tag of a component export: `ToggleGroupItem` -> `ff-toggle-group-item`. */
export function tagName(exportName: string): string {
  const kebab = exportName
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .replace(/([A-Z])([A-Z][a-z])/g, "$1-$2")
    .toLowerCase();
  return PREFIX + kebab;
}
