import "./styles.css";
import type { ElementType } from "react";
import { isComponentName, tagName } from "./tags";

type Module = Record<string, unknown>;

const RESIZE_LOOP = "ResizeObserver loop";

// Chrome raises this benign error on busy resizes and the trame client forwards every window error to the server log.
window.addEventListener(
  "error",
  (event) => {
    if (event.message.startsWith(RESIZE_LOOP)) event.stopImmediatePropagation();
  },
  true,
);

const LIBRARY = import.meta.glob<Module>("./components/*.tsx", { eager: true });
const UI = import.meta.glob<Module>("./components/ui/*.tsx", { eager: true });

function isComponent(value: unknown): value is ElementType {
  return typeof value === "function" || (typeof value === "object" && value !== null && "$$typeof" in value);
}

function collect(modules: Record<string, Module>, into: Map<string, ElementType>): void {
  for (const path of Object.keys(modules).sort()) {
    for (const [name, value] of Object.entries(modules[path])) {
      if (!isComponentName(name) || !isComponent(value)) continue;

      const tag = tagName(name);
      if (!into.has(tag)) into.set(tag, value);
    }
  }
}

// Library components register first, so `ff-table` is the TanStack table and shadcn's `Table` namesake is skipped.
const REGISTRY = new Map<string, ElementType>();
collect(LIBRARY, REGISTRY);
collect(UI, REGISTRY);

export const tags: readonly string[] = [...REGISTRY.keys()].sort();

export function install(registerTag: RegisterTag): void {
  for (const [tag, component] of REGISTRY) registerTag(tag, component);
}
