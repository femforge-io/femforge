import { animate, motion, useMotionValue } from "motion/react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { easeInstrument, useDurations } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface RevealProps {
  open?: boolean;
  className?: string;
  children?: ReactNode;
}

/** Controls that depend on a setting: they expand into place while `open` holds and collapse away when it ends, with the gap above them. */
export function Reveal({ open = false, className, children }: RevealProps) {
  const gap = useRef(0);
  const mounted = useRef(false);
  const margin = useMotionValue(0);
  const [settled, setSettled] = useState(true);
  const { panel } = useDurations();

  // A closed reveal stays mounted, so it gives back the gap its flex parent puts above it. The margin lands on the node as it attaches,
  // before an enclosing accordion measures the height it opens to; a margin set by an effect would arrive after that and end the open in a jump.
  const attach = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node?.parentElement) return;

      gap.current = Number.parseFloat(getComputedStyle(node.parentElement).rowGap) || 0;
      const start = open ? 0 : -gap.current;
      margin.jump(start);
      node.style.marginTop = `${start}px`;
    },
    // Measured once, when the node attaches; later changes of `open` animate the margin below.
    [margin],
  );

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return undefined;
    }

    const glide = animate(margin, open ? 0 : -gap.current, { duration: panel, ease: easeInstrument() });

    return () => glide.stop();
  }, [open, margin, panel]);

  return (
    <motion.div
      ref={attach}
      initial={false}
      animate={open ? { height: "auto", opacity: 1 } : { height: 0, opacity: 0 }}
      transition={{ duration: panel, ease: easeInstrument() }}
      onAnimationStart={() => setSettled(false)}
      onAnimationComplete={() => setSettled(true)}
      style={{ marginTop: margin, overflow: open && settled ? "visible" : "hidden" }}
      inert={!open}
      aria-hidden={!open}
    >
      <div className={cn(className)}>{children}</div>
    </motion.div>
  );
}
