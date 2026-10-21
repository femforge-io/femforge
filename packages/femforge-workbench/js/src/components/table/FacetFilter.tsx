import { useMemo, useState, type KeyboardEvent } from "react";
import type { Column } from "@tanstack/react-table";
import { Funnel } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import type { Features, TableRow } from "./features";
import { boundFrom, isRangeValue, isSetValue, isTextValue, type ColumnSpec, type Facet } from "./layout";

export type TableColumn = Column<Features, TableRow, unknown>;

const BLANK = "(blank)";
const SLIDER_STEPS = 200;
const LIST_HEIGHT = "max-h-64";

interface FacetFilterProps {
  column: TableColumn;
  spec: ColumnSpec;
  facetLimit: number;
}

/** The mode a column filters in: `auto` is a range for numbers, values while the facet is small, else text. */
export function facetMode(spec: ColumnSpec, column: TableColumn, facetLimit: number): Exclude<Facet, "auto"> {
  const facet = spec.facet ?? "auto";
  if (facet !== "auto") return facet;
  if (spec.numeric) return "range";

  return column.getFacetedUniqueValues().size <= facetLimit ? "values" : "none";
}

function label(value: unknown): string {
  return value === null || value === undefined ? BLANK : String(value);
}

function compare(a: unknown, b: unknown): number {
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;

  return String(a).localeCompare(String(b));
}

/** The facet entries as `[value, count]`, blanks folded to `null` and sorted last. */
function entriesOf(unique: Map<unknown, number>): Array<[unknown, number]> {
  const counts = new Map<unknown, number>();
  for (const [value, count] of unique) {
    const key = value ?? null;
    counts.set(key, (counts.get(key) ?? 0) + count);
  }

  return [...counts.entries()].sort(([a], [b]) => compare(a, b));
}

function niceStep(low: number, high: number): number {
  const span = high - low;
  if (span <= 0) return 1;

  return 10 ** Math.floor(Math.log10(span / SLIDER_STEPS));
}

function ValuesFacet({ column, title }: { column: TableColumn; title: string }) {
  const [search, setSearch] = useState("");
  const unique = column.getFacetedUniqueValues();
  const entries = useMemo(() => entriesOf(unique), [unique]);
  const filter = column.getFilterValue();
  const chosen: Set<unknown> | null = isSetValue(filter)
    ? new Set<unknown>(filter.in.map((value) => value ?? null))
    : null;
  const query = search.toLowerCase();
  const shown = entries.filter(([value]) => label(value).toLowerCase().includes(query));

  function toggle(value: unknown, checked: boolean): void {
    const next = new Set(chosen ?? entries.map(([entry]) => entry));
    if (checked) next.add(value);
    else next.delete(value);

    const everything = entries.every(([entry]) => next.has(entry));
    column.setFilterValue(everything ? undefined : { in: [...next] });
  }

  return (
    <div className="flex flex-col gap-1">
      <Input
        name={`${column.id}-search`}
        aria-label={`Search ${title} values`}
        placeholder="Search values"
        className="h-7 text-[length:inherit]"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className="flex gap-1">
        <Button variant="ghost" size="xs" onClick={() => column.setFilterValue(undefined)}>
          Select all
        </Button>
        <Button variant="ghost" size="xs" onClick={() => column.setFilterValue({ in: [] })}>
          Clear
        </Button>
      </div>
      <div className={cn("flex flex-col overflow-auto", LIST_HEIGHT)}>
        {shown.map(([value, count]) => (
          <label
            key={label(value)}
            className="flex h-6 shrink-0 cursor-pointer items-center gap-2 rounded-sm px-1 hover:bg-wash-hover"
          >
            <Checkbox
              className="size-3.5"
              checked={chosen === null || chosen.has(value)}
              onCheckedChange={(checked) => toggle(value, checked === true)}
              aria-label={label(value)}
            />
            <span className="min-w-0 grow truncate">{label(value)}</span>
            <span className="text-muted-foreground tabular-nums">{count}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function RangeFacet({ column, title }: { column: TableColumn; title: string }) {
  const [low, high] = column.getFacetedMinMaxValues() ?? [0, 0];
  const filter = column.getFilterValue();
  const current = isRangeValue(filter) ? filter : {};
  const [draft, setDraft] = useState<[number, number]>([current.min ?? low, current.max ?? high]);
  const flat = low === high;

  function commit(min: number, max: number): void {
    column.setFilterValue({ ...(min > low ? { min } : {}), ...(max < high ? { max } : {}) });
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter") commit(draft[0], draft[1]);
  }

  function bound(index: 0 | 1, text: string): void {
    const value = boundFrom(text, index === 0 ? low : high);
    if (value === null) return;

    setDraft(index === 0 ? [value, draft[1]] : [draft[0], value]);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1">
        <Input
          type="number"
          name={`${column.id}-min`}
          aria-label={`${title} minimum`}
          className="h-7 text-[length:inherit]"
          value={draft[0]}
          step={niceStep(low, high)}
          onChange={(event) => bound(0, event.target.value)}
          onBlur={() => commit(draft[0], draft[1])}
          onKeyDown={onKeyDown}
        />
        <span className="text-muted-foreground">to</span>
        <Input
          type="number"
          name={`${column.id}-max`}
          aria-label={`${title} maximum`}
          className="h-7 text-[length:inherit]"
          value={draft[1]}
          step={niceStep(low, high)}
          onChange={(event) => bound(1, event.target.value)}
          onBlur={() => commit(draft[0], draft[1])}
          onKeyDown={onKeyDown}
        />
      </div>
      <Slider
        aria-label={`${title} range`}
        min={low}
        max={high}
        step={niceStep(low, high)}
        disabled={flat}
        value={draft}
        onValueChange={(values) => setDraft([values[0] ?? low, values[1] ?? high])}
        onValueCommit={(values) => commit(values[0] ?? low, values[1] ?? high)}
      />
      <div className="flex justify-between text-muted-foreground tabular-nums">
        <span>{low}</span>
        <span>{high}</span>
      </div>
    </div>
  );
}

function TextFacet({ column, title, numeric }: { column: TableColumn; title: string; numeric: boolean }) {
  const filter = column.getFilterValue();

  return (
    <Input
      name={column.id}
      aria-label={`Filter ${title}`}
      placeholder={numeric ? ">= min" : "contains"}
      className="h-7 text-[length:inherit]"
      value={isTextValue(filter) ? filter.value : ""}
      onChange={(event) => column.setFilterValue({ value: event.target.value })}
    />
  );
}

function FacetContent({ column, spec, facetLimit }: FacetFilterProps) {
  const title = spec.title ?? spec.field;
  const mode = facetMode(spec, column, facetLimit);
  if (mode === "values") return <ValuesFacet column={column} title={title} />;
  if (mode === "range") return <RangeFacet column={column} title={title} />;

  return <TextFacet column={column} title={title} numeric={Boolean(spec.numeric)} />;
}

/** The header's filter popover; inputs and checkboxes inside keep it open, and pointer events never reach the header's drag sensor. */
export function FacetFilter({ column, spec, facetLimit }: FacetFilterProps) {
  const [open, setOpen] = useState(false);
  const active = column.getIsFiltered();
  const title = spec.title ?? spec.field;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`Filter ${title}`}
          className={cn("text-muted-foreground", active && "text-foreground")}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <Funnel />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-2 text-label" onPointerDown={(event) => event.stopPropagation()}>
        {open ? <FacetContent column={column} spec={spec} facetLimit={facetLimit} /> : null}
      </PopoverContent>
    </Popover>
  );
}
