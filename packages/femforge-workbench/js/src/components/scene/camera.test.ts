import { describe, expect, it } from "vitest";

import { between, type Pose, type Vector } from "./camera";

function close(actual: Vector, expected: Vector): void {
  actual.forEach((value, i) => expect(value).toBeCloseTo(expected[i], 9));
}

const FRONT: Pose = { position: [0, -10, 0], focalPoint: [0, 0, 0], viewUp: [0, 0, 1], parallelScale: 2 };
const SIDE: Pose = { position: [10, 0, 0], focalPoint: [0, 0, 0], viewUp: [0, 0, 1], parallelScale: 8 };
const TOP: Pose = { position: [0, 0, 40], focalPoint: [0, 0, 0], viewUp: [0, 1, 0], parallelScale: 2 };

describe("between", () => {
  it("starts and ends on the two poses", () => {
    expect(between(FRONT, SIDE, 0)).toEqual(FRONT);
    expect(between(FRONT, SIDE, 1)).toEqual(SIDE);
    close(between(FRONT, SIDE, 1 - 1e-12).position, SIDE.position);
  });

  it("turns on the arc around the focal point, halfway at half time", () => {
    const half = between(FRONT, SIDE, 0.5);

    close(half.position, [10 * Math.SQRT1_2, -10 * Math.SQRT1_2, 0]);
    close(half.viewUp, [0, 0, 1]);
    expect(half.parallelScale).toBeCloseTo(4, 9);
  });

  it("passes the pole from a side view to the top without flipping the up vector", () => {
    for (const t of [0.25, 0.5, 0.75]) {
      const pose = between(FRONT, TOP, t);
      const back = pose.position.map((value, i) => value - pose.focalPoint[i]);
      const along = back[0] * pose.viewUp[0] + back[1] * pose.viewUp[1] + back[2] * pose.viewUp[2];

      expect(Math.abs(along)).toBeLessThan(1e-9);
      expect(Math.hypot(...pose.viewUp)).toBeCloseTo(1, 9);
      expect(pose.viewUp[1]).toBeGreaterThan(0);
    }
  });

  it("zooms geometrically, so equal steps of time scale the distance by equal factors", () => {
    const distance = (t: number) => Math.hypot(...between(FRONT, { ...FRONT, position: [0, -1000, 0] }, t).position);

    expect(distance(0.5)).toBeCloseTo(100, 6);
    expect(distance(0.25) / distance(0)).toBeCloseTo(distance(0.5) / distance(0.25), 6);
  });
});
