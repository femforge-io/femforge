import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** tailwind-merge, told that the theme's `text-label` and `text-body` are font sizes, so either overrides a `text-sm` instead of reading as a color. */
const twMerge = extendTailwindMerge({ extend: { classGroups: { "font-size": [{ text: ["label", "body"] }] } } });

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
