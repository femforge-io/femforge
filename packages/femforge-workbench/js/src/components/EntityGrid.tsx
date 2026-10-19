import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type ReactNode, type Ref } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Icon } from "@/components/Icon";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { IconName } from "@/icons";
import { cn } from "@/lib/utils";
import { action, nextFocus } from "./entity-grid/keys";

const VIRTUAL_ROWS = 200;
const ROW_PX = 24;
const LINE_PX = 18;
const OVERSCAN = 12;
const TOOLTIP_DELAY_MS = 300;

/** One run of text in a condition line; a muted part reads dimmer and may carry its own tooltip. */
export interface LinePart {
  text: string;
  muted?: boolean;
  tip?: string;
}

/** One condition a row received, as the parts it reads in. */
export interface ConditionLine {
  parts: LinePart[];
}

/** One body or boundary group: its name, its material or measure, its condition lines and its glyph toggle. */
export interface EntityRow {
  id: string;
  name: string;
  detail?: string;
  detail_tip?: string;
  tip?: string[];
  lines?: ConditionLine[];
  icon?: IconName;
  glyph_shown?: boolean;
  group?: boolean;
  member?: boolean;
  dimmed?: boolean;
  dim_tip?: string;
  hidden?: boolean;
  label?: string;
}

export interface EntityGridProps {
  rows: EntityRow[];
  selected?: string;
  label: string;
  onSelect?: (id: string) => void;
  onToggleGlyph?: (id: string) => void;
  onFrame?: (id: string) => void;
  onShow?: (id: string) => void;
  className?: string;
}

interface Handlers {
  onSelect?: (id: string) => void;
  onToggleGlyph?: (id: string) => void;
  onFrame?: (id: string) => void;
  onShow?: (id: string) => void;
}

interface RowViewProps extends Handlers {
  row: EntityRow;
  index: number;
  focused: boolean;
  selected: boolean;
  onFocusRow(index: number): void;
}

interface RowsProps extends Handlers {
  rows: EntityRow[];
  focus: number;
  selected?: string;
  onFocusRow(index: number): void;
}

/** Scrolls a row index into view. */
interface Scroller {
  toIndex(index: number): void;
}

function Tipped({ tip, className, children }: { tip?: ReactNode; className?: string; children: ReactNode }) {
  if (!tip) return <span className={className}>{children}</span>;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={className}>{children}</span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80">
        {tip}
      </TooltipContent>
    </Tooltip>
  );
}

/** The name tooltip: the Elmer name in mono, the rest of the facts, then why a dimmed row cannot be highlighted. */
function nameTip(row: EntityRow): ReactNode {
  const [first, ...rest] = row.tip ?? [];
  if (first === undefined && !row.dim_tip) return null;

  return (
    <div className="flex flex-col gap-0.5">
      {first !== undefined ? <span className="font-mono">{first}</span> : null}
      {rest.map((line) => (
        <span key={line}>{line}</span>
      ))}
      {row.dim_tip ? <span>{row.dim_tip}</span> : null}
    </div>
  );
}

function RowView({
  row,
  index,
  focused,
  selected,
  onFocusRow,
  onSelect,
  onToggleGlyph,
  onFrame,
  onShow,
}: RowViewProps) {
  const lines = row.lines ?? [];

  return (
    <div
      role="row"
      aria-rowindex={index + 1}
      aria-selected={selected}
      aria-label={row.label ?? row.name}
      tabIndex={focused ? 0 : -1}
      data-index={index}
      data-id={row.id}
      onFocus={() => onFocusRow(index)}
      onClick={() => onSelect?.(row.id)}
      onDoubleClick={() => onFrame?.(row.id)}
      className={cn(
        "flex cursor-default flex-col border-l-2 pr-0.5 pl-1.5 outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset",
        selected ? "border-primary bg-primary/10" : "border-transparent hover:bg-accent/40",
        row.dimmed && "opacity-60",
      )}
    >
      <div className="grid h-6 grid-cols-[minmax(0,1fr)_5.5rem_1.5rem] items-center gap-x-2">
        <span role="gridcell" className="min-w-0">
          <Tipped tip={nameTip(row)} className={cn("block truncate", row.group && "font-medium", row.member && "pl-3")}>
            {row.name}
          </Tipped>
        </span>
        <span role="gridcell" className="min-w-0 text-right">
          <Tipped tip={row.detail_tip} className="block truncate text-muted-foreground tabular-nums">
            {row.detail ?? ""}
          </Tipped>
        </span>
        <span role="gridcell" className="flex justify-center">
          {row.icon && row.glyph_shown !== undefined ? (
            <button
              type="button"
              tabIndex={-1}
              aria-pressed={row.glyph_shown ?? true}
              aria-label={`Show glyph for ${row.name}`}
              onClick={(event) => {
                event.stopPropagation();
                onToggleGlyph?.(row.id);
              }}
              className={cn(
                "flex size-5 items-center justify-center rounded-sm hover:bg-accent",
                row.glyph_shown === false && "text-muted-foreground opacity-50",
              )}
            >
              <Icon name={row.icon} size={14} />
            </button>
          ) : row.icon ? (
            <span aria-hidden className="flex size-5 items-center justify-center text-muted-foreground">
              <Icon name={row.icon} size={14} />
            </span>
          ) : (
            <span aria-hidden className="text-muted-foreground">
              ─
            </span>
          )}
        </span>
      </div>
      {lines.map((line, number) => (
        <div key={number} aria-hidden className="pb-0.5 pl-3 text-label leading-4 text-muted-foreground">
          {line.parts.map((part, at) =>
            part.tip || part.muted ? (
              <Tipped key={at} tip={part.tip} className={cn(part.muted && "opacity-60")}>
                {part.text}
              </Tipped>
            ) : (
              <span key={at}>{part.text}</span>
            ),
          )}
        </div>
      ))}
      {row.hidden ? (
        <div className="pb-0.5 pl-3 text-label leading-4 text-muted-foreground">
          Hidden in Field Viewer ·{" "}
          <button
            type="button"
            tabIndex={-1}
            aria-keyshortcuts="S"
            onClick={(event) => {
              event.stopPropagation();
              onShow?.(row.id);
            }}
            className="text-primary underline-offset-2 hover:underline"
          >
            Show
          </button>
        </div>
      ) : null}
    </div>
  );
}

