import {
  columnFacetingFeature,
  columnFilteringFeature,
  columnGroupingFeature,
  columnOrderingFeature,
  columnPinningFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  createColumnHelper,
  createExpandedRowModel,
  createFacetedMinMaxValues,
  createFacetedRowModel,
  createFacetedUniqueValues,
  createFilteredRowModel,
  createGroupedRowModel,
  createSortedRowModel,
  rowExpandingFeature,
  rowSelectionFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  tableFeatures,
} from "@tanstack/react-table";
import { isRangeValue, isSetValue, isTextValue, type FilterValue } from "./layout";

export type TableRow = Record<string, unknown>;

const NUMBER_FILTER = /^\s*(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)\s*$/;

/** The row surface the filter functions need; typed structurally because the features object references them. */
export interface ValueRow {
  getValue(columnId: string): unknown;
}

type Delegate = (row: ValueRow, columnId: string, filterValue: unknown) => boolean;

interface Resolved {
  delegate: Delegate;
  value: unknown;
}

export function includesString(row: ValueRow, columnId: string, filterValue: unknown): boolean {
  const text = String(filterValue ?? "").toLowerCase();
  if (!text) return true;

  return String(row.getValue(columnId) ?? "")
    .toLowerCase()
    .includes(text);
}

/** Operator text: `>= 3`, `< 0.5`, `= 2`; a bare number means `>=`. */
export function number(row: ValueRow, columnId: string, filterValue: unknown): boolean {
  const match = NUMBER_FILTER.exec(String(filterValue ?? ""));
  if (!match) return true;

  const value = row.getValue(columnId);
  if (typeof value !== "number") return false;

  const op = match[1] ?? ">=";
  const bound = Number(match[2]);
  if (op === ">") return value > bound;
  if (op === "<") return value < bound;
  if (op === "<=") return value <= bound;
  if (op === "=") return value === bound;

  return value >= bound;
}

/** Inclusive `[min, max]`; a missing bound is open, a non-number never matches. */
export function inNumberRange(row: ValueRow, columnId: string, filterValue: unknown): boolean {
  const value = row.getValue(columnId);
  if (typeof value !== "number") return false;

  const [min, max] = Array.isArray(filterValue) ? (filterValue as [unknown, unknown]) : [undefined, undefined];
  return (typeof min !== "number" || value >= min) && (typeof max !== "number" || value <= max);
}

/** A scalar cell in a Set; `null` in the set stands for a blank cell. */
export function inSet(row: ValueRow, columnId: string, filterValue: unknown): boolean {
  if (!(filterValue instanceof Set)) return true;

  return filterValue.has(row.getValue(columnId) ?? null);
}

function resolve(filterValue: unknown): Resolved {
  if (isSetValue(filterValue)) return { delegate: inSet, value: new Set(filterValue.in.map((entry) => entry ?? null)) };
  if (isRangeValue(filterValue)) return { delegate: inNumberRange, value: [filterValue.min, filterValue.max] };
  if (isTextValue(filterValue)) return { delegate: includesString, value: filterValue.value };

  return { delegate: () => true, value: undefined };
}

function resolveNumeric(filterValue: unknown): Resolved {
  const resolved = resolve(filterValue);
  return resolved.delegate === includesString ? { delegate: number, value: resolved.value } : resolved;
}

/** Whether a filter value filters nothing, so the column drops it. */
export function isEmptyFilter(filterValue: unknown): boolean {
  if (isTextValue(filterValue)) return filterValue.value === "";
  if (isRangeValue(filterValue)) return filterValue.min === undefined && filterValue.max === undefined;

  return !isSetValue(filterValue);
}

function dispatcher(resolveFilterValue: (filterValue: unknown) => Resolved) {
  function dispatch(row: ValueRow, columnId: string, filterValue: unknown): boolean {
    const resolved = filterValue as Resolved;
    return resolved.delegate(row, columnId, resolved.value);
  }

  return Object.assign(dispatch, { resolveFilterValue, autoRemove: isEmptyFilter });
}

/** One filter function per column: the value's shape (`{value}`, `{min, max}`, `{in}`) picks the delegate once per filter. */
export const facet = dispatcher(resolve);

/** The numeric variant: text is the operator syntax instead of a substring. */
export const facetNumeric = dispatcher(resolveNumeric);

export const features = tableFeatures({
  columnSizingFeature,
  columnVisibilityFeature,
  columnOrderingFeature,
  columnPinningFeature,
  columnFilteringFeature,
  filteredRowModel: createFilteredRowModel(),
  filterFns: { includesString, number, inNumberRange, inSet, facet, facetNumeric },
  columnFacetingFeature,
  facetedRowModel: createFacetedRowModel(),
  facetedUniqueValues: createFacetedUniqueValues(),
  facetedMinMaxValues: createFacetedMinMaxValues(),
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, basic: sortFn_basic },
  columnGroupingFeature,
  groupedRowModel: createGroupedRowModel(),
  rowExpandingFeature,
  expandedRowModel: createExpandedRowModel(),
  rowSelectionFeature,
});

export type Features = typeof features;

export const helper = createColumnHelper<Features, TableRow>();

export type { FilterValue };
