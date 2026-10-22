import { expect, test } from "vitest";
import { action, nextFocus } from "./keys";

const NONE = { ctrlKey: false, metaKey: false, altKey: false };

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

test("Enter and Space select, F frames, S shows and Escape clears", () => {
  expect(action("Enter", NONE)).toBe("select");
  expect(action(" ", NONE)).toBe("select");
  expect(action("f", NONE)).toBe("frame");
  expect(action("F", NONE)).toBe("frame");
  expect(action("s", NONE)).toBe("show");
  expect(action("S", NONE)).toBe("show");
  expect(action("Escape", NONE)).toBe("clear");
  expect(action("x", NONE)).toBeNull();
  expect(action("g", NONE)).toBeNull();
});

test("a chord with Ctrl, Meta or Alt is left to the browser", () => {
  expect(action("f", { ...NONE, ctrlKey: true })).toBeNull();
  expect(action("s", { ...NONE, metaKey: true })).toBeNull();
  expect(action("g", { ...NONE, altKey: true })).toBeNull();
  expect(action("Enter", { ...NONE, ctrlKey: true })).toBeNull();
  expect(action("F", NONE)).toBe("frame");
});
