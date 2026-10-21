import { motion } from "motion/react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { easeInstrument, useDurations } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface RevealProps {
  open?: boolean;
  className?: string;
  children?: ReactNode;
}

/** Controls that depend on a setting: they expand into place while `open` holds and collapse away when it ends, with the gap above them. */
export function Reveal({ open = false, className, children }: RevealProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [gap, setGap] = useState(0);
  const [settled, setSettled] = useState(true);
  const { panel } = useDurations();

  // A closed reveal stays mounted, so it must also give back the gap its flex parent puts above it.
  useLayoutEffect(() => {
    const parent = ref.current?.parentElement;
    if (parent) setGap(Number.parseFloat(getComputedStyle(parent).rowGap) || 0);
  }, []);

  return (
    <motion.div
      ref={ref}
      initial={false}
      animate={open ? { height: "auto", opacity: 1, marginTop: 0 } : { height: 0, opacity: 0, marginTop: -gap }}
      transition={{ duration: panel, ease: easeInstrument() }}
      onAnimationStart={() => setSettled(false)}
      onAnimationComplete={() => setSettled(true)}
      style={{ overflow: open && settled ? "visible" : "hidden" }}
      inert={!open}
      aria-hidden={!open}
    >
      <div className={cn(className)}>{children}</div>
    </motion.div>
  );
}
