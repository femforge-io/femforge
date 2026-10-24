import { useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";

type Bezier = [number, number, number, number];

const FALLBACK_EASE: Bezier = [0.2, 0, 0, 1];

/** A time token of styles.css, such as `--delay-tooltip`, in milliseconds; 0 when the stylesheet lacks it. */
export function token(name: string): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const value = Number.parseFloat(raw);
  if (Number.isNaN(value)) return 0;

  return raw.endsWith("ms") ? value : value * 1000;
}

/** The `--ease-instrument` curve as motion's cubic-bezier array. */
export function easeInstrument(): Bezier {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--ease-instrument");
  const numbers = /cubic-bezier\(([^)]*)\)/.exec(raw)?.[1].split(",").map(Number);

  return numbers?.length === 4 && numbers.every(Number.isFinite) ? (numbers as Bezier) : FALLBACK_EASE;
}

export interface Durations {
  micro: number;
  panel: number;
  camera: number;
}

/** The motion durations of styles.css in seconds, all 0 under reduced motion, which the stylesheet's own rule cannot reach. */
export function useDurations(): Durations {
  const reduced = useReducedMotion();
  if (reduced) return { micro: 0, panel: 0, camera: 0 };

  return {
    micro: token("--duration-micro") / 1000,
    panel: token("--duration-panel") / 1000,
    camera: token("--duration-camera") / 1000,
  };
}

/** False for the first two frames after mount, so what a popover opens with snaps into place instead of animating. */
export function useSettled(): boolean {
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setSettled(true));
    });

    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, []);

  return settled;
}
