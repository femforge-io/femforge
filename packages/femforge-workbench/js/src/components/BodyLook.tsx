import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { EyeIcon, EyeOffIcon } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Reveal } from "@/components/Reveal";
import { Tip } from "@/components/Tip";
import { easeInstrument, token, useDurations, useSettled } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { CHECKER, diagonal, hexToHsv, hsvToHex, percent, swatchFill, type Hsv, type Paint } from "./body-look/color";
import { holdSelection } from "./slider/drag";
import { ChoiceList } from "./combobox/ChoiceList";
import { useLiveChoice } from "./combobox/live";
import type { ComboboxItem } from "./combobox/sections";

export type { Paint } from "./body-look/color";

type Mode = Exclude<Paint, "mixed">;

const NO_FIELDS = "This run has no fields";
const MODES: { value: Mode; label: string }[] = [
  { value: "field", label: "Field" },
  { value: "colormap", label: "Colormap" },
  { value: "solid", label: "Fixed" },
];

export interface BodyLookProps {
  paint?: Paint;
  colors?: (string | null)[];
  shown?: boolean | "mixed";
  opacity?: number;
  fieldGradient?: string;
  colormap?: string;
  colormaps?: ComboboxItem[];
  filters?: string[];
  fieldDisabled?: boolean;
  palette?: string[];
  label?: string;
  disabled?: boolean;
  className?: string;
  onShownChange?: (shown: boolean) => void;
  onOpacityChange?: (opacity: number) => void;
  onOpacityCommit?: (opacity: number) => void;
  onPaintChange?: (paint: Mode) => void;
  onColorChange?: (color: string) => void;
  onColormapChange?: (colormap: string) => void;
}

