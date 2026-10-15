import { useEffect } from "react";

export interface ThemeProps {
  dark?: boolean;
}

/** Sets `.dark` and `color-scheme` on `<html>`, where Radix portals outside the layout root can see them. */
export function Theme({ dark = true }: ThemeProps): null {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", dark);
    root.style.colorScheme = dark ? "dark" : "light";
  }, [dark]);

  return null;
}
