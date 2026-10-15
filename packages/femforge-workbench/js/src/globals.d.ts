import type { ElementType } from "react";
import type * as monaco from "monaco-editor/editor/editor.api.js";
import type { TableDebug } from "./components/Table";

declare global {
  /** trame's tag registry setter; the last registration of a tag wins. */
  type RegisterTag = (tag: string, component: ElementType) => void;

  interface TrameState {
    get(key: string): unknown;
    set(key: string, value: unknown): void;
    watch(keys: string[], callback: (...values: unknown[]) => void): void;
  }

  interface TrameClient {
    state: TrameState;
    refs: Record<string, unknown>;
    trigger(name: string, args?: unknown[], kwargs?: Record<string, unknown>): Promise<unknown>;
    registerTag: RegisterTag;
  }

  interface FfLibrary {
    install(registerTag: RegisterTag): void;
    tags: readonly string[];
  }

  interface Window {
    trame?: TrameClient;
    Ff?: FfLibrary;
    __monaco?: typeof monaco;
    __ffTable?: TableDebug;
  }
}