/** One body's look as a swatch that opens its editor: visibility and opacity on top, then where its color comes from. */
export function BodyLook({
  paint = "field",
  colors = [],
  shown = true,
  opacity = 1,
  fieldGradient,
  colormap,
  colormaps = [],
  filters = [],
  fieldDisabled = false,
  palette = [],
  label = "Body",
  disabled,
  className,
  onShownChange,
  onOpacityChange,
  onOpacityCommit,
  onPaintChange,
  onColorChange,
  onColormapChange,
}: BodyLookProps) {
  const [open, setOpen] = useState(false);
  // The browser owns the chosen mode: a click shows its content at once, and the server's paint takes over once it agrees.
  const [pending, setPending] = useState<Mode>();
  const send = useRef(0);
  // Likewise the body colormap: a step shows at once, and the server hears it throttled.
  const [map, applyMap] = useLiveChoice(colormap, onColormapChange);
  const { panel } = useDurations();
  const visible = shown !== false;
  const mode = pending ?? paint;
  const chosen = colormaps.find((item) => item.value === map);
  const modeLabel = MODES.find((each) => each.value === mode)?.label ?? "Mixed";
  const facts = [
    modeLabel,
    opacity < 1 ? percent(opacity) : null,
    shown === false ? "hidden" : shown === "mixed" ? "partly hidden" : null,
  ];

  useEffect(() => {
    if (pending !== undefined && pending === paint) setPending(undefined);
  }, [pending, paint]);

  useEffect(() => () => window.clearTimeout(send.current), []);

  // Reaching for a hidden body's dimmed opacity shows the body again.
  function reveal(): void {
    if (!visible) onShownChange?.(true);
  }

  function pick(next: string): void {
    if (next === "field" && fieldDisabled) return;

    setPending(next as Mode);
    // The scene redraws after the popover has grown, so the redraw never stalls the animation.
    window.clearTimeout(send.current);
    send.current = window.setTimeout(() => onPaintChange?.(next as Mode), token("--duration-panel"));
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tip content={`${label}: ${facts.filter(Boolean).join(", ")}`}>
        <PopoverTrigger asChild disabled={disabled}>
          <button
            type="button"
            aria-label={`${label} look: ${facts.filter(Boolean).join(", ")}`}
            className={cn(
              "rounded-full outline-none transition-[box-shadow] duration-(--duration-micro) ease-instrument hover:shadow-[0_0_0_3px_var(--wash-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50",
              className,
            )}
          >
            <Swatch
              paint={paint}
              colors={colors}
              shown={shown}
              opacity={opacity}
              fieldGradient={fieldGradient}
              mapGradient={chosen?.swatch}
            />
          </button>
        </PopoverTrigger>
      </Tip>
      <PopoverContent
        align="start"
        side="bottom"
        sticky="always"
        collisionPadding={8}
        className="w-85 p-2.5"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          (event.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>("[role=slider]")?.focus();
        }}
      >
        <div className="grid grid-cols-[1.5rem_minmax(0,1fr)_2.5rem] items-center gap-2">
          <Tip content={visible ? "Hide" : "Show"} focus={false}>
            <button
              type="button"
              aria-label={`Hide ${label}`}
              aria-pressed={shown === "mixed" ? "mixed" : !visible}
              onClick={() => onShownChange?.(!visible)}
              className="inline-grid size-6 place-items-center rounded-md transition-[background-color] duration-(--duration-micro) ease-instrument hover:bg-wash-hover focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none aria-pressed:bg-wash-selected"
            >
              {visible ? <EyeIcon className="size-4" /> : <EyeOffIcon className="size-4" />}
            </button>
          </Tip>
          <Slider
            value={[opacity]}
            min={0}
            max={1}
            step={0.01}
            onPointerDown={reveal}
            onValueChange={(next) => {
              reveal();
              onOpacityChange?.(next[0]);
            }}
            onValueCommit={(next) => onOpacityCommit?.(next[0])}
            aria-label={`${label} opacity`}
            style={
              {
                "--ff-opacity-tint": colors.find(Boolean) ?? "var(--foreground)",
                "--ff-checker": CHECKER,
              } as CSSProperties
            }
            className={cn(
              !visible && "opacity-50",
              "[&_[data-slot=slider-range]]:bg-transparent [&_[data-slot=slider-track]]:h-2.5 [&_[data-slot=slider-track]]:[background:linear-gradient(90deg,transparent,var(--ff-opacity-tint)),var(--ff-checker)]",
            )}
          />
          <span className={cn("text-right text-label tabular-nums text-muted-foreground", !visible && "opacity-50")}>
            {percent(opacity)}
          </span>
        </div>
        <Reveal open={visible}>
          <Tabs value={mode === "mixed" ? "" : mode} onValueChange={pick} className="mt-2.5 gap-2.5">
            <TabsList className="grid w-full grid-cols-3">
              {MODES.map((each) => {
                const off = each.value === "field" && fieldDisabled;
                const trigger = (
                  <TabsTrigger
                    key={each.value}
                    value={each.value}
                    aria-disabled={off || undefined}
                    className={cn(off && "cursor-not-allowed opacity-50")}
                  >
                    {each.label}
                  </TabsTrigger>
                );

                return off ? (
                  <Tip key={each.value} content={NO_FIELDS}>
                    {trigger}
                  </Tip>
                ) : (
                  trigger
                );
              })}
            </TabsList>
          </Tabs>
          <AutoHeight>
            {/* The old mode fades out over the new one as it fades in, while the height follows the new one. */}
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.div
                key={mode}
                className={cn((mode === "colormap" || mode === "solid") && "pt-2.5")}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: panel, ease: easeInstrument() }}
              >
                {mode === "colormap" ? (
                  <ChoiceList
                    items={colormaps}
                    filters={filters}
                    value={map}
                    onStep={applyMap}
                    onPick={(next) => next !== map && applyMap(next)}
                    searchPlaceholder="Search colormaps"
                    emptyText="No colormaps match. Clear the search or the filters."
                    reserve="13rem"
                  />
                ) : null}
                {mode === "solid" ? (
                  <Picker
                    color={colors.find(Boolean) ?? palette[0] ?? ""}
                    palette={palette}
                    onColorChange={onColorChange}
                  />
                ) : null}
              </motion.div>
            </AnimatePresence>
          </AutoHeight>
        </Reveal>
      </PopoverContent>
    </Popover>
  );
}

