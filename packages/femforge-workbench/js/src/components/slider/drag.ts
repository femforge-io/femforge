/** The style properties that switch text selection, as the root element carries them. */
export interface SelectStyle {
  userSelect: string;
  webkitUserSelect: string;
}

/** Turns text selection off on `style` while a slider is dragged, so a pointer that leaves the track selects nothing; returns the release. */
export function holdSelection(style: SelectStyle): () => void {
  const before = { userSelect: style.userSelect, webkitUserSelect: style.webkitUserSelect };
  style.userSelect = "none";
  style.webkitUserSelect = "none";

  let released = false;

  return () => {
    if (released) return;

    released = true;
    style.userSelect = before.userSelect;
    style.webkitUserSelect = before.webkitUserSelect;
  };
}
