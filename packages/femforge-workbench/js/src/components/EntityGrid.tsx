import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type ReactNode, type Ref } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ConditionCardView, type ConditionCard } from "@/components/Fields";
import { Icon } from "@/components/Icon";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { IconName } from "@/icons";
import { Glide } from "@/lib/glide";
import { cn } from "@/lib/utils";
import { action, nextFocus } from "./entity-grid/keys";

const VIRTUAL_ROWS = 200;
const ROW_PX = 26;
const CARD_PX = 64;
const OVERSCAN = 12;
const COLUMNS = "grid grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-x-2";
const ROW_KEYS = "Enter F S Escape";
const POPUP = '[data-radix-popper-content-wrapper], [role="dialog"]';

/** A body a boundary touches, by its name and its body color. */
export interface BodyChip {
  name: string;
  color?: string;
}

/** One body or boundary group: its kind icon, its name or the bodies it touches, its material and measure, and its conditions as cards. */
export interface EntityRow {
  id: string;
  name: string;
  detail?: string;
  detail_tip?: string;
  material?: string;
  material_tip?: string;
  tip?: string[];
  kind_icon?: IconName;
  chips?: BodyChip[];
  cards?: ConditionCard[];
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
  onFrame?: (id: string) => void;
  onShow?: (id: string) => void;
  onHover?: (id: string) => void;
  className?: string;
}

interface Handlers {
  onSelect?: (id: string) => void;
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
      <TooltipContent side="right" className="max-w-80" definition>
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

/** A boundary's name as the bodies it touches: each body's color chip and name, joined by a two-way arrow. */
function Chips({ chips }: { chips: BodyChip[] }) {
  return (
    <span className="flex min-w-0 items-center gap-1">
      {chips.map((chip, index) => (
        <span key={`${chip.name}-${index}`} className="flex min-w-0 items-center gap-1">
          {index ? <span className="shrink-0 text-muted-foreground">↔</span> : null}
          {chip.color ? (
            <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ background: chip.color }} />
          ) : null}
          <span className="min-w-0 truncate">{chip.name}</span>
        </span>
      ))}
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
  onFrame,
  onShow,
  onHover,
}: RowViewProps) {
  const [keyboard, setKeyboard] = useState(false);
  const cards = row.cards ?? [];
  const indent = row.member ? "pl-8" : "pl-6";
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
        "group/row relative flex flex-col gap-1 border-l-2 border-transparent pr-1 pl-1.5 outline-none",
        cards.length || row.hidden ? "pb-1.5" : "",
        "transition-[background-color] duration-(--duration-micro) ease-instrument",
        interactive && "cursor-pointer focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset",
        interactive && !selected && "hover:bg-wash-hover",
        row.dimmed && "opacity-60",
      )}
    >
      <div className={cn(COLUMNS, "h-6.5")}>
        <span role="gridcell" aria-hidden className="flex items-center text-muted-foreground">
          {row.kind_icon ? <Icon name={row.kind_icon} size={14} /> : null}
        </span>
        <span role="gridcell" className={cn("flex min-w-0 items-center", row.member && "pl-3")}>
          <Tipped
            tip={nameTip(row)}
            open={keyboard && focused}
            className={cn("block min-w-0 truncate", row.group ? "font-medium" : "font-semibold")}
          >
            {row.chips?.length ? <Chips chips={row.chips} /> : row.name}
          </Tipped>
        </span>
        <span role="gridcell" className="flex min-w-0 items-center justify-end gap-2">
          {row.material ? (
            <Tipped
              tip={row.material_tip}
              className="flex h-5 items-center rounded-md bg-readonly px-1.5 text-label text-readonly-foreground"
            >
              {row.material}
            </Tipped>
          ) : null}
          <Tipped tip={row.detail_tip} className="block truncate text-muted-foreground tabular-nums">
            {row.detail ?? ""}
          </Tipped>
        </span>
      </div>
      {cards.length ? (
        <div className={cn("flex flex-col gap-1", indent)}>
          {cards.map((card, number) => (
            <ConditionCardView key={number} card={card} />
          ))}
        </div>
      ) : null}
      {row.hidden ? (
        <div className={cn("text-label leading-4 text-muted-foreground", indent)}>
          Hidden in Field Viewer ·{" "}
          <button
            type="button"
            tabIndex={-1}
            aria-keyshortcuts="S"
            onClick={(event) => {
              event.stopPropagation();
              onShow?.(row.id);
            }}
            className="cursor-pointer text-foreground underline underline-offset-2"
          >
            Show
          </button>
        </div>
      ) : null}
    </div>
  );
}

function HeaderRow({ columns }: { columns: string[] }) {
  const [name, detail] = columns;

  return (
    <div
      role="row"
      aria-rowindex={1}
      className={cn(COLUMNS, "h-5 border-l-2 border-transparent pr-1 pl-1.5 text-label text-muted-foreground")}
    >
      <span role="columnheader" aria-hidden />
      <span role="columnheader" className="min-w-0 truncate">
        {name ?? ""}
      </span>
      <span role="columnheader" className="min-w-0 truncate text-right">
        {detail ?? ""}
      </span>
    </div>
  );
}

/** The selected row's marker, which slides between the rows of one grid and fades when the selection moves to another. */
function Chosen({ selected }: { selected?: string }) {
  return (
    <Glide
      chosen={selected}
      selector='[role="row"][aria-selected="true"]'
      className="border-l-2 border-primary bg-wash-selected"
    />
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
    estimateSize: (index) => ROW_PX + (rows[index]?.cards?.length ?? 0) * CARD_PX,
    overscan: OVERSCAN,
  });
  useImperativeHandle(scroller, () => ({ toIndex: (index) => virtualizer.scrollToIndex(index, { align: "auto" }) }));

  return (
    <div ref={viewport} className="max-h-[60vh] overflow-auto">
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        <Chosen selected={selected} />
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

/** Rows of bodies or boundaries as one grid under its column headers with a single tab stop: the arrows move over the rows a group heads, Enter selects, F frames, S shows a hidden body and Escape, pressed anywhere, clears; hover and focus name the row the scene outlines. */
export function EntityGrid({
  rows,
  selected,
  label,
  columns,
  onSelect,
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

  const holdsSelection = Boolean(selected) && rows.some((row) => row.id === selected);

  // Escape clears the selection wherever the focus is, the 3D view included, unless a popup is open to close. The listener captures, because
  // the 3D view keeps the keys it receives from the page; a key in a grid is left to the grid's own handler.
  useEffect(() => {
    if (!holdsSelection) return undefined;

    const clear = (event: globalThis.KeyboardEvent): void => {
      if (event.key !== "Escape" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (document.querySelector(POPUP) || (event.target instanceof Element && event.target.closest('[role="grid"]')))
        return;

      onSelect?.("");
    };
    window.addEventListener("keydown", clear, true);

    return () => window.removeEventListener("keydown", clear, true);
  }, [holdsSelection, onSelect]);

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
      case "frame":
        if (!row.dimmed) onFrame?.(row.id);
        break;
      case "show":
        if (row.hidden) onShow?.(row.id);
        break;
      case "clear":
        onSelect?.("");
        break;
    }
  }

  const handlers = { onSelect, onFrame, onShow, onHover, onFocusRow: setFocus, offset };

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
        className={cn("ff-entity-grid relative flex w-full flex-col text-label", className)}
      >
        {virtual ? null : <Chosen selected={selected} />}
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
