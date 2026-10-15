export interface ComboboxItem {
  value: string;
  label: string;
  groups?: string[];
  swatch?: string;
}

export interface Section {
  heading: string;
  items: ComboboxItem[];
}

/** The items under the active filters (all of them while none is active), each once under its first active group, sections in first-seen order. */
export function sectionsOf(items: readonly ComboboxItem[], active: readonly string[]): Section[] {
  const sections = new Map<string, ComboboxItem[]>();
  for (const item of items) {
    const groups = item.groups ?? [];
    const heading = active.length === 0 ? (groups[0] ?? "") : groups.find((group) => active.includes(group));
    if (heading === undefined) continue;

    const section = sections.get(heading);
    if (section) section.push(item);
    else sections.set(heading, [item]);
  }

  return [...sections].map(([heading, members]) => ({ heading, items: members }));
}

/** The value `delta` places from `value` in list order, wrapping; the first item when `value` is not listed. */
export function stepped(items: readonly ComboboxItem[], value: string | undefined, delta: number): string | undefined {
  if (items.length === 0) return undefined;

  const index = items.findIndex((item) => item.value === value);
  if (index < 0) return items[0].value;

  return items[(index + delta + items.length) % items.length].value;
}
