import { useRef, useState } from "react";
import type { Table } from "@tanstack/react-table";
import { Columns3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { TableColumn } from "./FacetFilter";
import type { Features, TableRow } from "./features";

interface ColumnMenuProps {
  table: Table<Features, TableRow>;
  fields: string[];
  grouping: string[];
  titles: Map<string, string>;
}

/** The column chooser: stays open while toggling, searches, toggles all, and shift-click toggles a range. */
export function ColumnMenu({ table, fields, grouping, titles }: ColumnMenuProps) {
  const [search, setSearch] = useState("");
  const last = useRef<number | null>(null);
  const shift = useRef(false);
  const query = search.toLowerCase();

  function listed(column: TableColumn | undefined): column is TableColumn {
    return column !== undefined && column.getCanHide() && !grouping.includes(column.id);
  }

  function title(column: TableColumn): string {
    return titles.get(column.id) ?? column.id;
  }

  const columns = fields.map((field) => table.getColumn(field)).filter(listed);
  const shown = columns.filter((column) => title(column).toLowerCase().includes(query));
  const allVisible = shown.every((column) => column.getIsVisible());

  function setVisible(targets: TableColumn[], visible: boolean): void {
    const entries = targets.map((column) => [column.id, visible] as const);
    table.setColumnVisibility((current) => ({ ...current, ...Object.fromEntries(entries) }));
  }

  function toggle(index: number, visible: boolean): void {
    const anchor = shift.current && last.current !== null ? last.current : index;
    setVisible(shown.slice(Math.min(anchor, index), Math.max(anchor, index) + 1), visible);
    last.current = index;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="xs" aria-label="Choose columns">
          <Columns3 /> Columns
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <div className="p-1">
          <Input
            name="ff-column-search"
            aria-label="Search columns"
            placeholder="Search columns"
            className="h-7 text-[length:inherit]"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => event.stopPropagation()}
          />
        </div>
        <DropdownMenuCheckboxItem
          checked={allVisible}
          onSelect={(event) => event.preventDefault()}
          onCheckedChange={(checked) => setVisible(shown, checked === true)}
        >
          Toggle all
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <div className="max-h-[50vh] overflow-auto">
          {shown.map((column, index) => (
            <DropdownMenuCheckboxItem
              key={column.id}
              checked={column.getIsVisible()}
              onSelect={(event) => event.preventDefault()}
              onClick={(event) => {
                shift.current = event.shiftKey;
              }}
              onCheckedChange={(checked) => toggle(index, checked === true)}
            >
              {title(column)}
            </DropdownMenuCheckboxItem>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
