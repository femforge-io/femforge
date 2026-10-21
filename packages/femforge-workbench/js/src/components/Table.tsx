import { useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type Ref } from "react";
import {
  functionalUpdate,
  useTable,
  type ExpandedState,
  type RowSelectionState,
  type Updater,
} from "@tanstack/react-table";
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, horizontalListSortingStrategy } from "@dnd-kit/sortable";
import { SlidersHorizontal, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { Body, type Scroller } from "./table/Body";
import { ColumnMenu } from "./table/ColumnMenu";
import { ConfigPanel } from "./table/ConfigPanel";
import { HeaderCell } from "./table/HeaderCell";
import { features, helper, type TableRow } from "./table/features";
import {
  applyLists,
  defaultLayout,
  documentOf,
  isRangeValue,
  isSetValue,
  isTextValue,
  layoutFrom,
  listsOf,
  mergeOrder,
  stateFrom,
  type ColumnSpec,
  type LayoutDefaults,
  type LayoutState,
  type LayoutV2,
} from "./table/layout";

export type {
  ColumnSpec,
  Facet,
  LayoutColumn,
  LayoutDefaults,
  LayoutFilter,
  LayoutSort,
  LayoutV2,
  RangeFilter,
  SetFilter,
  TextFilter,
} from "./table/layout";
export type { TableRow } from "./table/features";

const LAYOUT_DEBOUNCE_MS = 150;
const DEFAULT_LEAD = 1;
const DEFAULT_FACET_LIMIT = 50;
const DEFAULT_ROW_HEIGHT = 26;
const ROW_HEIGHT_VAR = "--ff-row-height";
const VIEWPORT_SELECTOR = "[data-slot=scroll-area-viewport]";
const MIN_WIDTH = 110;
const CHAR_WIDTH = 8;
const HEADER_PADDING = 88;

export type Selectable = "none" | "single" | "multiple";

export interface TableOptions {
  /** The persisted document, applied at mount and through `restore`. */
  layout?: LayoutV2 | null;
  /** The authored layout a missing document and Reset fall back to. */
  defaults?: LayoutDefaults;
  filterable?: boolean;
  groupable?: boolean;
  /** How many leading columns stay pinned at the left. */
  lead?: number;
  virtual?: boolean;
  /** The field whose value identifies a row; the index otherwise. */
  rowKey?: string;
  selectable?: Selectable;
  /** The unique-value count up to which an `auto` facet lists values. */
  facetLimit?: number;
  dense?: boolean;
  debug?: boolean;
}

export interface Selection {
  keys: string[];
  rows: TableRow[];
}

export interface TableHandle {
  snapshot(): LayoutV2;
  restore(layout: LayoutV2 | null | undefined): void;
  resetLayout(): void;
  scrollToEnd(): void;
  scrollToRow(index: number): void;
  openConfig(open?: boolean): void;
}

export interface TableProps {
  ref?: Ref<TableHandle>;
  columns?: ColumnSpec[];
  rows?: TableRow[];
  options?: TableOptions;
  onLayoutChange?: (layout: LayoutV2) => void;
  onRowClick?: (row: TableRow) => void;
  onSelectionChange?: (selection: Selection) => void;
  className?: string;
}

export interface TableDebug {
  rows: number;
  shown: number;
  ms: number;
  groupId: string | null;
  collapsed: string[];
}

/** The ids TanStack gives the group rows of these rows, every level, so a derived `expanded` map can list them all. */
function groupIdsOf(rows: TableRow[], grouping: string[]): string[] {
  if (grouping.length === 0) return [];

  const ids = new Set<string>();
  for (const row of rows) {
    let id = "";
    for (const field of grouping) {
      const part = `${field}:${String(row[field] ?? undefined)}`;
      id = id ? `${id}>${part}` : part;
      ids.add(id);
    }
  }

  return [...ids];
}

function filterLabel(title: string, value: unknown): string {
  if (isTextValue(value)) return `${title}: ${value.value}`;
  if (isSetValue(value)) return `${title}: ${value.in.length} values`;
  if (isRangeValue(value)) return `${title}: ${value.min ?? "…"} to ${value.max ?? "…"}`;

  return title;
}

function sameDocument(a: LayoutState, b: LayoutState): boolean {
  return JSON.stringify(documentOf(a)) === JSON.stringify(documentOf(b));
}

export function Table({
  ref,
  columns = [],
  rows = [],
  options = {},
  onLayoutChange,
  onRowClick,
  onSelectionChange,
  className,
}: TableProps) {
  const fields = useMemo(() => columns.map((column) => column.field), [columns]);
  const specs = useMemo(() => new Map(columns.map((column) => [column.field, column])), [columns]);
  const titles = useMemo(
    () => new Map(columns.map((column) => [column.field, column.title ?? column.field])),
    [columns],
  );
  const lead = options.lead ?? DEFAULT_LEAD;
  const pinned = useMemo(() => fields.slice(0, lead), [fields, lead]);
  const pinnedSet = useMemo(() => new Set(pinned), [pinned]);
  const filterable = Boolean(options.filterable);
  const groupable = options.groupable ?? true;
  const selectable = options.selectable ?? "none";
  const facetLimit = options.facetLimit ?? DEFAULT_FACET_LIMIT;
  const virtual = Boolean(options.virtual);
  const rowKey = options.rowKey;
  const defaults = useRef<LayoutDefaults | undefined>(options.defaults);
  defaults.current = options.defaults;

  // The rule the column definitions apply, so a persisted document cannot group what the table refuses to group.
  const groupableFields = useMemo(
    () => new Set(groupable ? fields.filter((field) => !pinnedSet.has(field)) : []),
    [fields, pinnedSet, groupable],
  );

  const [layout, setLayout] = useState<LayoutState>(() =>
    stateFrom(layoutFrom(options.layout) ?? defaultLayout(fields, options.defaults), fields, groupableFields),
  );
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [configOpen, setConfigOpen] = useState(false);
  const [viewport, setViewport] = useState<HTMLElement | null>(null);
  const [rowHeight, setRowHeight] = useState(DEFAULT_ROW_HEIGHT);

  useEffect(() => {
    setLayout((current) => {
      const document = documentOf({ ...current, order: mergeOrder(current.order, fields) });
      const merged = stateFrom(document, fields, groupableFields);

      return sameDocument(merged, current) ? current : merged;
    });
  }, [fields, groupableFields]);

  const columnDefs = useMemo(() => {
    const defs = columns.map((column) =>
      helper.accessor((row) => row[column.field] ?? undefined, {
        id: column.field,
        header: column.title ?? column.field,
        filterFn: column.numeric ? "facetNumeric" : "facet",
        sortFn: column.numeric ? "basic" : "alphanumeric",
        sortUndefined: "last",
        enableHiding: !pinnedSet.has(column.field),
        enableGrouping: groupable && !pinnedSet.has(column.field),
        size:
          column.width ??
          Math.max(MIN_WIDTH, CHAR_WIDTH * String(column.title ?? column.field).length + HEADER_PADDING),
      }),
    );

    return helper.columns(defs);
  }, [columns, pinnedSet, groupable]);

  const groupIds = useMemo(() => groupIdsOf(rows, layout.grouping), [rows, layout.grouping]);
  // TanStack turns `expanded: true` plus a toggle into a map of the rows present at that moment, so the map is derived from the data.
  const expanded = useMemo<ExpandedState>(
    () => (collapsed.size === 0 ? true : Object.fromEntries(groupIds.map((id) => [id, !collapsed.has(id)]))),
    [collapsed, groupIds],
  );

  function update<K extends keyof LayoutState>(key: K): (updater: Updater<LayoutState[K]>) => void {
    return (updater) => setLayout((current) => ({ ...current, [key]: functionalUpdate(updater, current[key]) }));
  }

  const table = useTable({
    features,
    columns: columnDefs,
    data: rows,
    getRowId: rowKey ? (row, index) => String(row[rowKey] ?? index) : undefined,
    state: {
      columnOrder: layout.order,
      columnVisibility: layout.visibility,
      sorting: layout.sorting,
      columnFilters: layout.filters,
      grouping: layout.grouping,
      expanded,
      rowSelection,
      columnPinning: { start: pinned, end: [] },
    },
    onColumnOrderChange: update("order"),
    onColumnVisibilityChange: update("visibility"),
    onSortingChange: update("sorting"),
    onColumnFiltersChange: update("filters"),
    onGroupingChange: update("grouping"),
    onRowSelectionChange: (updater) => setRowSelection((current) => functionalUpdate(updater, current)),
    onExpandedChange: () => undefined,
    groupedColumnMode: "remove",
    autoResetExpanded: false,
    autoResetAll: false,
    enableSortingRemoval: true,
    enableMultiSort: true,
    enableRowSelection: selectable !== "none",
    enableMultiRowSelection: selectable === "multiple",
  });

  const mounted = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return undefined;
    }

    clearTimeout(timer.current);
    timer.current = setTimeout(() => onLayoutChange?.(documentOf(layout)), LAYOUT_DEBOUNCE_MS);
    return () => clearTimeout(timer.current);
  }, [layout, onLayoutChange]);

  // `useTable` hands back a fresh object every render, so the effect reads it through a ref instead of depending on it.
  const latest = useRef(table);
  latest.current = table;

  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (selectable === "none") return;

    const keys = Object.entries(rowSelection)
      .filter(([, on]) => on)
      .map(([key]) => key)
      .sort();
    const signature = keys.join(",");
    if (reported.current === signature) return;

    const mounting = reported.current === null;
    reported.current = signature;
    if (mounting) return;

    const byId = latest.current.getCoreRowModel().rowsById;
    onSelectionChange?.({ keys, rows: keys.flatMap((key) => (byId[key] ? [byId[key].original] : [])) });
  }, [rowSelection, selectable, onSelectionChange]);

  const rootRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    setViewport(root.querySelector<HTMLElement>(VIEWPORT_SELECTOR));
    const height = Number.parseFloat(getComputedStyle(root).getPropertyValue(ROW_HEIGHT_VAR));
    if (Number.isFinite(height) && height > 0) setRowHeight(height);
  }, [options.dense]);

  const renderStart = useRef(0);
  renderStart.current = performance.now();
  useLayoutEffect(() => {
    if (!options.debug) return;

    const first = table.getRowModel().rows.find((row) => row.getIsGrouped());
    window.__ffTable = {
      rows: rows.length,
      shown: table.getRowModel().rows.length,
      ms: performance.now() - renderStart.current,
      groupId: first?.id ?? null,
      collapsed: [...collapsed],
    };
  });

  const scroller = useRef<Scroller | null>(null);
  useImperativeHandle(ref, () => ({
    snapshot: () => documentOf(layout),
    restore: (document) =>
      setLayout(stateFrom(layoutFrom(document) ?? defaultLayout(fields, defaults.current), fields, groupableFields)),
    resetLayout: () => setLayout(stateFrom(defaultLayout(fields, defaults.current), fields, groupableFields)),
    scrollToEnd: () => {
      if (viewport) viewport.scrollTop = viewport.scrollHeight;
    },
    scrollToRow: (index) => scroller.current?.toIndex(index),
    openConfig: (open = true) => setConfigOpen(open),
  }));

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const draggable = table
    .getVisibleLeafColumns()
    .map((column) => column.id)
    .filter((id) => !pinnedSet.has(id));

  function toggleGroup(id: string): void {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);

      return next;
    });
  }

  function onDragEnd(event: DragEndEvent): void {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    setLayout((current) => {
      const from = current.order.indexOf(String(active.id));
      const to = current.order.indexOf(String(over.id));
      return from < 0 || to < 0 ? current : { ...current, order: arrayMove(current.order, from, to) };
    });
  }

  const shownRows = table.getRowModel().rows;
  const dataCount = table.getFilteredRowModel().rows.length;
  const leafCount = table.getVisibleLeafColumns().length;
  const activeFilters = layout.filters.filter((entry) => specs.has(entry.id));

  return (
    <div
      ref={rootRef}
      className={cn(
        "ff-table flex h-full w-full flex-col bg-background text-foreground",
        options.dense && "[--ff-row-height:22px] text-label",
        className,
      )}
    >
      <div className="flex h-(--ff-tab-height) shrink-0 items-center gap-2 border-b border-border px-2 text-label text-muted-foreground">
        <span data-slot="rows" className="shrink-0 tabular-nums">
          {dataCount === rows.length ? `${rows.length} rows` : `${dataCount} of ${rows.length} rows`}
        </span>
        <div className="flex min-w-0 grow items-center gap-1 overflow-hidden">
          {activeFilters.map((entry) => (
            <Badge key={entry.id} variant="secondary" className="max-w-56 gap-1 pr-0.5">
              <span className="truncate">{filterLabel(titles.get(entry.id) ?? entry.id, entry.value)}</span>
              <button
                type="button"
                className="rounded-full hover:bg-wash-hover"
                aria-label={`Clear ${titles.get(entry.id) ?? entry.id} filter`}
                onClick={() => table.getColumn(entry.id)?.setFilterValue(undefined)}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
        <ColumnMenu table={table} fields={layout.order} grouping={layout.grouping} titles={titles} />
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Configure columns"
          aria-pressed={configOpen}
          className={cn(configOpen && "bg-wash-selected text-foreground")}
          onClick={() => setConfigOpen((open) => !open)}
        >
          <SlidersHorizontal />
        </Button>
      </div>
      <div className="flex min-h-0 grow flex-row">
        <ScrollArea className="min-w-0 grow">
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <table className="border-separate border-spacing-0 whitespace-nowrap">
              <thead className="sticky top-0 z-20">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    <SortableContext items={draggable} strategy={horizontalListSortingStrategy}>
                      {headerGroup.headers.map((header) => (
                        <HeaderCell
                          key={header.id}
                          header={header}
                          spec={specs.get(header.column.id) ?? { field: header.column.id }}
                          pinned={pinnedSet.has(header.column.id)}
                          filterable={filterable}
                          groupable={groupable}
                          facetLimit={facetLimit}
                        />
                      ))}
                    </SortableContext>
                  </tr>
                ))}
              </thead>
              <Body
                rows={shownRows}
                specs={specs}
                titles={titles}
                pinned={pinnedSet}
                leafCount={leafCount}
                selectable={selectable !== "none"}
                onRowClick={onRowClick}
                onToggleGroup={toggleGroup}
                virtual={virtual}
                viewport={viewport}
                rowHeight={rowHeight}
                scroller={scroller}
              />
            </table>
          </DndContext>
          <ScrollBar orientation="horizontal" />
        </ScrollArea>
        {configOpen ? (
          <ConfigPanel
            lists={listsOf(layout, pinned)}
            pinned={pinned}
            titles={titles}
            onChange={(lists) => setLayout((current) => applyLists(current, lists))}
            groupable={groupable}
            onReset={() => setLayout(stateFrom(defaultLayout(fields, defaults.current), fields, groupableFields))}
            onClose={() => setConfigOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
