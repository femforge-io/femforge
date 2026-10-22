/** What a key does to the focused row of an entity grid. */
export type RowAction = "select" | "frame" | "show" | "clear";

/** The row index a navigation key moves the focus to, or null for any other key; the ends hold the focus. */
export function nextFocus(key: string, index: number, count: number): number | null {
  if (count <= 0) return null;

  const last = count - 1;
  switch (key) {
    case "ArrowDown":
      return Math.min(index + 1, last);
    case "ArrowUp":
      return Math.max(index - 1, 0);
    case "Home":
      return 0;
    case "End":
      return last;
    default:
      return null;
  }
}

/** The modifier keys held with a key press. */
export interface Modifiers {
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

/** The action a key takes on the focused row, or null for a key the grid leaves alone, a chord with Ctrl, Meta or Alt included so the browser keeps its shortcuts. */
export function action(key: string, held: Modifiers): RowAction | null {
  if (held.ctrlKey || held.metaKey || held.altKey) return null;

  switch (key) {
    case "Enter":
    case " ":
      return "select";
    case "f":
    case "F":
      return "frame";
    case "s":
    case "S":
      return "show";
    case "Escape":
      return "clear";
    default:
      return null;
  }
}
