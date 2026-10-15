import { expect, test } from "vitest";
import {
  applyLists,
  boundFrom,
  defaultLayout,
  documentOf,
  layoutFrom,
  listsOf,
  mergeOrder,
  stateFrom,
  type LayoutState,
  type LayoutV2,
} from "./layout";

const FIELDS = ["a", "b", "c"];

function layout(partial: Partial<Omit<LayoutV2, "v">> = {}): LayoutV2 {
  return { ...defaultLayout(FIELDS), ...partial };
}

test("only a v2 document is a layout", () => {
  const document = { v: 2, columns: [], groupBy: [], sort: [], filter: [] };

  expect(layoutFrom(document)).toBe(document);
  expect(layoutFrom({ columns: [{ field: "a", visible: true }], sort: [], filter: [] })).toBeNull();
  expect(layoutFrom({ v: 1, columns: [] })).toBeNull();
  expect(layoutFrom(null)).toBeNull();
  expect(layoutFrom([{ v: 2 }])).toBeNull();
});

test("the default layout shows every field in order unless the caller authored otherwise", () => {
  expect(defaultLayout(FIELDS)).toEqual({
    v: 2,
    columns: [
      { field: "a", visible: true },
      { field: "b", visible: true },
      { field: "c", visible: true },
    ],
    groupBy: [],
    sort: [],
    filter: [],
  });
  expect(defaultLayout(FIELDS, { groupBy: ["b"] }).groupBy).toEqual(["b"]);
});

test("a document is fitted to the fields the frame has", () => {
  const document = layout({
    columns: [
      { field: "gone", visible: true },
      { field: "b", visible: false },
    ],
    sort: [
      { field: "b", dir: "desc" },
      { field: "gone", dir: "asc" },
    ],
    filter: [
      { field: "b", value: "x" },
      { field: "gone", value: "y" },
    ],
  });

  const state = stateFrom(document, FIELDS, new Set(FIELDS));

  expect(state.order).toEqual(["b", "a", "c"]);
  expect(state.visibility).toEqual({ b: false });
  expect(state.sorting).toEqual([{ id: "b", desc: true }]);
  expect(state.filters).toEqual([{ id: "b", value: { value: "x" } }]);
});

test("a document can only group a groupable field", () => {
  const document = layout({ groupBy: ["a", "b"] });

  // A pinned lead column is not groupable, and a document written before the caller changed `lead=` can name one:
  // two config-panel lists would then share one sortable id.
  expect(stateFrom(document, FIELDS, new Set(["b"])).grouping).toEqual(["b"]);
  expect(stateFrom(document, FIELDS, new Set()).grouping).toEqual([]);
});

test("a state round-trips through its document", () => {
  const state: LayoutState = {
    order: ["c", "a", "b"],
    visibility: { a: false },
    sorting: [{ id: "c", desc: false }],
    filters: [
      { id: "b", value: { min: 2 } },
      { id: "c", value: "not a filter value" },
    ],
    grouping: ["c"],
  };

  const document = documentOf(state);

  expect(document).toEqual({
    v: 2,
    columns: [
      { field: "c", visible: true },
      { field: "a", visible: false },
      { field: "b", visible: true },
    ],
    groupBy: ["c"],
    sort: [{ field: "c", dir: "asc" }],
    filter: [{ field: "b", min: 2 }],
  });
  expect(stateFrom(document, ["a", "b", "c"], new Set(["c"]))).toEqual({
    ...state,
    filters: [{ id: "b", value: { min: 2 } }],
  });
});

test("an order follows the fields that came and went", () => {
  expect(mergeOrder(["c", "gone", "a"], FIELDS)).toEqual(["c", "a", "b"]);
});

test("the config lists keep the pinned fields first and the grouped ones apart", () => {
  const state: LayoutState = {
    order: ["a", "b", "c"],
    visibility: { c: false },
    sorting: [],
    filters: [],
    grouping: ["b"],
  };

  expect(listsOf(state, ["a"])).toEqual({ group: ["b"], visible: ["a"], hidden: ["c"] });
});

test("an edited list keeps a grouped field in its slot", () => {
  const state: LayoutState = {
    order: ["a", "b", "c"],
    visibility: {},
    sorting: [],
    filters: [],
    grouping: ["b"],
  };

  const applied = applyLists(state, { group: ["b"], visible: ["c"], hidden: ["a"] });

  expect(applied.order).toEqual(["c", "b", "a"]);
  expect(applied.visibility).toEqual({ a: false });
  expect(applied.grouping).toEqual(["b"]);
});

test("a blank range input means the facet's own end", () => {
  expect(boundFrom("", 3.5)).toBe(3.5);
  expect(boundFrom("  ", 3.5)).toBe(3.5);
  expect(boundFrom("2", 3.5)).toBe(2);
  expect(boundFrom("nope", 3.5)).toBeNull();
});
