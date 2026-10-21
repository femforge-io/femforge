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
const COLUMNS = "grid grid-cols-[minmax(0,1fr)_5.5rem_2.25rem] items-center gap-x-2";
const ROW_KEYS = "Enter G F S Escape";

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
  glyph_note?: string;
  fit?: boolean;
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
  columns?: string[];
  onSelect?: (id: string) => void;
  onToggleGlyph?: (id: string) => void;
  onFrame?: (id: string) => void;
  onShow?: (id: string) => void;
  onHover?: (id: string) => void;
  className?: string;
}

interface Handlers {
  onSelect?: (id: string) => void;
  onToggleGlyph?: (id: string) => void;
  onFrame?: (id: string) => void;
  onShow?: (id: string) => void;
  onHover?: (id: string) => void;
}

interface RowViewProps extends Handlers {
  row: EntityRow;
  index: number;
  offset: number;
  focused: boolean;
  selected: boolean;
  onFocusRow(index: number): void;
}

interface RowsProps extends Handlers {
  rows: EntityRow[];
  focus: number;
  offset: number;
  selected?: string;
  onFocusRow(index: number): void;
}

/** Scrolls a row index into view. */
interface Scroller {
  toIndex(index: number): void;
}

function Tipped({
  tip,
  open,
  className,
  children,
}: {
  tip?: ReactNode;
  open?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const [hovered, setHovered] = useState(false);
  if (!tip) return <span className={className}>{children}</span>;

  return (
    <Tooltip open={Boolean(open) || hovered} onOpenChange={setHovered}>
      <TooltipTrigger asChild>
        <span className={className}>{children}</span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80">
        {tip}
      </TooltipContent>
    </Tooltip>
  );
}

/** The name tooltip: the Elmer name in mono, the rest of the facts, the detail's own tip, then why a dimmed row cannot be highlighted. */
function nameTip(row: EntityRow): ReactNode {
  const [first, ...rest] = row.tip ?? [];
  if (first === undefined && !row.dim_tip && !row.detail_tip) return null;

  return (
    <div className="flex flex-col gap-0.5">
      {first !== undefined ? <span className="font-mono">{first}</span> : null}
      {rest.map((line) => (
        <span key={line}>{line}</span>
      ))}
      {row.detail_tip ? <span>{row.detail_tip}</span> : null}
      {row.dim_tip ? <span>{row.dim_tip}</span> : null}
    </div>
  );
}

function GlyphCell({ row, onToggleGlyph }: { row: EntityRow; onToggleGlyph?: (id: string) => void }) {
  if (row.group) return null;

  if (row.icon && row.glyph_shown !== undefined) {
    const overruled = Boolean(row.glyph_note);

    return (
      <button
        type="button"
        tabIndex={-1}
        aria-pressed={row.glyph_shown}
        aria-keyshortcuts="G"
        aria-label={overruled ? `Show glyph for ${row.name}, ${row.glyph_note}` : `Show glyph for ${row.name}`}
        title={row.glyph_note}
        onClick={(event) => {
          event.stopPropagation();
          onToggleGlyph?.(row.id);
        }}
        className={cn(
          "flex size-6 cursor-pointer items-center justify-center rounded-sm hover:bg-wash-hover",
          (overruled || row.glyph_shown === false) && "text-muted-foreground opacity-50",
        )}
      >
        <Icon name={row.icon} size={14} />
      </button>
    );
  }

  if (row.icon) {
    return (
      <span aria-hidden className="flex size-6 items-center justify-center text-muted-foreground">
        <Icon name={row.icon} size={14} />
      </span>
    );
  }

  return (
    <span aria-hidden className="text-muted-foreground">
      ─
    </span>
  );
}

function RowView({
  row,
  index,
  offset,
  focused,
  selected,
  onFocusRow,
  onSelect,
  onToggleGlyph,
  onFrame,
  onShow,
  onHover,
}: RowViewProps) {
  const [keyboard, setKeyboard] = useState(false);
  const lines = row.lines ?? [];
  const indent = row.member ? "pl-6" : "pl-3";
  const interactive = !row.group;

  return (
    <div
      role="row"
      aria-rowindex={index + offset + 1}
      aria-selected={interactive ? selected : undefined}
      aria-label={row.label ?? row.name}
      aria-keyshortcuts={interactive ? ROW_KEYS : undefined}
      tabIndex={interactive ? (focused ? 0 : -1) : undefined}
      data-index={index}
      data-id={row.id}
      onFocus={
        interactive
          ? (event) => {
              onFocusRow(index);
              onHover?.(row.id);
              if (event.target === event.currentTarget) setKeyboard(event.currentTarget.matches(":focus-visible"));
            }
          : undefined
      }
      onPointerEnter={interactive ? () => onHover?.(row.id) : undefined}
      onBlur={interactive ? () => setKeyboard(false) : undefined}
      onClick={interactive ? () => onSelect?.(row.id) : undefined}
      onDoubleClick={interactive ? () => onFrame?.(row.id) : undefined}
      className={cn(
        "group/row flex flex-col border-l-2 pr-0.5 pl-1.5 outline-none",
        interactive && "cursor-pointer focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        selected && interactive ? "border-primary bg-wash-selected hover:bg-wash-selected-hover" : "border-transparent",
        interactive && !selected && "hover:bg-wash-hover",
        row.dimmed && "opacity-60",
      )}
    >
      <div className={cn(COLUMNS, "h-6")}>
        <span role="gridcell" className="flex min-w-0 items-center gap-1">
          <Tipped
            tip={nameTip(row)}
            open={keyboard && focused}
            className={cn("block min-w-0 truncate", row.group && "font-medium", row.member && "pl-3")}
          >
            {row.name}
          </Tipped>
          {row.fit && interactive ? (
            <button
              type="button"
              tabIndex={-1}
              aria-label={`Fit view to ${row.name}`}
              aria-keyshortcuts="F"
              title={`Fit view to ${row.name}`}
              onClick={(event) => {
                event.stopPropagation();
                onFrame?.(row.id);
              }}
              className={cn(
                "flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm text-muted-foreground hover:bg-wash-hover hover:text-foreground",
                selected ? "visible" : "invisible group-hover/row:visible group-focus/row:visible",
              )}
            >
              <Icon name="maximize-2" size={12} />
            </button>
          ) : null}
        </span>
        <span role="gridcell" className="min-w-0 text-right">
          <Tipped tip={row.detail_tip} className="block truncate text-muted-foreground tabular-nums">
            {row.detail ?? ""}
          </Tipped>
        </span>
        <span role="gridcell" className="flex justify-center">
          <GlyphCell row={row} onToggleGlyph={onToggleGlyph} />
        </span>
      </div>
      {lines.map((line, number) => (
        <div key={number} aria-hidden className={cn("pb-0.5 text-label leading-4 text-muted-foreground", indent)}>
          {line.parts.map((part, at) =>
            part.tip || part.muted ? (
              <Tipped key={at} tip={part.tip} className={part.muted ? "opacity-60" : undefined}>
                {part.text}
              </Tipped>
            ) : (
              <span key={at}>{part.text}</span>
            ),
          )}
        </div>
      ))}
      {row.hidden ? (
        <div className={cn("pb-0.5 text-label leading-4 text-muted-foreground", indent)}>
          Hidden in Field Viewer ·{" "}
          <button
            type="button"
            tabIndex={-1}
            aria-keyshortcuts="S"
            onClick={(event) => {
              event.stopPropagation();
              onShow?.(row.id);
            }}
            className="cursor-pointer text-primary underline-offset-2 hover:underline"
          >
            Show
          </button>
        </div>
      ) : null}
    </div>
  );
}

function HeaderRow({ columns }: { columns: string[] }) {
  const [name, detail, glyph] = columns;

  return (
    <div
      role="row"
      aria-rowindex={1}
      className={cn(COLUMNS, "h-5 border-l-2 border-transparent pr-0.5 pl-1.5 text-label text-muted-foreground")}
    >
      <span role="columnheader" className="min-w-0 truncate">
        {name ?? ""}
      </span>
      <span role="columnheader" className="min-w-0 truncate text-right">
        {detail ?? ""}
      </span>
      <span role="columnheader" className="justify-self-center whitespace-nowrap">
        {glyph ?? ""}
      </span>
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

/** Rows of bodies or boundaries as one grid under its column headers with a single tab stop: the arrows move over the rows a group heads, Enter selects, G toggles the glyph, F frames, S shows a hidden body and Escape clears. */
export function EntityGrid({
  rows,
  selected,
  label,
  columns,
  onSelect,
  onToggleGlyph,
  onFrame,
  onShow,
  onHover,
  className,
}: EntityGridProps) {
  const root = useRef<HTMLDivElement | null>(null);
  const scroller = useRef<Scroller | null>(null);
  const moved = useRef(false);
  const [focus, setFocus] = useState(0);
  const stops = rows.flatMap((row, index) => (row.group ? [] : [index]));
  const shown = stops.includes(focus) ? focus : (stops.find((index) => index > focus) ?? stops.at(-1) ?? 0);
  const virtual = rows.length > VIRTUAL_ROWS;
  const offset = columns?.length && rows.length ? 1 : 0;

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
    const step = nextFocus(event.key, stops.indexOf(shown), stops.length);
    const next = step === null ? undefined : stops[step];
    if (next !== undefined) {
      event.preventDefault();
      moveTo(next);
      return;
    }

    const taken = action(event.key, event);
    const row = rows[shown];
    if (taken === null || row === undefined || row.group) return;

    event.preventDefault();
    switch (taken) {
      case "select":
        onSelect?.(row.id);
        break;
      case "glyph":
        if (row.icon && row.glyph_shown !== undefined) onToggleGlyph?.(row.id);
        break;
      case "frame":
        if (row.fit) onFrame?.(row.id);
        break;
      case "show":
        if (row.hidden) onShow?.(row.id);
        break;
      case "clear":
        onSelect?.("");
        break;
    }
  }

  const handlers = { onSelect, onToggleGlyph, onFrame, onShow, onHover, onFocusRow: setFocus, offset };

  return (
    <TooltipProvider>
      <div
        ref={root}
        role="grid"
        aria-label={label}
        aria-rowcount={rows.length + offset}
        onKeyDown={onKeyDown}
        onPointerLeave={() => {
          const focused = document.activeElement;
          if (!(root.current?.contains(focused) && focused?.matches(":focus-visible"))) onHover?.("");
        }}
        onBlur={(event) => {
          if (!root.current?.contains(event.relatedTarget as Node | null)) onHover?.("");
        }}
        className={cn("ff-entity-grid flex w-full flex-col text-label", className)}
      >
        {offset ? <HeaderRow columns={columns ?? []} /> : null}
        {virtual ? (
          <VirtualRows rows={rows} focus={shown} selected={selected} scroller={scroller} {...handlers} />
        ) : (
          <PlainRows rows={rows} focus={shown} selected={selected} {...handlers} />
        )}
      </div>
    </TooltipProvider>
  );
}