/** Animates its own height to its content's, so switching modes grows and shrinks the popover instead of jumping. */
function AutoHeight({ children }: { children: ReactNode }) {
  const inner = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number>();
  const settled = useSettled();

  useLayoutEffect(() => {
    const element = inner.current;
    if (!element) return undefined;

    const fit = (): void => setHeight(element.offsetHeight);
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(element);

    return () => watch.disconnect();
  }, []);

  return (
    <div
      className={cn("overflow-clip", settled && "transition-[height] duration-(--duration-panel) ease-instrument")}
      style={{ height }}
    >
      <div ref={inner} className="relative flow-root">
        {children}
      </div>
    </div>
  );
}

interface SwatchProps {
  paint: Paint;
  colors: (string | null)[];
  shown: boolean | "mixed";
  opacity: number;
  fieldGradient?: string;
  mapGradient?: string;
}

/** The 18 px circle: the body's fill over a checkerboard its opacity lets through, a corner dot of the body colormap for an auto color, an outlined eye-off when hidden. */
function Swatch({ paint, colors, shown, opacity, fieldGradient, mapGradient }: SwatchProps) {
  if (shown === false) {
    return (
      <span className="relative grid size-4.5 place-items-center rounded-full text-muted-foreground shadow-[inset_0_0_0_1px_var(--border)]">
        <EyeOffIcon className="size-3" aria-hidden />
      </span>
    );
  }

  return (
    <span
      className="relative block size-4.5 rounded-full shadow-[inset_0_0_0_1px_var(--swatch-ring)]"
      style={{ background: CHECKER }}
    >
      <span
        className="absolute inset-0 rounded-full"
        style={{ background: swatchFill(paint, colors, fieldGradient), opacity }}
      />
      {paint === "colormap" && mapGradient ? (
        <span
          className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full shadow-[0_0_0_2px_var(--background)]"
          style={{ background: diagonal(mapGradient) }}
        />
      ) : null}
    </span>
  );
}

interface PickerProps {
  color: string;
  palette: string[];
  onColorChange?: (color: string) => void;
}

const FINE = 0.01;
const COARSE = 0.1;

