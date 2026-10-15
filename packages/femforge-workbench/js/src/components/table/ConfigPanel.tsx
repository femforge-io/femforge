import { useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Pin, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { Lists } from "./layout";

type ListId = keyof Lists;

const LISTS: ListId[] = ["group", "visible", "hidden"];
const LIST_TITLES: Record<ListId, string> = { group: "Group by", visible: "Visible", hidden: "Hidden" };

interface ConfigPanelProps {
  lists: Lists;
  pinned: string[];
  titles: Map<string, string>;
  groupable: boolean;
  onChange(lists: Lists): void;
  onReset(): void;
  onClose(): void;
}

interface FieldItemProps {
  id: string;
  title: string;
  locked: boolean;
  selected: boolean;
  disabled: boolean;
  onSelect(selected: boolean): void;
}

interface FieldListProps {
  id: ListId;
  items: string[];
  query: string;
  pinned: string[];
  titles: Map<string, string>;
  selected: Set<string>;
  movable: number;
  disabled: boolean;
  onSelect(field: string, selected: boolean): void;
  onMoveHere(): void;
}

function isListId(id: string): id is ListId {
  return (LISTS as string[]).includes(id);
}

/** What the pointer is over, falling back to the nearest list while it is over none: rect collision reads the wrong list on an upward drag. */
const underPointer: CollisionDetection = (args) => {
  const hit = pointerWithin(args);

  return hit.length > 0 ? hit : closestCorners(args);
};

function without(lists: Lists, fields: Set<string>): Lists {
  return {
    group: lists.group.filter((field) => !fields.has(field)),
    visible: lists.visible.filter((field) => !fields.has(field)),
    hidden: lists.hidden.filter((field) => !fields.has(field)),
  };
}

function FieldItem({ id, title, locked, selected, disabled, onSelect }: FieldItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
    disabled: disabled || locked,
  });

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(
        "flex h-6 items-center gap-1.5 rounded-sm px-1 outline-none hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring/50",
        !locked && !disabled && "cursor-grab",
        isDragging && "opacity-40",
      )}
      {...attributes}
      {...listeners}
    >
      {locked ? (
        <Pin className="size-3 shrink-0 text-muted-foreground" />
      ) : (
        <Checkbox
          className="size-3.5"
          checked={selected}
          aria-label={`Select ${title}`}
          onCheckedChange={(checked) => onSelect(checked === true)}
          onPointerDown={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        />
      )}
      <span className="min-w-0 grow truncate">{title}</span>
      {locked ? null : <GripVertical className="size-3 shrink-0 text-muted-foreground" />}
    </li>
  );
}

function FieldList({
  id,
  items,
  query,
  pinned,
  titles,
  selected,
  movable,
  disabled,
  onSelect,
  onMoveHere,
}: FieldListProps) {
  const { setNodeRef, isOver } = useDroppable({ id });
  const shown = items.filter((field) => (titles.get(field) ?? field).toLowerCase().includes(query));

  return (
    <section
      ref={setNodeRef}
      className={cn("flex flex-col gap-0.5 p-1", isOver && "bg-accent/30")}
      aria-label={LIST_TITLES[id]}
    >
      <header className="flex h-6 items-center gap-1 px-1 text-muted-foreground">
        <span className="font-medium">{LIST_TITLES[id]}</span>
        <span className="tabular-nums">{items.length}</span>
        <span className="grow" />
        <Button variant="ghost" size="xs" disabled={movable === 0} onClick={onMoveHere}>
          {movable > 0 ? `Move ${movable} here` : "Move here"}
        </Button>
      </header>
      <SortableContext items={items} strategy={verticalListSortingStrategy}>
        <ul className="flex min-h-6 flex-col gap-0.5">
          {shown.map((field) => (
            <FieldItem
              key={field}
              id={field}
              title={titles.get(field) ?? field}
              locked={pinned.includes(field)}
              selected={selected.has(field)}
              disabled={disabled}
              onSelect={(value) => onSelect(field, value)}
            />
          ))}
        </ul>
      </SortableContext>
    </section>
  );
}

