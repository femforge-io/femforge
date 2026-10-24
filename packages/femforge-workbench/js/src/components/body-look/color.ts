/** Where a body's color comes from, as the swatch and the popover tabs read it; `mixed` for a group whose bodies differ. */
export type Paint = "field" | "colormap" | "solid" | "mixed";

/** A color as hue in degrees and saturation and value from 0 to 1. */
export interface Hsv {
  h: number;
  s: number;
  v: number;
}

export const CHECKER = "repeating-conic-gradient(var(--checker-a) 0 25%, var(--checker-b) 0 50%) 0 0 / 6px 6px";

/** `#RRGGBB` (or `#RGB`) as hue, saturation and value; black for anything else. */
export function hexToHsv(hex: string): Hsv {
  const digits = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim())?.[1];
  if (!digits) return { h: 0, s: 0, v: 0 };

  const full = digits.length === 3 ? [...digits].map((d) => d + d).join("") : digits;
  const [r, g, b] = [0, 2, 4].map((at) => Number.parseInt(full.slice(at, at + 2), 16) / 255);
  const high = Math.max(r, g, b);
  const span = high - Math.min(r, g, b);
  let h = 0;
  if (span > 0) {
    if (high === r) h = ((g - b) / span) % 6;
    else if (high === g) h = (b - r) / span + 2;
    else h = (r - g) / span + 4;
  }

  return { h: (h * 60 + 360) % 360, s: high === 0 ? 0 : span / high, v: high };
}

/** Hue, saturation and value as `#RRGGBB`. */
export function hsvToHex({ h, s, v }: Hsv): string {
  const channel = (n: number): number => {
    const k = (n + h / 60) % 6;
    return v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
  };

  return `#${[channel(5), channel(3), channel(1)]
    .map((x) =>
      Math.round(x * 255)
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")
    .toUpperCase()}`;
}

/** A left-to-right (or reversed, right-to-left) CSS gradient turned to run corner to corner, which reads in a circle. */
export function diagonal(gradient: string): string {
  return gradient
    .replace(/linear-gradient\(\s*to right\s*,/, "linear-gradient(135deg,")
    .replace(/linear-gradient\(\s*to left\s*,/, "linear-gradient(315deg,");
}

/** The fill of a swatch: the field colormap, one color, or equal diagonal bands of a group's distinct colors over the field colormap when some of its bodies follow the field. */
export function swatchFill(
  paint: Paint,
  colors: readonly (string | null)[],
  fieldGradient: string | undefined,
): string {
  const distinct = [...new Set(colors.filter((color): color is string => Boolean(color)))];
  const field = fieldGradient ? diagonal(fieldGradient) : "transparent";
  if (paint === "field" || distinct.length === 0) return field;

  const fielded = paint === "mixed" && colors.some((color) => !color);
  if (distinct.length === 1 && !fielded) return distinct[0];

  const share = fielded ? 50 : 100;
  const band = share / distinct.length;
  const stops = distinct.map(
    (color, index) => `${color} ${(index * band).toFixed(1)}% ${((index + 1) * band).toFixed(1)}%`,
  );
  const bands = `linear-gradient(135deg, ${stops.join(", ")}${fielded ? `, transparent ${share}%` : ""})`;

  return fielded ? `${bands}, ${field}` : bands;
}

/** An opacity as the popover and the row read it, such as `35 %`. */
export function percent(opacity: number): string {
  return `${Math.round(opacity * 100)} %`;
}
