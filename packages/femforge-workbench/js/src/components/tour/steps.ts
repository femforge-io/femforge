export type TourSide = "top" | "bottom" | "left" | "right";

export interface TourStep {
  target: string;
  title: string;
  body: string;
  side?: TourSide;
}

export interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

/** The authoring-order indices of the steps whose target is present. */
export function visibleIndices(steps: readonly TourStep[], isPresent: (target: string) => boolean): number[] {
  return steps.flatMap((step, index) => (isPresent(step.target) ? [index] : []));
}

/** The steps whose target is present, in authoring order. */
export function visibleSteps(steps: readonly TourStep[], isPresent: (target: string) => boolean): TourStep[] {
  return visibleIndices(steps, isPresent).map((index) => steps[index]);
}

/** An index forced into `[0, count - 1]`, 0 when there is nothing to index. */
export function clampStep(index: number, count: number): number {
  if (count <= 0 || !Number.isFinite(index)) return 0;

  return Math.min(Math.max(Math.trunc(index), 0), count - 1);
}

/** The position in `visible` of the first step after (`delta` 1) or the last before (`delta` -1) authoring index `from`; -1 past either end. */
export function neighbour(visible: readonly number[], from: number, delta: 1 | -1): number {
  if (delta > 0) return visible.findIndex((index) => index > from);

  const before = visible.filter((index) => index < from);

  return before.length - 1;
}

/** A rect grown by `padding` on every side. */
export function padded(rect: Box, padding: number): Box {
  return {
    top: rect.top - padding,
    left: rect.left - padding,
    width: rect.width + 2 * padding,
    height: rect.height + 2 * padding,
  };
}

/** Whether two boxes coincide, so a re-measure that moved nothing skips the re-render. */
export function sameBox(a: Box | null, b: Box | null): boolean {
  if (a === null || b === null) return a === b;

  return a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height;
}
