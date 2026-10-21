import type { CSSProperties } from "react";
import { flexRender, type Header } from "@tanstack/react-table";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowDown, ArrowUp, EllipsisVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { FacetFilter, type TableColumn } from "./FacetFilter";
import type { Features, TableRow } from "./features";
import type { ColumnSpec } from "./layout";
import { Tip } from "@/components/Tip";

interface HeaderCellProps {
  header: Header<Features, TableRow, unknown>;
  spec: ColumnSpec;
  pinned: boolean;
  filterable: boolean;
  groupable: boolean;
  facetLimit: number;
}

interface HeaderMenuProps {
  column: TableColumn;
  title: string;
  groupable: boolean;
}

function HeaderMenu({ column, title, groupable }: HeaderMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`${title} menu`}
          className="text-muted-foreground"
          onPointerDown={(event) => event.stopPropagation()}
        >
          <EllipsisVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" onPointerDown={(event) => event.stopPropagation()}>
        <DropdownMenuItem onSelect={() => column.toggleSorting(false)}>Sort ascending</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => column.toggleSorting(true)}>Sort descending</DropdownMenuItem>
        <DropdownMenuItem disabled={!column.getIsSorted()} onSelect={() => column.clearSorting()}>
          Clear sort
        </DropdownMenuItem>
        {groupable && column.getCanGroup() ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => column.toggleGrouping()}>
              {column.getIsGrouped() ? "Ungroup" : "Group by"}
            </DropdownMenuItem>
          </>
        ) : null}
        {column.getCanHide() ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => column.toggleVisibility(false)}>Hide column</DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A sortable, draggable header: the title sorts, the funnel filters, the menu groups and hides; pinned headers stick left. */
export function HeaderCell({ header, spec, pinned, filterable, groupable, facetLimit }: HeaderCellProps) {
  const column = header.column;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: column.id,
    disabled: pinned,
  });
  const sorted = column.getIsSorted();
  const numeric = Boolean(spec.numeric);
  const title = spec.title ?? spec.field;
  const style: CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition,
    width: column.getSize(),
    minWidth: column.getSize(),
    maxWidth: column.getSize(),
    left: pinned ? column.getStart("start") : undefined,
  };

  return (
    <th
      ref={setNodeRef}
      style={style}
      className={cn(
        "border-b border-border bg-card px-1 py-0.5 text-left font-medium text-muted-foreground select-none",
        pinned && "sticky z-30",
        isDragging && "z-40 opacity-60",
      )}
    >
      <div className="flex items-center gap-0.5">
        <div
          className={cn("flex min-w-0 grow items-center gap-1 px-1", numeric && "justify-end")}
          {...attributes}
          {...listeners}
        >
          <Tip content={column.id}>
            <span className="min-w-0 cursor-pointer truncate" onClick={column.getToggleSortingHandler()}>
              {flexRender(column.columnDef.header, header.getContext())}
            </span>
          </Tip>
          <span className="w-3 shrink-0 text-foreground">
            {sorted === "asc" ? (
              <ArrowUp className="size-3" />
            ) : sorted === "desc" ? (
              <ArrowDown className="size-3" />
            ) : null}
          </span>
        </div>
        {filterable ? <FacetFilter column={column} spec={spec} facetLimit={facetLimit} /> : null}
        <HeaderMenu column={column} title={title} groupable={groupable} />
      </div>
    </th>
  );
}
