import { useEffect, useRef, useState } from "react";

const SEND_MS = 100;
const SETTLE_MS = 400;

interface Queue {
  timer: number;
  at: number;
  queued?: string;
  sent?: string;
}

/**
 * A choice the browser shows at once while the server hears at most one change per SEND_MS; the server's value takes over once it agrees and the echoes of earlier steps have landed.
 * `later` holds a change back for a while, so a popup can finish closing before the scene redraws.
 */
export function useLiveChoice(
  value: string | undefined,
  send?: (next: string) => void,
): [string | undefined, (next: string) => void, (next: string, ms: number) => void] {
  const [pending, setPending] = useState<string>();
  const queue = useRef<Queue>({ timer: 0, at: 0 });
  const latest = useRef(send);
  latest.current = send;

  useEffect(() => {
    if (pending === undefined || pending !== value) return undefined;

    const settle = window.setTimeout(
      () => setPending(undefined),
      Math.max(0, SETTLE_MS - (performance.now() - queue.current.at)),
    );

    return () => window.clearTimeout(settle);
  }, [pending, value]);

  // A change still waiting for its turn goes out when the control goes away.
  useEffect(
    () => () => {
      const last = queue.current;
      window.clearTimeout(last.timer);
      if (last.queued !== undefined && last.queued !== last.sent) latest.current?.(last.queued);
    },
    [],
  );

  function flush(): void {
    const last = queue.current;
    last.timer = 0;
    if (last.queued === undefined || last.queued === last.sent) {
      last.queued = undefined;
      last.sent = undefined;
      return;
    }

    last.sent = last.queued;
    last.at = performance.now();
    latest.current?.(last.queued);
    last.timer = window.setTimeout(flush, SEND_MS);
  }

  function apply(next: string): void {
    setPending(next);
    queue.current.queued = next;
    if (!queue.current.timer) flush();
  }

  function later(next: string, ms: number): void {
    setPending(next);
    queue.current.queued = next;
    window.clearTimeout(queue.current.timer);
    queue.current.timer = window.setTimeout(flush, ms);
  }

  return [pending ?? value, apply, later];
}
