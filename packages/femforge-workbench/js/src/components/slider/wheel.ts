const WHEEL_NOTCH = 100;
const WHEEL_LINE = 33;

/** How many steps a wheel turn moves a slider: one per notch, at least one, up the track for a turn away from the user. */
export function wheelSteps(deltaY: number, deltaMode: number): number {
  const pixels = deltaMode === 1 ? deltaY * WHEEL_LINE : deltaY;
  const notches = Math.max(1, Math.round(Math.abs(pixels) / WHEEL_NOTCH));

  return -Math.sign(pixels) * notches;
}

/** The values after moving the thumb nearest `fraction` of the track by `steps`, kept between its neighbours and on the step grid. */
export function stepped(
  values: number[],
  fraction: number,
  steps: number,
  min: number,
  max: number,
  step: number,
): number[] {
  const pointed = min + fraction * (max - min);
  const index = values.reduce(
    (best, value, at) => (Math.abs(value - pointed) < Math.abs(values[best] - pointed) ? at : best),
    0,
  );
  const low = index > 0 ? values[index - 1] : min;
  const high = index < values.length - 1 ? values[index + 1] : max;
  const decimals = (String(step).split(".")[1] ?? "").length;
  const moved = Number((min + Math.round((values[index] + steps * step - min) / step) * step).toFixed(decimals));
  const next = [...values];
  next[index] = Math.min(high, Math.max(low, moved));

  return next;
}
