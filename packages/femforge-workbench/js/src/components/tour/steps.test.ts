import { expect, test } from "vitest";
import { clampStep, neighbour, padded, sameBox, visibleIndices, visibleSteps, type TourStep } from "./steps";

const STEPS: TourStep[] = [
  { target: "#a", title: "A", body: "First." },
  { target: "#gone", title: "Gone", body: "Never mounted." },
  { target: "#b", title: "B", body: "Second.", side: "right" },
  { target: "#c", title: "C", body: "Third." },
];

const PRESENT = new Set(["#a", "#b", "#c"]);
const isPresent = (target: string): boolean => PRESENT.has(target);

test("steps without a present target are skipped, the rest keep authoring order", () => {
  expect(visibleIndices(STEPS, isPresent)).toEqual([0, 2, 3]);
  expect(visibleSteps(STEPS, isPresent).map((step) => step.title)).toEqual(["A", "B", "C"]);
  expect(visibleSteps(STEPS, () => false)).toEqual([]);
  expect(visibleSteps([], isPresent)).toEqual([]);
});

test("clamping keeps an index inside the visible list and maps an empty list to 0", () => {
  expect(clampStep(1, 3)).toBe(1);
  expect(clampStep(-2, 3)).toBe(0);
  expect(clampStep(7, 3)).toBe(2);
  expect(clampStep(1.8, 3)).toBe(1);
  expect(clampStep(Number.NaN, 3)).toBe(0);
  expect(clampStep(4, 0)).toBe(0);
});

test("the neighbour is found by authoring order, so a vanished current step still moves on", () => {
  const visible = [0, 2, 3];
  expect(neighbour(visible, 0, 1)).toBe(1);
  expect(neighbour(visible, 2, -1)).toBe(0);
  expect(neighbour(visible, 1, 1)).toBe(1);
  expect(neighbour(visible, 1, -1)).toBe(0);
  expect(neighbour(visible, 3, 1)).toBe(-1);
  expect(neighbour(visible, 0, -1)).toBe(-1);
});

test("the spotlight box grows by the padding and equal boxes compare equal", () => {
  const box = padded({ top: 10, left: 20, width: 100, height: 30 }, 4);
  expect(box).toEqual({ top: 6, left: 16, width: 108, height: 38 });
  expect(sameBox(box, { ...box })).toBe(true);
  expect(sameBox(box, { ...box, width: 109 })).toBe(false);
  expect(sameBox(null, null)).toBe(true);
  expect(sameBox(box, null)).toBe(false);
});
