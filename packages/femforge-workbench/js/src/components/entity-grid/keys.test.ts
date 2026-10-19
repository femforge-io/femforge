import { expect, test } from "vitest";
import { action, nextFocus } from "./keys";

test("the arrows step one row and hold at the ends", () => {
  expect(nextFocus("ArrowDown", 0, 3)).toBe(1);
  expect(nextFocus("ArrowDown", 2, 3)).toBe(2);
  expect(nextFocus("ArrowUp", 0, 3)).toBe(0);
  expect(nextFocus("ArrowUp", 2, 3)).toBe(1);
});

test("Home and End jump to the first and the last row", () => {
  expect(nextFocus("Home", 2, 5)).toBe(0);
  expect(nextFocus("End", 0, 5)).toBe(4);
});

test("other keys and an empty grid move nothing", () => {
  expect(nextFocus("a", 1, 3)).toBeNull();
  expect(nextFocus("ArrowDown", 0, 0)).toBeNull();
});

test("Enter and Space select, G toggles the glyph, F frames, S shows and Escape clears", () => {
  expect(action("Enter")).toBe("select");
  expect(action(" ")).toBe("select");
  expect(action("g")).toBe("glyph");
  expect(action("G")).toBe("glyph");
  expect(action("f")).toBe("frame");
  expect(action("F")).toBe("frame");
  expect(action("s")).toBe("show");
  expect(action("S")).toBe("show");
  expect(action("Escape")).toBe("clear");
  expect(action("x")).toBeNull();
});
