import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Truncate } from "@/components/Truncate";
import { Glide } from "@/lib/glide";
import { useSettled } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { sectionsOf, type ComboboxItem } from "./sections";

const LIST_MIN = 160;
const LIST_MAX = 650;
const STEP_KEYS = new Set(["ArrowDown", "ArrowUp", "Home", "End"]);

export interface ChoiceListProps {
  items: ComboboxItem[];
  filters?: string[];
  value?: string;
  /** Receives each arrow-key step, which applies as it goes. */
  onStep?: (value: string) => void;
  /** Receives a click or Enter. */
  onPick?: (value: string) => void;
  searchPlaceholder?: string;
  emptyText?: string;
  /** The popover's height taken by everything but the list, so the list never pushes the popover off screen. */
  reserve: string;
}

/** The searchable list of a picker popover: group toggles narrow it, sticky headings divide it, the arrow keys apply as they step, typing only moves the highlight, and its height follows the matches. */
export function ChoiceList({
  items,
  filters = [],
  value,
  onStep,
  onPick,
  searchPlaceholder = "Search",
  emptyText = "Nothing matches. Clear the search or the filters.",
  reserve,
}: ChoiceListProps) {
  const [active, setActive] = useState<string[]>([]);
  const [cursor, setCursor] = useState(value ?? "");
  const [height, setHeight] = useState(LIST_MIN);
  const sizer = useRef<HTMLDivElement>(null);
  const stepping = useRef(false);
  const settled = useSettled();
  const sections = sectionsOf(items, active);

  useLayoutEffect(() => {
    const element = sizer.current;
    if (!element) return undefined;

    const fit = (): void => setHeight(Math.max(LIST_MIN, Math.min(LIST_MAX, element.scrollHeight)));
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(element);

    return () => watch.disconnect();
  }, []);

  // The list opens on the chosen item.
  useEffect(() => {
    if (!value) return undefined;

    const frame = requestAnimationFrame(() => {
      // Scrolls the list alone: scrollIntoView would also scroll whatever clips the popover's content.
      const item = sizer.current?.querySelector(`[cmdk-item][data-value="${CSS.escape(value)}"]`);
      const viewport = item?.closest("[data-slot=scroll-area-viewport]");
      if (!item || !viewport) return;

      const at = item.getBoundingClientRect();
      const box = viewport.getBoundingClientRect();
      viewport.scrollTop += at.top - box.top - (box.height - at.height) / 2;
    });

    return () => cancelAnimationFrame(frame);
    // Only on opening: later changes come from this list and are already in view.
  }, []);

  function highlight(next: string): void {
    setCursor(next);
    if (!stepping.current || !next || next === value) return;

    stepping.current = false;
    onStep?.(next);
  }

  function key(event: KeyboardEvent<HTMLDivElement>): void {
    stepping.current = STEP_KEYS.has(event.key);
  }

  return (
    <Command
      value={cursor}
      onValueChange={highlight}
      onKeyDown={key}
      disablePointerSelection
      loop
      className="bg-transparent"
    >
      <div className="rounded-md border">
        <CommandInput placeholder={searchPlaceholder} className="h-8 text-label" />
      </div>
      {filters.length > 0 ? (
        <ToggleGroup
          type="multiple"
          variant="outline"
          size="sm"
          spacing={1}
          value={active}
          onValueChange={setActive}
          className="mt-2.5 grid w-full grid-cols-3"
          aria-label="Filter by group"
        >
          {filters.map((group) => (
            <ToggleGroupItem key={group} value={group} className="h-6 w-full px-1 text-label">
              {group}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      ) : null}
      <ScrollArea
        className={cn(settled && "transition-[height] duration-(--duration-panel) ease-instrument")}
        style={{
          height: `max(6rem, min(${height}px, calc(var(--radix-popover-content-available-height, 100vh) - ${reserve})))`,
        }}
      >
        <CommandList ref={sizer} className="max-h-none overflow-visible pr-2.5 [&_[cmdk-list-sizer]]:relative">
          {/* An item listed under Favorites and under its own group carries the marker in its first place. */}
          <Glide
            chosen={value}
            selector="[cmdk-item][aria-current=true]"
            speed="micro"
            className="rounded-r-sm border-l-2 border-primary bg-wash-selected"
          />
          <CommandEmpty className="px-1.5 py-2 text-label text-muted-foreground">{emptyText}</CommandEmpty>
          {sections.map((section) => (
            <CommandGroup
              key={section.heading}
              heading={section.heading}
              className="p-0 [&_[cmdk-group-heading]]:sticky [&_[cmdk-group-heading]]:top-0 [&_[cmdk-group-heading]]:z-10 [&_[cmdk-group-heading]]:flex [&_[cmdk-group-heading]]:items-center [&_[cmdk-group-heading]]:gap-2 [&_[cmdk-group-heading]]:bg-popover [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:text-[0.65rem] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:after:h-px [&_[cmdk-group-heading]]:after:flex-1 [&_[cmdk-group-heading]]:after:bg-border [&_[cmdk-group-heading]]:after:content-[''] [&_[cmdk-item]]:scroll-mt-7"
            >
              {section.items.map((item) => (
                <CommandItem
                  key={`${section.heading}:${item.value}`}
                  value={item.value}
                  keywords={[item.label, ...(item.groups ?? [])]}
                  onSelect={() => onPick?.(item.value)}
                  aria-current={item.value === value ? "true" : undefined}
                  className={cn(
                    "gap-2.5 py-1 hover:bg-wash-hover aria-[current=true]:border-transparent aria-[current=true]:bg-transparent aria-[current=true]:hover:bg-transparent aria-[current=true]:data-[selected=true]:bg-transparent",
                    item.swatch && "grid grid-cols-[44%_minmax(0,1fr)]",
                  )}
                >
                  {item.swatch ? (
                    <span className="h-3 rounded-sm" style={{ background: item.swatch }} aria-hidden />
                  ) : null}
                  <Truncate text={item.label} className="text-left" />
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
      </ScrollArea>
    </Command>
  );
}
