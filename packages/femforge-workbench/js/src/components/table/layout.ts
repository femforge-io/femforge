import type {
  ColumnFiltersState,
  ColumnOrderState,
  ColumnVisibilityState,
  GroupingState,
  SortingState,
} from "@tanstack/react-table";

export const LAYOUT_VERSION = 2;

export type Facet = "auto" | "values" | "range" | "none";

export interface ColumnSpec {
  field: string;
  title?: string;
  numeric?: boolean;
  /** How the header filters: `auto` is a range for numbers, a value list up to `facetLimit` unique values, else text. */
  facet?: Facet;
  width?: number;
}

export interface LayoutColumn {
  field: string;
  visible: boolean;
}

export interface LayoutSort {
  field: string;
  dir: "asc" | "desc";
}

export interface TextFilter {
  field: string;
  value: string;
}

export interface RangeFilter {
  field: string;
  min?: number;
  max?: number;
}

export interface SetFilter {
  field: string;
  in: unknown[];
}

export type LayoutFilter = TextFilter | RangeFilter | SetFilter;

/** The persisted layout document; `columns` keeps grouped fields so ungrouping restores their slot. Older documents (no `v: 2`) are ignored. */
export interface LayoutV2 {
  v: 2;
  columns: LayoutColumn[];
  groupBy: string[];
  sort: LayoutSort[];
  filter: LayoutFilter[];
}

/** The authored layout a missing document and Reset fall back to. */
export type LayoutDefaults = Partial<Omit<LayoutV2, "v">>;

/** A column's filter value in table state: the persisted filter without its field. */
export type FilterValue = { value: string } | { min?: number; max?: number } | { in: unknown[] };

export interface LayoutState {
  order: ColumnOrderState;
  visibility: ColumnVisibilityState;
  sorting: SortingState;
  filters: ColumnFiltersState;
  grouping: GroupingState;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isTextValue(value: unknown): value is { value: string } {
  return isRecord(value) && typeof value.value === "string";
}

export function isRangeValue(value: unknown): value is { min?: number; max?: number } {
  return (
    isRecord(value) &&
    !("value" in value) &&
    !("in" in value) &&
    (value.min === undefined || typeof value.min === "number") &&
    (value.max === undefined || typeof value.max === "number")
  );
}

export function isSetValue(value: unknown): value is { in: unknown[] } {
  return isRecord(value) && Array.isArray(value.in);
}

export function isFilterValue(value: unknown): value is FilterValue {
  return isTextValue(value) || isRangeValue(value) || isSetValue(value);
}

export function layoutFrom(document: unknown): LayoutV2 | null {
  return isRecord(document) && document.v === LAYOUT_VERSION ? (document as unknown as LayoutV2) : null;
}

export function defaultLayout(fields: string[], defaults: LayoutDefaults = {}): LayoutV2 {
  return {
    v: LAYOUT_VERSION,
    columns: defaults.columns ?? fields.map((field) => ({ field, visible: true })),
    groupBy: defaults.groupBy ?? [],
    sort: defaults.sort ?? [],
    filter: defaults.filter ?? [],
  };
}

/** Table state from a document: unknown fields are dropped, fields the document lacks are appended visible, and only `groupable` fields group. */
export function stateFrom(layout: LayoutV2, fields: string[], groupable: ReadonlySet<string>): LayoutState {
  const known = new Set(fields);
  const columns = layout.columns.filter((entry) => known.has(entry.field));
  const listed = new Set(columns.map((entry) => entry.field));
  const hidden = columns.filter((entry) => !entry.visible).map((entry) => entry.field);
  const filters = layout.filter.filter((entry) => known.has(entry.field));

  return {
    order: [...columns.map((entry) => entry.field), ...fields.filter((field) => !listed.has(field))],
    visibility: Object.fromEntries(hidden.map((field) => [field, false])),
    sorting: layout.sort
      .filter((entry) => known.has(entry.field))
      .map((entry) => ({ id: entry.field, desc: entry.dir === "desc" })),
    filters: filters.map(({ field, ...value }) => ({ id: field, value })),
    grouping: layout.groupBy.filter((field) => groupable.has(field)),
  };
}

export function documentOf(state: LayoutState): LayoutV2 {
  const filters = state.filters.filter((entry) => isFilterValue(entry.value));

  return {
    v: LAYOUT_VERSION,
    columns: state.order.map((field) => ({ field, visible: state.visibility[field] !== false })),
    groupBy: [...state.grouping],
    sort: state.sorting.map((entry) => ({ field: entry.id, dir: entry.desc ? "desc" : "asc" })),
    filter: filters.map((entry) => ({ field: entry.id, ...(entry.value as FilterValue) })),
  };
}

/** A range input's bound: a blank input means "no bound", which is the facet's own end, not `Number("")`. */
export function boundFrom(text: string, fallback: number): number | null {
  const value = text.trim() === "" ? fallback : Number(text);

  return Number.isFinite(value) ? value : null;
}

/** The order with fields that left dropped and fields that arrived appended. */
export function mergeOrder(order: ColumnOrderState, fields: string[]): ColumnOrderState {
  const known = new Set(fields);
  const kept = order.filter((field) => known.has(field));
  const present = new Set(kept);

  return [...kept, ...fields.filter((field) => !present.has(field))];
}

/** The three ordered lists the config panel edits. */
export interface Lists {
  group: string[];
  visible: string[];
  hidden: string[];
}

export function listsOf(state: LayoutState, pinned: string[]): Lists {
  const grouped = new Set(state.grouping);
  const free = state.order.filter((field) => !grouped.has(field) && !pinned.includes(field));

  return {
    group: [...state.grouping],
    visible: [...pinned, ...free.filter((field) => state.visibility[field] !== false)],
    hidden: free.filter((field) => state.visibility[field] === false),
  };
}

/** The state after the lists were edited: grouped fields keep their slot in the order, the rest follow the lists. */
export function applyLists(state: LayoutState, lists: Lists): LayoutState {
  const grouped = new Set(lists.group);
  const queue = [...lists.visible, ...lists.hidden];
  const order = state.order.map((field) => (grouped.has(field) ? field : (queue.shift() ?? field)));

  return {
    ...state,
    order: [...order, ...queue],
    visibility: Object.fromEntries(lists.hidden.map((field) => [field, false])),
    grouping: lists.group,
  };
}
