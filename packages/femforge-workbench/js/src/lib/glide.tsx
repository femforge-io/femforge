import { motion } from "motion/react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { easeInstrument, token, useDurations } from "@/lib/motion";
import { cn } from "@/lib/utils";

type Box = { x: number; y: number; width: number; height: number; shown: boolean; slide: boolean };

export interface GlideProps {
  /** The chosen value; a change slides the marker. */
  chosen?: unknown;
  /** Finds the chosen item among the marker's siblings. */
  selector: string;
  /** An attribute whose change on any item also slides the marker, such as `data-highlighted` for a hover React does not see. */
  observe?: string;
  /** `micro` for a marker that follows held keys or the pointer, `panel` otherwise. */
  speed?: "micro" | "panel";
  className?: string;
}

/** The marker of the chosen item: the first child of a positioned set of items, under every one of them, sliding to the chosen one and fading when the choice leaves the set. */
export function Glide({ chosen, selector, observe, speed = "panel", className }: GlideProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [box, setBox] = useState<Box>();
  // Hidden while its popover opens, when a list can still be laying out its first rows.
  const [ready, setReady] = useState(false);
  const durations = useDurations();

  useEffect(() => {
    const wait = window.setTimeout(() => setReady(true), token("--duration-micro"));

    return () => window.clearTimeout(wait);
  }, []);

  useLayoutEffect(() => {
    const container = ref.current?.parentElement;
    if (!container) return undefined;

    // Only a new choice slides; the first place, a return from nowhere and a layout change snap.
    const measure = (slide: boolean): void => {
      const item = container.querySelector<HTMLElement>(selector);
      if (!item) {
        setBox((last) => last && { ...last, shown: false, slide: false });
        return;
      }

      // Measured on screen, so rows moved by a transform count, and unscaled, so an opening popover's zoom does not.
      const outer = container.getBoundingClientRect();
      const inner = item.getBoundingClientRect();
      const scale = container.offsetWidth ? outer.width / container.offsetWidth : 1;
      setBox((last) => ({
        x: (inner.left - outer.left) / scale - container.clientLeft + container.scrollLeft,
        y: (inner.top - outer.top) / scale - container.clientTop + container.scrollTop,
        width: item.offsetWidth,
        height: item.offsetHeight,
        shown: true,
        slide: slide && last?.shown === true,
      }));
    };
    measure(true);
    const resize = new ResizeObserver(() => measure(false));
    resize.observe(container);
    const mutate = new MutationObserver(() => measure(true));
    if (observe) mutate.observe(container, { subtree: true, attributes: true, attributeFilter: [observe] });

    return () => {
      resize.disconnect();
      mutate.disconnect();
    };
  }, [chosen, selector, observe]);

  const move = { duration: box?.slide ? durations[speed] : 0, ease: easeInstrument() };

  return (
    <motion.span
      ref={ref}
      aria-hidden
      className={cn("pointer-events-none absolute top-0 left-0", className)}
      initial={false}
      animate={{
        ...(box ? { x: box.x, y: box.y, width: box.width, height: box.height } : {}),
        opacity: box?.shown && ready ? 1 : 0,
      }}
      transition={{
        x: move,
        y: move,
        width: move,
        height: move,
        opacity: { duration: durations.micro, ease: easeInstrument() },
      }}
    />
  );
}
