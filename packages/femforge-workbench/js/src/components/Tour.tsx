import { useEffect, useEffectEvent, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { clampStep, neighbour, padded, sameBox, visibleIndices, type Box, type TourStep } from "./tour/steps";

export type { TourSide, TourStep } from "./tour/steps";

const PADDING = 4;
const RADIUS = 6;
const OVERLAY = "0 0 0 9999px var(--scrim)";
const POLL_MS = 100;
const WAIT_MS = 2000;
const NO_STEPS: TourStep[] = [];

export interface TourProps {
  steps: TourStep[];
  open: boolean;
  onOpenChange?: (open: boolean) => void;
  step?: number;
  onStepChange?: (index: number) => void;
  onFinish?: (reason: "done" | "skipped") => void;
  className?: string;
}

/** The rendered element a selector names; an invalid selector or an element with no box counts as absent. */
function locate(target: string): Element | null {
  try {
    const element = document.querySelector(target);
    if (element === null || element.getClientRects().length === 0) return null;

    // A target in a hidden dock tab keeps its box but is not visible.
    return element instanceof HTMLElement && !element.checkVisibility({ visibilityProperty: true }) ? null : element;
  } catch {
    return null;
  }
}

function isPresent(target: string): boolean {
  return locate(target) !== null;
}

function measure(element: Element): Box {
  const { top, left, width, height } = element.getBoundingClientRect();

  return padded({ top, left, width, height }, PADDING);
}

/** A non-modal guided tour: a spotlight over each step's target and a card beside it, skipping steps whose target is not mounted. */
export function Tour({ steps = NO_STEPS, open, onOpenChange, step, onStepChange, onFinish, className }: TourProps) {
  const [visible, setVisible] = useState<number[] | null>(null);
  const [own, setOwn] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const primary = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const signature = JSON.stringify(steps);

  const count = visible?.length ?? 0;
  const position = clampStep(step ?? own, count);
  const at = visible === null || count === 0 ? undefined : visible[position];
  const current = at === undefined ? undefined : steps[at];
  const shown = open && current !== undefined && box !== null;
  const target = current?.target;

  function go(found: number[], index: number): void {
    setVisible(found);
    setOwn(index);
    onStepChange?.(index);
  }

  function finish(reason: "done" | "skipped"): void {
    onFinish?.(reason);
    onOpenChange?.(false);
  }

  function move(delta: 1 | -1): void {
    if (at === undefined) return;

    if (delta > 0 && position >= count - 1) {
      finish("done");
      return;
    }

    const found = visibleIndices(steps, isPresent);
    const index = neighbour(found, at, delta);
    if (index < 0) {
      if (delta > 0) finish("done");
      return;
    }

    go(found, index);
  }

  const start = useEffectEvent((final: boolean): boolean => {
    const first = steps[0];
    if (!final && first !== undefined && !isPresent(first.target)) return false;

    const found = visibleIndices(steps, isPresent);
    if (found.length === 0) {
      onOpenChange?.(false);
      return true;
    }

    setVisible(found);
    setOwn(0);
    return true;
  });

  const recover = useEffectEvent((from: number): void => {
    const found = visibleIndices(steps, isPresent);
    if (found.length === 0) {
      onOpenChange?.(false);
      return;
    }

    const forward = neighbour(found, from, 1);
    go(found, forward >= 0 ? forward : found.length - 1);
  });

  // Dock panels mount after the tour opens, so the first target is polled for before the steps are resolved.
  useEffect(() => {
    if (!open) {
      setVisible(null);
      return;
    }

    const deadline = performance.now() + WAIT_MS;
    let timer = 0;
    const attempt = (): void => {
      if (!start(performance.now() >= deadline)) timer = window.setTimeout(attempt, POLL_MS);
    };
    attempt();

    return () => window.clearTimeout(timer);
  }, [open, signature]);

  useLayoutEffect(() => {
    if (!open || at === undefined || target === undefined) {
      setBox(null);
      return;
    }

    const element = locate(target);
    if (element === null) {
      setBox(null);
      recover(at);
      return;
    }

    element.scrollIntoView({ block: "nearest", inline: "nearest" });
    const update = (): void => {
      const next = measure(element);
      setBox((previous) => (sameBox(previous, next) ? previous : next));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const observer = new ResizeObserver(update);
    observer.observe(element);

    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      observer.disconnect();
    };
  }, [open, at, target]);

  useEffect(() => {
    if (at !== undefined) primary.current?.focus({ preventScroll: true });
  }, [at]);

  // Closing unmounts the card, which drops focus to the body unless the element focused before the tour takes it back.
  useEffect(() => {
    if (!open) return;

    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    return () => {
      const active = document.activeElement;
      if (before?.isConnected && (active === null || active === document.body)) before.focus({ preventScroll: true });
    };
  }, [open]);

  // A non-modal popover does not trap focus, so Tab wraps across the card's enabled buttons by hand.
  function wrap(event: KeyboardEvent<HTMLDivElement>): void {
    const buttons = [...(card.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (first === undefined || last === undefined) return;

    const active = document.activeElement;
    const inside = buttons.some((button) => button === active);
    if (event.shiftKey && (active === first || !inside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !inside)) {
      event.preventDefault();
      first.focus();
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key === "Tab") {
      wrap(event);
      return;
    }

    const onButton = event.target instanceof HTMLButtonElement;
    if (event.key === "ArrowRight" || (event.key === "Enter" && !onButton)) {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      move(-1);
    }
  }

  const last = position >= count - 1;

  return (
    <Popover open={shown} modal={false}>
      {shown
        ? createPortal(
            <PopoverAnchor asChild>
              <div
                aria-hidden
                data-slot="tour-spotlight"
                className="pointer-events-none fixed z-40 ring-2 ring-primary transition-[top,left,width,height] duration-200 ease-out motion-reduce:transition-none"
                style={{ ...box, borderRadius: RADIUS, boxShadow: OVERLAY }}
              />
            </PopoverAnchor>,
            document.body,
          )
        : null}
      {shown ? (
        <PopoverContent
          ref={card}
          data-slot="tour-card"
          side={current.side ?? "bottom"}
          sideOffset={10}
          collisionPadding={12}
          updatePositionStrategy="always"
          aria-labelledby={titleId}
          aria-describedby={bodyId}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            primary.current?.focus({ preventScroll: true });
          }}
          onEscapeKeyDown={(event) => {
            event.preventDefault();
            finish("skipped");
          }}
          onInteractOutside={(event) => event.preventDefault()}
          onKeyDown={onKeyDown}
          className={cn("flex w-72 flex-col gap-1", className)}
        >
          <p className="text-xs text-muted-foreground">
            Step {position + 1} of {count}
          </p>
          <h2 id={titleId} className="font-medium">
            {current.title}
          </h2>
          <p id={bodyId} className="text-sm text-muted-foreground">
            {current.body}
          </p>
          <div className="mt-3 flex items-center gap-1">
            <Button
              variant="ghost"
              size="xs"
              className={cn("-ml-2 text-xs text-muted-foreground", last && "invisible")}
              disabled={last}
              onClick={() => finish("skipped")}
            >
              Skip Tour
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className={cn("ml-auto", position === 0 && "invisible")}
              disabled={position === 0}
              onClick={() => move(-1)}
            >
              Back
            </Button>
            <Button ref={primary} size="sm" onClick={() => move(1)}>
              {last ? "Done" : "Next"}
            </Button>
          </div>
        </PopoverContent>
      ) : null}
    </Popover>
  );
}
