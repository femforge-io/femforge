import { useImperativeHandle, type CSSProperties, type Ref } from "react";
import type { Row } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Features, TableRow } from "./features";
import type { ColumnSpec } from "./layout";

const OVERSCAN = 20;
const GROUP_INDENT = 16;
const GROUP_PADDING = 8;

export type TableRowModel = Row<Features, TableRow>;

/** Scrolls the body to a row index of the shown row model. */
export interface Scroller {
  toIndex(index: number): void;
}

interface RowsProps {
  rows: TableRowModel[];
  specs: Map<string, ColumnSpec>;
  titles: Map<string, string>;
  pinned: Set<string>;
  leafCount: number;
  selectable: boolean;
  onRowClick?: (row: TableRow) => void;
  onToggleGroup(id: string): void;
}

interface BodyProps extends RowsProps {
  virtual: boolean;
  viewport: HTMLElement | null;
  rowHeight: number;
  scroller: Ref<Scroller | null>;
}

interface RowViewProps extends Omit<RowsProps, "rows"> {
  row: TableRowModel;
  index: number;
  style?: CSSProperties;
}

export function formatValue(value: unknown, numeric: boolean): string {
  if (value === null || value === undefined) return "";

  if (numeric && typeof value === "number") {
    return Number.isInteger(value)
      ? String(value)
      : value.toLocaleString("en-US", { maximumFractionDigits: 3, useGrouping: false });
  }

  return String(value);
}

function GroupRow({ row, index, specs, titles, leafCount, onToggleGroup, style }: RowViewProps) {
  const field = row.groupingColumnId ?? "";
  // getLeafRows() counts the nested group rows too, and only the data rows are worth reporting.
  const count = row.getLeafRows().filter((leaf) => !leaf.getIsGrouped()).length;

  return (
    <tr
      data-index={index}
      className="h-(--ff-row-height) cursor-pointer bg-card"
      style={style}
      onClick={() => onToggleGroup(row.id)}
    >
      <td colSpan={leafCount} className="border-b border-border p-0">
        <div
          className="sticky left-0 flex w-fit items-center gap-1 px-2 font-medium"
          style={{ paddingLeft: GROUP_PADDING + GROUP_INDENT * row.depth }}
        >
          {row.getIsExpanded() ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {row.depth > 0 ? <span className="text-muted-foreground">{titles.get(field) ?? field}:</span> : null}
          <span>{formatValue(row.getGroupingValue(field), Boolean(specs.get(field)?.numeric))}</span>
          <span className="text-muted-foreground tabular-nums">({count})</span>
        </div>
      </td>
    </tr>
  );
}

function DataRow({ row, index, specs, pinned, selectable, onRowClick, style }: RowViewProps) {
  const selected = row.getIsSelected();

  function onClick(): void {
    if (selectable) row.toggleSelected();

    onRowClick?.(row.original);
  }

  return (
    <tr
      data-index={index}
      data-state={selected ? "selected" : undefined}
      className={cn(
        "h-(--ff-row-height) hover:bg-wash-hover",
        selected && "bg-wash-selected",
        (selectable || onRowClick) && "cursor-pointer",
      )}
      style={style}
      onClick={onClick}
    >
      {row.getVisibleCells().map((cell) => {
        const numeric = Boolean(specs.get(cell.column.id)?.numeric);
        const stuck = pinned.has(cell.column.id);
        const size = cell.column.getSize();

        return (
          <td
            key={cell.id}
            className={cn(
              "truncate border-b border-border px-2 py-0.5",
              numeric && "text-right tabular-nums",
              stuck && "sticky z-10 bg-background",
            )}
            style={{
              width: size,
              minWidth: size,
              maxWidth: size,
              left: stuck ? cell.column.getStart("start") : undefined,
            }}
          >
            {cell.getIsPlaceholder() ? null : formatValue(cell.getValue(), numeric)}
          </td>
        );
      })}
    </tr>
  );
}

function RowView(props: RowViewProps) {
  return props.row.getIsGrouped() ? <GroupRow {...props} /> : <DataRow {...props} />;
}

function PlainBody({ rows, viewport, scroller, ...rest }: BodyProps) {
  useImperativeHandle(scroller, () => ({
    toIndex: (index) => viewport?.querySelector(`tr[data-index="${index}"]`)?.scrollIntoView({ block: "center" }),
  }));

  return (
    <tbody>
      {rows.map((row, index) => (
        <RowView key={row.id} row={row} index={index} {...rest} />
      ))}
    </tbody>
  );
}

function VirtualBody({ rows, viewport, rowHeight, scroller, ...rest }: BodyProps) {
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => viewport,
    estimateSize: () => rowHeight,
    overscan: OVERSCAN,
  });
  useImperativeHandle(scroller, () => ({ toIndex: (index) => virtualizer.scrollToIndex(index, { align: "center" }) }));

  const items = virtualizer.getVirtualItems();
  const first = items[0];
  const last = items[items.length - 1];
  const before = first ? first.start : 0;
  const after = last ? virtualizer.getTotalSize() - last.end : 0;

  return (
    <tbody>
      {before > 0 ? (
        <tr aria-hidden>
          <td colSpan={rest.leafCount} className="p-0" style={{ height: before }} />
        </tr>
      ) : null}
      {items.map((item) => {
        const row = rows[item.index];
        return row ? <RowView key={row.id} row={row} index={item.index} {...rest} /> : null;
      })}
      {after > 0 ? (
        <tr aria-hidden>
          <td colSpan={rest.leafCount} className="p-0" style={{ height: after }} />
        </tr>
      ) : null}
    </tbody>
  );
}

/** The rows, every column in the DOM; `virtual` keeps only the rows near the viewport mounted. */
export function Body(props: BodyProps) {
  return props.virtual ? <VirtualBody {...props} /> : <PlainBody {...props} />;
}
