import { motion } from "motion/react";
import { Children, type ReactNode } from "react";

import { easeInstrument, useDurations } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface DrawerProps {
  open?: boolean;
  className?: string;
  children?: ReactNode;
}

/** A bar pinned to the bottom of its parent that the body slides up under while `open` holds, so the bar rides up and heads the open drawer; the first child is the bar, the rest the body. */
export function Drawer({ open = false, className, children }: DrawerProps) {
  const [bar, ...body] = Children.toArray(children);
  const { panel } = useDurations();

  return (
    <div className={cn("absolute inset-x-0 bottom-0 z-40 flex flex-col", className)}>
      {bar}
      <motion.div
        initial={false}
        animate={{ height: open ? "auto" : 0 }}
        transition={{ duration: panel, ease: easeInstrument() }}
        className="overflow-hidden"
        inert={!open}
        aria-hidden={!open}
      >
        {body}
      </motion.div>
    </div>
  );
}