/** A fixed color: saturation and value on the square, hue on the strip, or one of the palette chips; the arrow keys move either knob. */
function Picker({ color, palette, onColorChange }: PickerProps) {
  const [hsv, setHsv] = useState<Hsv>(() => hexToHsv(color));
  const dragging = useRef(false);
  const frame = useRef(0);
  const latest = useRef(hsv);
  latest.current = hsv;

  // A color set from outside (a chip, another tab) moves the knobs, never during a drag, whose own values are newer; a grey keeps its hue.
  useEffect(() => {
    if (dragging.current) return;

    setHsv((current) => {
      const next = hexToHsv(color);
      return hsvToHex(current) === color.toUpperCase() ? current : { ...next, h: next.s === 0 ? current.h : next.h };
    });
  }, [color]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // One server change per frame while the knob moves.
  function send(next: Hsv): void {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => onColorChange?.(hsvToHex(next)));
  }

  function set(next: Hsv): void {
    setHsv(next);
    send(next);
  }

  function drag(event: ReactPointerEvent<HTMLDivElement>, apply: (x: number, y: number) => Hsv): void {
    const box = event.currentTarget.getBoundingClientRect();
    const release = holdSelection(document.documentElement.style);
    dragging.current = true;
    const move = (at: PointerEvent | ReactPointerEvent): void =>
      set(
        apply(
          Math.min(1, Math.max(0, (at.clientX - box.left) / box.width)),
          Math.min(1, Math.max(0, (at.clientY - box.top) / box.height)),
        ),
      );
    const end = (): void => {
      release();
      dragging.current = false;
      cancelAnimationFrame(frame.current);
      onColorChange?.(hsvToHex(latest.current));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };

    move(event);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }

  function nudge(event: ReactKeyboardEvent<HTMLDivElement>, axes: "square" | "hue"): void {
    const by = event.shiftKey ? COARSE : FINE;
    const clamp = (x: number): number => Math.min(1, Math.max(0, x));
    const keys: Record<string, (h: Hsv) => Hsv> =
      axes === "hue"
        ? {
            ArrowLeft: (h) => ({ ...h, h: Math.max(0, h.h - by * 100) }),
            ArrowDown: (h) => ({ ...h, h: Math.max(0, h.h - by * 100) }),
            ArrowRight: (h) => ({ ...h, h: Math.min(360, h.h + by * 100) }),
            ArrowUp: (h) => ({ ...h, h: Math.min(360, h.h + by * 100) }),
            Home: (h) => ({ ...h, h: 0 }),
            End: (h) => ({ ...h, h: 360 }),
          }
        : {
            ArrowLeft: (h) => ({ ...h, s: clamp(h.s - by) }),
            ArrowRight: (h) => ({ ...h, s: clamp(h.s + by) }),
            ArrowDown: (h) => ({ ...h, v: clamp(h.v - by) }),
            ArrowUp: (h) => ({ ...h, v: clamp(h.v + by) }),
            Home: (h) => ({ ...h, s: 0 }),
            End: (h) => ({ ...h, s: 1 }),
          };
    const change = keys[event.key];
    if (!change) return;

    event.preventDefault();
    set(change(hsv));
  }

  return (
    <div className="flex flex-col gap-2.5 pb-1">
      <div
        role="slider"
        aria-label="Saturation and brightness"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(hsv.s * 100)}
        aria-valuetext={`Saturation ${Math.round(hsv.s * 100)} %, brightness ${Math.round(hsv.v * 100)} %`}
        tabIndex={0}
        onPointerDown={(event) => drag(event, (x, y) => ({ ...latest.current, s: x, v: 1 - y }))}
        onKeyDown={(event) => nudge(event, "square")}
        className="ff-sv relative h-32 cursor-crosshair touch-none rounded-lg outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        style={{ "--hue": hsv.h } as CSSProperties}
      >
        <span
          className="ff-knob absolute size-3.5 -translate-1/2 rounded-full"
          style={{ left: `${hsv.s * 100}%`, top: `${(1 - hsv.v) * 100}%` }}
        />
      </div>
      <div
        role="slider"
        aria-label="Hue"
        aria-valuemin={0}
        aria-valuemax={360}
        aria-valuenow={Math.round(hsv.h)}
        tabIndex={0}
        onPointerDown={(event) => drag(event, (x) => ({ ...latest.current, h: x * 360 }))}
        onKeyDown={(event) => nudge(event, "hue")}
        className="ff-hue-strip relative h-2.5 cursor-pointer touch-none rounded-full outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <span
          className="ff-knob absolute top-1/2 size-3.5 -translate-1/2 rounded-full"
          style={{ left: `${(hsv.h / 360) * 100}%` }}
        />
      </div>
      <div className="grid grid-cols-8 gap-1.5">
        {palette.map((chip, index) => (
          <button
            key={chip}
            type="button"
            aria-label={`Color ${index + 1}, ${chip}`}
            aria-pressed={chip.toUpperCase() === color.toUpperCase()}
            onClick={() => onColorChange?.(chip)}
            className="aspect-square rounded-full shadow-[inset_0_0_0_1px_var(--swatch-ring)] outline-none transition-[box-shadow] duration-(--duration-micro) ease-instrument hover:shadow-[0_0_0_2px_var(--wash-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-pressed:shadow-[0_0_0_2px_var(--foreground)]"
            style={{ background: chip }}
          />
        ))}
      </div>
    </div>
  );
}
