import { expect, test } from "vitest";
import {
  facet,
  facetNumeric,
  includesString,
  inNumberRange,
  inSet,
  isEmptyFilter,
  number,
  type ValueRow,
} from "./features";

const COLUMN = "x";

function row(value: unknown): ValueRow {
  return { getValue: () => value };
}

function matches(dispatch: typeof facet, value: unknown, cell: unknown): boolean {
  return dispatch(row(cell), COLUMN, dispatch.resolveFilterValue(value));
}

test("a filter value's shape picks the delegate", () => {
  expect(matches(facet, { value: "ab" }, "xaby")).toBe(true);
  expect(matches(facet, { value: "ab" }, "xy")).toBe(false);
  expect(matches(facet, { min: 2, max: 4 }, 3)).toBe(true);
  expect(matches(facet, { min: 2, max: 4 }, 5)).toBe(false);
  expect(matches(facet, { in: ["a", null] }, "a")).toBe(true);
  expect(matches(facet, { in: ["a", null] }, undefined)).toBe(true);
  expect(matches(facet, { in: ["a"] }, "b")).toBe(false);
  expect(matches(facet, "not a filter value", "anything"), "a shape none of the guards claim filters nothing").toBe(
    true,
  );
});

test("a numeric column reads text as an operator instead of a substring", () => {
  expect(matches(facetNumeric, { value: "3" }, 3)).toBe(true);
  expect(matches(facetNumeric, { value: "3" }, 2)).toBe(false);
  expect(matches(facetNumeric, { value: "< 3" }, 2)).toBe(true);
  expect(matches(facet, { value: "< 3" }, 2)).toBe(false);
  expect(matches(facetNumeric, { min: 2 }, 3)).toBe(true);
  expect(matches(facetNumeric, { in: [3] }, 3)).toBe(true);
});

test("the operator syntax covers each comparison and ignores what it cannot read", () => {
  expect(number(row(3), COLUMN, ">= 3")).toBe(true);
  expect(number(row(3), COLUMN, "> 3")).toBe(false);
  expect(number(row(3), COLUMN, "<=3")).toBe(true);
  expect(number(row(3), COLUMN, "= 3")).toBe(true);
  expect(number(row(3), COLUMN, "-2")).toBe(true);
  expect(number(row(3), COLUMN, "two"), "a half-typed filter must not empty the table").toBe(true);
  expect(number(row("3"), COLUMN, "3"), "a text cell is no number").toBe(false);
});

test("the delegates hold their own edges", () => {
  expect(includesString(row("AbC"), COLUMN, "bc")).toBe(true);
  expect(includesString(row(null), COLUMN, "")).toBe(true);
  expect(includesString(row(null), COLUMN, "b")).toBe(false);
  expect(inNumberRange(row(3), COLUMN, [undefined, undefined])).toBe(true);
  expect(inNumberRange(row(3), COLUMN, "not a range"), "an unreadable range is two open bounds").toBe(true);
  expect(inNumberRange(row("3"), COLUMN, [1, 4])).toBe(false);
  expect(inSet(row("a"), COLUMN, "not a set")).toBe(true);
});

test("a filter that filters nothing is dropped by the column", () => {
  expect(facet.autoRemove).toBe(isEmptyFilter);
  expect(isEmptyFilter({ value: "" })).toBe(true);
  expect(isEmptyFilter({ value: "a" })).toBe(false);
  expect(isEmptyFilter({})).toBe(true);
  expect(isEmptyFilter({ min: 1 })).toBe(false);
  expect(isEmptyFilter({ in: [] }), "an empty value list filters everything out").toBe(false);
  expect(isEmptyFilter("nonsense")).toBe(true);
});