function PlainRows({ rows, focus, selected, ...rest }: RowsProps) {
  return (
    <>
      {rows.map((row, index) => (
        <RowView
          key={row.id}
          row={row}
          index={index}
          focused={index === focus}
          selected={row.id === selected}
          {...rest}
        />
      ))}
    </>
  );
}

function VirtualRows({ rows, focus, selected, scroller, ...rest }: RowsProps & { scroller: Ref<Scroller | null> }) {
  const viewport = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => viewport.current,
    estimateSize: (index) => ROW_PX + (rows[index]?.lines?.length ?? 0) * LINE_PX,
    overscan: OVERSCAN,
  });
  useImperativeHandle(scroller, () => ({ toIndex: (index) => virtualizer.scrollToIndex(index, { align: "auto" }) }));

  return (
    <div ref={viewport} className="max-h-[60vh] overflow-auto">
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((item) => {
          const row = rows[item.index];
          if (!row) return null;

          return (
            <div
              key={row.id}
              ref={virtualizer.measureElement}
              data-index={item.index}
              className="absolute top-0 left-0 w-full"
              style={{ transform: `translateY(${item.start}px)` }}
            >
              <RowView
                row={row}
                index={item.index}
                focused={item.index === focus}
                selected={row.id === selected}
                {...rest}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Rows of bodies or boundaries as one grid with a single tab stop: the arrows move, Enter selects, G toggles the glyph, F frames, S shows a hidden body and Escape clears. */
export function EntityGrid({
  rows,
  selected,
  label,
  onSelect,
  onToggleGlyph,
  onFrame,
  onShow,
  className,
}: EntityGridProps) {
  const root = useRef<HTMLDivElement | null>(null);
  const scroller = useRef<Scroller | null>(null);
  const moved = useRef(false);
  const [focus, setFocus] = useState(0);
  const shown = Math.min(focus, Math.max(rows.length - 1, 0));
  const virtual = rows.length > VIRTUAL_ROWS;

  useEffect(() => {
    if (!moved.current) return;

    moved.current = false;
    root.current?.querySelector<HTMLElement>(`[role="row"][data-index="${shown}"]`)?.focus();
  }, [shown]);

  function moveTo(index: number): void {
    moved.current = true;
    scroller.current?.toIndex(index);
    setFocus(index);
    if (index === shown) root.current?.querySelector<HTMLElement>(`[role="row"][data-index="${index}"]`)?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const next = nextFocus(event.key, shown, rows.length);
    if (next !== null) {
      event.preventDefault();
      moveTo(next);
      return;
    }

    const taken = action(event.key);
    const row = rows[shown];
    if (taken === null || row === undefined) return;

    event.preventDefault();
    switch (taken) {
      case "select":
        onSelect?.(row.id);
        break;
      case "glyph":
        if (row.icon && row.glyph_shown !== undefined) onToggleGlyph?.(row.id);
        break;
      case "frame":
        onFrame?.(row.id);
        break;
      case "show":
        if (row.hidden) onShow?.(row.id);
        break;
      case "clear":
        onSelect?.("");
        break;
    }
  }

  const handlers = { onSelect, onToggleGlyph, onFrame, onShow, onFocusRow: setFocus };

  return (
    <TooltipProvider delayDuration={TOOLTIP_DELAY_MS}>
      <div
        ref={root}
        role="grid"
        aria-label={label}
        aria-rowcount={rows.length}
        onKeyDown={onKeyDown}
        className={cn("ff-entity-grid flex w-full flex-col text-label", className)}
      >
        {virtual ? (
          <VirtualRows rows={rows} focus={shown} selected={selected} scroller={scroller} {...handlers} />
        ) : (
          <PlainRows rows={rows} focus={shown} selected={selected} {...handlers} />
        )}
      </div>
    </TooltipProvider>
  );
}
