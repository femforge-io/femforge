import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import { Terminal, type ITerminalOptions } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

const FONT_FAMILY = "JetBrains Mono";
const FONT_SIZE = 13;

export interface TermSize {
  cols: number;
  rows: number;
}

export interface TermHandle {
  write(text: string): void;
  clear(): void;
  fit(): void;
  scrollToBottom(): void;
  rows(): number;
}

export interface TermProps {
  ref?: Ref<TermHandle>;
  options?: ITerminalOptions;
  onData?: (data: string) => void;
  onResize?: (size: TermSize) => void;
  className?: string;
}

export function Term({ ref, options = {}, onData, onResize, className }: TermProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const onDataRef = useRef(onData);
  const onResizeRef = useRef(onResize);
  onDataRef.current = onData;
  onResizeRef.current = onResize;

  // The terminal is created once; `options` are read at mount and the callbacks through refs.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const terminal = new Terminal({
      convertEol: true,
      scrollback: 5000,
      fontSize: FONT_SIZE,
      fontFamily: FONT_FAMILY,
      ...options,
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    termRef.current = terminal;
    fitRef.current = fit;

    const disposables = [
      terminal.onData((data) => onDataRef.current?.(data)),
      terminal.onResize((size) => onResizeRef.current?.(size)),
    ];
    const observer = new ResizeObserver(() => fit.fit());
    let disposed = false;

    // Cell metrics are measured at open(), so the font has to be loaded first; writes before open() are buffered by xterm.
    void document.fonts.load(`${FONT_SIZE}px "${FONT_FAMILY}"`).then(() => {
      if (disposed) return;

      terminal.open(host);
      // xterm's hidden input is a form field Chrome reports as unnamed; it is never submitted, so a name is all it needs.
      if (terminal.textarea) terminal.textarea.name = "ff-terminal";

      fit.fit();
      observer.observe(host);
    });

    return () => {
      disposed = true;
      observer.disconnect();
      for (const disposable of disposables) disposable.dispose();
      terminal.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  useImperativeHandle(ref, () => ({
    write: (text) => termRef.current?.write(text),
    clear: () => termRef.current?.clear(),
    fit: () => fitRef.current?.fit(),
    scrollToBottom: () => termRef.current?.scrollToBottom(),
    rows: () => termRef.current?.rows ?? 0,
  }));

  return <div ref={hostRef} className={className} style={{ width: "100%", height: "100%", overflow: "hidden" }} />;
}
