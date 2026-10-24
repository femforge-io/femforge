import { useState, type KeyboardEvent } from "react";
import { ChevronDownIcon } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Truncate } from "@/components/Truncate";
import { token } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { ChoiceList } from "./combobox/ChoiceList";
import { useLiveChoice } from "./combobox/live";
import { stepped, type ComboboxItem } from "./combobox/sections";

export type { ComboboxItem } from "./combobox/sections";

const STEPS: Record<string, number> = { ArrowDown: 1, ArrowUp: -1 };

export interface ComboboxProps {
  items?: ComboboxItem[];
  value?: string;
  onValueChange?: (value: string) => void;
  filters?: string[];
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}

/** A searchable single choice whose list narrows by group toggles; the arrow keys apply as they step, in the list and on the closed trigger alike. */
export function Combobox({
  items = [],
  value,
  onValueChange,
  filters = [],
  placeholder = "",
  searchPlaceholder = "Search",
  emptyText,
  disabled,
  className,
  "aria-label": ariaLabel,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [shown, apply, later] = useLiveChoice(value, onValueChange);
  const chosen = items.find((item) => item.value === shown);

  function pick(next: string): void {
    setOpen(false);
    // The scene redraws once the list has closed.
    if (next !== shown) later(next, token("--duration-panel"));
  }

  function step(event: KeyboardEvent<HTMLButtonElement>): void {
    const delta = STEPS[event.key];
    if (delta === undefined || open) return;

    event.preventDefault();
    const next = stepped(items, shown, delta);
    if (next !== undefined && next !== shown) apply(next);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild disabled={disabled}>
        <button
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label={chosen && ariaLabel ? `${ariaLabel}: ${chosen.label}` : ariaLabel}
          onKeyDown={step}
          className={cn(
            "flex h-8 w-full min-w-0 items-center gap-2 rounded-md border border-input bg-transparent px-3 text-sm whitespace-nowrap shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50",
            className,
          )}
        >
          {chosen ? (
            <>
              <Truncate text={chosen.label} className={cn("text-left", chosen.swatch ? "w-42 shrink-0" : "grow")} />
              {chosen.swatch ? (
                <span className="h-3.5 min-w-8 grow rounded-sm" style={{ background: chosen.swatch }} aria-hidden />
              ) : null}
            </>
          ) : (
            <span className="grow truncate text-left text-muted-foreground">{placeholder}</span>
          )}
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" collisionPadding={8} className="w-(--radix-popover-trigger-width) min-w-72 p-2.5">
        <ChoiceList
          items={items}
          filters={filters}
          value={shown}
          onStep={apply}
          onPick={pick}
          searchPlaceholder={searchPlaceholder}
          emptyText={emptyText}
          reserve="7rem"
        />
      </PopoverContent>
    </Popover>
  );
}