/** Three ordered lists (group by, visible, hidden): drag within and between them, multi-select and move, search, show/hide all, reset. */
export function ConfigPanel({ lists, pinned, titles, groupable, onChange, onReset, onClose }: ConfigPanelProps) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [active, setActive] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const query = search.toLowerCase();
  const searching = query !== "";
  const editable: ListId[] = groupable ? LISTS : LISTS.filter((list) => list !== "group");

  function containerOf(id: UniqueIdentifier): ListId | null {
    const key = String(id);
    if (isListId(key)) return key;

    return LISTS.find((list) => lists[list].includes(key)) ?? null;
  }

  function floor(list: ListId): number {
    return list === "visible" ? pinned.length : 0;
  }

  function moveTo(field: string, to: ListId, index: number): void {
    const next = without(lists, new Set([field]));
    next[to].splice(Math.max(index, floor(to)), 0, field);
    onChange(next);
  }

  function onDragStart({ active: dragged }: DragStartEvent): void {
    setActive(String(dragged.id));
  }

  function onDragOver({ active: dragged, over }: DragOverEvent): void {
    if (!over) return;

    const from = containerOf(dragged.id);
    const to = containerOf(over.id);
    if (!from || !to || from === to) return;

    const index = isListId(String(over.id)) ? lists[to].length : lists[to].indexOf(String(over.id));
    moveTo(String(dragged.id), to, index);
  }

  function onDragEnd({ active: dragged, over }: DragEndEvent): void {
    setActive(null);
    if (!over) return;

    const from = containerOf(dragged.id);
    const to = containerOf(over.id);
    if (!from || !to) return;

    if (from !== to) {
      onDragOver({ active: dragged, over } as DragOverEvent);
      return;
    }

    const list = lists[to];
    const fromIndex = list.indexOf(String(dragged.id));
    const toIndex = list.indexOf(String(over.id));
    if (fromIndex < 0 || toIndex < floor(to) || fromIndex === toIndex) return;

    onChange({ ...lists, [to]: arrayMove(list, fromIndex, toIndex) });
  }

  function select(field: string, value: boolean): void {
    setSelected((current) => {
      const next = new Set(current);
      if (value) next.add(field);
      else next.delete(field);

      return next;
    });
  }

  function movable(to: ListId): number {
    return [...selected].filter((field) => !lists[to].includes(field)).length;
  }

  function moveSelected(to: ListId): void {
    const chosen = LISTS.flatMap((list) => lists[list].filter((field) => selected.has(field)));
    const next = without(lists, selected);
    next[to] = [...next[to], ...chosen];
    onChange(next);
    setSelected(new Set());
  }

  function showAll(): void {
    onChange({ ...lists, visible: [...lists.visible, ...lists.hidden], hidden: [] });
  }

  function hideAll(): void {
    const free = lists.visible.filter((field) => !pinned.includes(field));
    onChange({
      ...lists,
      visible: lists.visible.filter((field) => pinned.includes(field)),
      hidden: [...lists.hidden, ...free],
    });
  }

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-border text-label" aria-label="Table configuration">
      <div className="flex items-center gap-1 border-b border-border p-1">
        <Input
          name="ff-field-search"
          aria-label="Search fields"
          placeholder="Search fields"
          className="h-7 text-[length:inherit]"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Button variant="ghost" size="icon-xs" aria-label="Close configuration" onClick={onClose}>
          <X />
        </Button>
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={underPointer}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActive(null)}
      >
        <ScrollArea className="min-h-0 grow">
          {editable.map((list) => (
            <FieldList
              key={list}
              id={list}
              items={lists[list]}
              query={query}
              pinned={pinned}
              titles={titles}
              selected={selected}
              movable={movable(list)}
              disabled={searching}
              onSelect={select}
              onMoveHere={() => moveSelected(list)}
            />
          ))}
        </ScrollArea>
        <DragOverlay>
          {active ? (
            <div className="flex h-6 items-center gap-1.5 rounded-sm border border-border bg-popover px-1 text-label shadow-md">
              <span className="min-w-0 grow truncate">{titles.get(active) ?? active}</span>
              <GripVertical className="size-3 shrink-0 text-muted-foreground" />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
      <div className="flex items-center gap-1 border-t border-border p-1">
        <Button variant="ghost" size="xs" onClick={showAll}>
          Show all
        </Button>
        <Button variant="ghost" size="xs" onClick={hideAll}>
          Hide all
        </Button>
        <span className="grow" />
        <Button variant="outline" size="xs" onClick={onReset}>
          Reset
        </Button>
      </div>
    </aside>
  );
}
