import { expect, test } from "vitest";
import { sectionsOf, stepped, type ComboboxItem } from "./sections";

const ITEMS: ComboboxItem[] = [
  { value: "viridis", label: "viridis", groups: ["Favorites", "Sequential"] },
  { value: "magma", label: "magma", groups: ["Sequential"] },
  { value: "vik", label: "vik", groups: ["Favorites", "Diverging"] },
  { value: "roma", label: "roma", groups: ["Diverging"] },
  { value: "", label: "Same as field" },
];

function headings(active: string[]): Array<[string, string[]]> {
  return sectionsOf(ITEMS, active).map((section) => [section.heading, section.items.map((item) => item.value)]);
}

test("with no filter every item shows once under its first group", () => {
  expect(headings([])).toEqual([
    ["Favorites", ["viridis", "vik"]],
    ["Sequential", ["magma"]],
    ["Diverging", ["roma"]],
    ["", [""]],
  ]);
});

test("filters keep the items of any active group, under the first active one", () => {
  expect(headings(["Diverging"])).toEqual([["Diverging", ["vik", "roma"]]]);
  expect(headings(["Sequential", "Diverging"])).toEqual([
    ["Sequential", ["viridis", "magma"]],
    ["Diverging", ["vik", "roma"]],
  ]);
  expect(headings(["Favorites", "Diverging"])).toEqual([
    ["Favorites", ["viridis", "vik"]],
    ["Diverging", ["roma"]],
  ]);
});

test("stepping wraps and starts from the first item when the value is unlisted", () => {
  expect(stepped(ITEMS, "viridis", 1)).toBe("magma");
  expect(stepped(ITEMS, "viridis", -1)).toBe("");
  expect(stepped(ITEMS, "", 1)).toBe("viridis");
  expect(stepped(ITEMS, "missing", 1)).toBe("viridis");
  expect(stepped([], "viridis", 1)).toBeUndefined();
});
