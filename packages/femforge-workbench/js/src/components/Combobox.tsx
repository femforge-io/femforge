import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tip } from "@/components/Tip";
import { Truncate } from "@/components/Truncate";
import { cn } from "@/lib/utils";
import { sectionsOf, stepped, type ComboboxItem } from "./combobox/sections";

export type { ComboboxItem } from "./combobox/sections";

const STEPS: Record<string, number> = { ArrowDown: 1, ArrowUp: -1 };

// One label width for every row, so every swatch starts and ends at the same x whatever its label.
const LABEL_COLUMN = "w-42 shrink-0 truncate";

export interface ComboboxProps {
  items?: ComboboxItem[];
  value?: string;
  onValueChange?: (value: string) => void;
  lead?: ComboboxItem;
  filters?: string[];
  compact?: boolean;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}

/** A label in the shared column with its swatch filling the rest of the row. */
function Entry({ item }: { item: ComboboxItem }) {
  return (
    <>
      <Truncate text={item.label} className={cn("text-left", item.swatch ? LABEL_COLUMN : "grow")} />
      {item.swatch ? (
        <span className="h-3.5 min-w-8 grow rounded-sm" style={{ background: item.swatch }} aria-hidden />
      ) : null}
    </>
  );
}

/** A searchable single choice whose list narrows by group toggles; the arrow keys step through it while it is closed, and `compact` shows only the chosen swatch. */
export function Combobox({
  items = [],
  value,
  onValueChange,
  lead,
  filters = [],
  compact = false,
  placeholder = "",
  searchPlaceholder = "Search",
  emptyText = "No match",
  disabled,
  className,
  "aria-label": ariaLabel,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const listed = lead ? [lead, ...items] : items;
  const chosen = listed.find((item) => item.value === value);

  // The list opens on the chosen item, not on its top.
  useEffect(() => {
    if (!open || value === undefined) return;

    const frame = requestAnimationFrame(() => {
      list.current
        ?.querySelector(`[cmdk-item][data-value="${CSS.escape(value)}"]`)
        ?.scrollIntoView({ block: "center" });
    });

    return () => cancelAnimationFrame(frame);
  }, [open, value]);

  // A narrower list starts from its top, whatever the chosen item scrolled it to.
  function rewind(): void {
    list.current?.scrollTo({ top: 0 });
  }

  function filter(next: string[]): void {
    setActive(next);
    rewind();
  }

  function choose(next: string): void {
    setOpen(false);
    if (next !== value) onValueChange?.(next);
  }

  function step(event: KeyboardEvent<HTMLButtonElement>): void {
    const delta = STEPS[event.key];
    if (delta === undefined || open) return;

    event.preventDefault();
    const next = stepped(listed, value, delta);
    if (next !== undefined && next !== value) onValueChange?.(next);
  }

  function row(item: ComboboxItem) {
    return (
      <CommandItem
        key={item.value}
        value={item.value}
        keywords={[item.label, ...(item.groups ?? [])]}
        onSelect={() => choose(item.value)}
      >
        <Entry item={item} />
        <CheckIcon className={cn("ml-auto", item.value === value ? "opacity-100" : "opacity-0")} />
      </CommandItem>
    );
  }

  function face() {
    if (!chosen) return <span className="grow truncate text-left text-muted-foreground">{placeholder}</span>;

    if (compact) {
      return chosen.swatch ? (
        <span className="h-3.5 min-w-0 grow rounded-sm" style={{ background: chosen.swatch }} aria-hidden />
      ) : (
        <span className="grow truncate text-left">{chosen.label}</span>
      );
    }

    return <Entry item={chosen} />;
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tip content={compact ? chosen?.label : undefined}>
        <PopoverTrigger asChild disabled={disabled}>
          <button
            type="button"
            role="combobox"
            aria-expanded={open}
            aria-label={chosen && ariaLabel ? `${ariaLabel}: ${chosen.label}` : ariaLabel}
            onKeyDown={step}
            className={cn(
              "flex w-full min-w-0 items-center rounded-md border border-input bg-transparent whitespace-nowrap shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50",
              compact ? "h-6 gap-1 px-1.5" : "h-8 gap-2 px-3 text-sm",
              className,
            )}
          >
            {face()}
            <ChevronDownIcon className={cn("shrink-0 opacity-50", compact ? "size-3.5" : "size-4")} />
          </button>
        </PopoverTrigger>
      </Tip>
      <PopoverContent
        align={compact ? "end" : "start"}
        collisionPadding={8}
        className={cn(
          "flex max-h-(--radix-popover-content-available-height) flex-col overflow-hidden p-0",
          compact ? "w-96" : "w-(--radix-popover-trigger-width) min-w-72",
        )}
      >
        <Command defaultValue={value} className="min-h-0 flex-1">
          <CommandInput placeholder={searchPlaceholder} onValueChange={rewind} />
          {filters.length > 0 ? (
            <ToggleGroup
              type="multiple"
              variant="outline"
              size="sm"
              spacing={1}
              value={active}
              onValueChange={filter}
              className="grid w-full shrink-0 grid-cols-3 border-b p-1.5"
              aria-label="Filter by group"
            >
              {filters.map((group) => (
                <ToggleGroupItem key={group} value={group} className="h-6 w-full px-1 text-label">
                  {group}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          ) : null}
          <CommandList ref={list} className="max-h-none min-h-0 flex-1">
            <CommandEmpty>{emptyText}</CommandEmpty>
            {lead ? <CommandGroup>{row(lead)}</CommandGroup> : null}
            {sectionsOf(items, active).map((section) => (
              <CommandGroup key={section.heading} heading={section.heading}>
                {section.items.map(row)}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
