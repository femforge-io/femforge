import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { easeInstrument, token, useDurations } from "@/lib/motion";

// Every busy area shares one line on the window's edge: the first area to show it draws it.
const holders = new Set<symbol>();
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);

  return () => listeners.delete(listener);
}

function leader(): symbol | undefined {
  return holders.values().next().value;
}

function hold(id: symbol, held: boolean): void {
  if (held) holders.add(id);
  else holders.delete(id);
  listeners.forEach((listener) => listener());
}

export interface BusyProps {
  active?: boolean;
}

/** Server work in progress for the area around it: after the busy delay, a 2 px line along the window's bottom edge, held for at least the busy hold. */
export function Busy({ active = false }: BusyProps) {
  const anchor = useRef<HTMLSpanElement>(null);
  const id = useRef(Symbol("busy")).current;
  const since = useRef(0);
  const [shown, setShown] = useState(false);
  const first = useSyncExternalStore(subscribe, leader);
  const { micro } = useDurations();

  useEffect(() => {
    const area = anchor.current?.parentElement ?? null;

    if (active && !shown) {
      const timer = setTimeout(() => {
        area?.setAttribute("aria-busy", "true");
        since.current = performance.now();
        hold(id, true);
        setShown(true);
      }, token("--delay-busy"));

      return () => clearTimeout(timer);
    }

    if (!active && shown) {
      const timer = setTimeout(
        () => {
          area?.removeAttribute("aria-busy");
          hold(id, false);
          setShown(false);
        },
        Math.max(0, since.current + token("--busy-hold") - performance.now()),
      );

      return () => clearTimeout(timer);
    }

    return undefined;
  }, [active, shown, id]);

  useEffect(() => {
    const area = anchor.current?.parentElement;

    return () => {
      area?.removeAttribute("aria-busy");
      hold(id, false);
    };
  }, [id]);

  return (
    <span ref={anchor} hidden>
      {createPortal(
        <AnimatePresence>
          {shown && first === id ? (
            <motion.div
              key="busy"
              aria-hidden
              data-slot="busy-line"
              className="pointer-events-none fixed inset-x-0 bottom-0 z-50 h-0.5 overflow-hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: micro, ease: easeInstrument() }}
            >
              <div className="absolute inset-y-0 w-1/3 bg-primary/60 animate-[ff-slide_1.4s_ease-in-out_infinite] motion-reduce:animate-none" />
            </motion.div>
          ) : null}
        </AnimatePresence>,
        document.body,
      )}
    </span>
  );
}
