import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { easeInstrument, useDurations } from "@/lib/motion";

/** A message to show; each new `seq` shows its text again, even when the text repeats. */
export interface ToastMessage {
  seq: number;
  text: string;
}

const SHOWN_MS = 3000;

/** A short message that rises in the bottom-right corner over the status bar and fades after a few seconds. */
export function Toast({ message }: { message?: ToastMessage | null }) {
  const [shown, setShown] = useState<ToastMessage | null>(null);
  const { panel } = useDurations();

  useEffect(() => {
    if (!message?.text) return undefined;

    setShown(message);
    const timer = window.setTimeout(() => setShown(null), SHOWN_MS);

    return () => window.clearTimeout(timer);
    // A message is identified by its seq; a re-render with the same one must not show it again.
  }, [message?.seq]);

  return (
    <div className="pointer-events-none fixed right-3 bottom-9 z-50" role="status" aria-live="polite">
      <AnimatePresence>
        {shown ? (
          <motion.div
            key={shown.seq}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={{ duration: panel, ease: easeInstrument() }}
            className="rounded-md bg-popover px-3 py-1.5 text-label text-popover-foreground shadow-md ring-1 ring-foreground/10"
          >
            {shown.text}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
