import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

const INDENT_PX = 12;
const LEAF_INSET_PX = 18;

export interface TreeItem {
  id: string;
  title: string;
  children?: TreeItem[];
}

export interface TreeProps {
  items: TreeItem[];
  value?: string[];
  onValueChange?: (ids: string[]) => void;
  selectable?: "leaf" | "none";
  openAll?: boolean;
  className?: string;
}

interface TreeNodeProps {
  item: TreeItem;
  depth: number;
  selected: string[];
  selectable: "leaf" | "none";
  openAll: boolean;
  onToggle: (id: string) => void;
  onToggleAll: (ids: string[], select: boolean) => void;
}

/** Every leaf id under an item, the item itself when it has no children. */
function leafIds(item: TreeItem): string[] {
  if (!item.children || item.children.length === 0) return [item.id];

  return item.children.flatMap(leafIds);
}

function TreeNode({ item, depth, selected, selectable, openAll, onToggle, onToggleAll }: TreeNodeProps) {
  const rowClass =
    "flex h-[var(--ff-row-height)] w-full items-center gap-1.5 rounded-sm px-1 text-label hover:bg-accent";

  if (item.children && item.children.length > 0) {
    const leaves = leafIds(item);
    const chosen = leaves.filter((id) => selected.includes(id)).length;
    const group = chosen === leaves.length ? true : chosen === 0 ? false : "indeterminate";

    return (
      <Collapsible defaultOpen={openAll}>
        <div className={cn(rowClass, "group")} style={{ paddingLeft: depth * INDENT_PX }} data-item={item.id}>
          {selectable === "leaf" ? (
            <Checkbox
              checked={group}
              onCheckedChange={() => onToggleAll(leaves, chosen < leaves.length)}
              aria-label={item.title}
            />
          ) : null}
          <CollapsibleTrigger className="flex min-w-0 flex-1 items-center gap-1.5">
            <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90" />
            <span className="truncate">{item.title}</span>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent>
          {item.children.map((child) => (
            <TreeNode
              key={child.id}
              item={child}
              depth={depth + 1}
              selected={selected}
              selectable={selectable}
              openAll={openAll}
              onToggle={onToggle}
              onToggleAll={onToggleAll}
            />
          ))}
        </CollapsibleContent>
      </Collapsible>
    );
  }

  return (
    <label className={rowClass} style={{ paddingLeft: depth * INDENT_PX + LEAF_INSET_PX }} data-item={item.id}>
      {selectable === "leaf" ? (
        <Checkbox checked={selected.includes(item.id)} onCheckedChange={() => onToggle(item.id)} />
      ) : null}
      <span className="truncate">{item.title}</span>
    </label>
  );
}

/** A recursive tree of collapsible branches and (optionally) checkable leaves; `value` holds the selected leaf ids. */
export function Tree({ items, value, onValueChange, selectable = "leaf", openAll = false, className }: TreeProps) {
  const [internal, setInternal] = useState<string[]>([]);
  const selected = value ?? internal;

  function report(next: string[]): void {
    setInternal(next);
    onValueChange?.(next);
  }

  function toggle(id: string): void {
    report(selected.includes(id) ? selected.filter((other) => other !== id) : [...selected, id]);
  }

  // A branch is not a value of its own: its checkbox selects or clears the leaves under it, so `value` still holds leaf ids only.
  function toggleAll(ids: string[], select: boolean): void {
    const rest = selected.filter((id) => !ids.includes(id));
    report(select ? [...rest, ...ids] : rest);
  }

  return (
    <div className={cn("ff-tree flex flex-col", className)}>
      {items.map((item) => (
        <TreeNode
          key={item.id}
          item={item}
          depth={0}
          selected={selected}
          selectable={selectable}
          openAll={openAll}
          onToggle={toggle}
          onToggleAll={toggleAll}
        />
      ))}
    </div>
  );
}
