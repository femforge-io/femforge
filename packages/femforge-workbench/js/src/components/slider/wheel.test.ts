import { describe, expect, it } from "vitest";

import { stepped, wheelSteps } from "./wheel";

describe("wheelSteps", () => {
  it("steps once per notch, up for a turn away, and at least once for a trackpad's small delta", () => {
    expect(wheelSteps(-100, 0)).toBe(1);
    expect(wheelSteps(300, 0)).toBe(-3);
    expect(wheelSteps(4, 0)).toBe(-1);
    expect(wheelSteps(-3, 1)).toBe(1);
  });
});

describe("stepped", () => {
  it("moves a single thumb on the step grid and stops at the ends", () => {
    expect(stepped([2], 0.5, 1, 0.5, 6, 0.5)).toEqual([2.5]);
    expect(stepped([0.15], 0, 1, 0, 1, 0.05)).toEqual([0.2]);
    expect(stepped([6], 1, 3, 0.5, 6, 0.5)).toEqual([6]);
    expect(stepped([0.5], 0, -1, 0.5, 6, 0.5)).toEqual([0.5]);
  });

  it("moves the thumb nearest the pointer and never past its neighbour", () => {
    expect(stepped([10, 90], 0.2, 1, 0, 100, 1)).toEqual([11, 90]);
    expect(stepped([10, 90], 0.8, -1, 0, 100, 1)).toEqual([10, 89]);
    expect(stepped([10, 11], 0.1, 5, 0, 100, 1)).toEqual([11, 11]);
  });
});
