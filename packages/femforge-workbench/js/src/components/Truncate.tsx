import { useRef, useState, type CSSProperties } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const PAD_X = 10;
const PAD_Y = 6;

export interface TruncateProps {
  text: string;
  className?: string;
}

/** One line of text cut with an ellipsis, whose full text opens in place over it while it is cut, running on to the right. */
export function Truncate({ text, className }: TruncateProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);
  const [fit, setFit] = useState<{ height: number; font: CSSProperties }>({ height: 0, font: {} });

  function onOpenChange(next: boolean) {
    const element = ref.current;
    if (next && element) {
      const style = getComputedStyle(element);
      setCut(element.scrollWidth > element.clientWidth);
      setFit({
        height: element.getBoundingClientRect().height,
        font: { fontSize: style.fontSize, lineHeight: style.lineHeight, fontWeight: style.fontWeight },
      });
    }
    setOpen(next);
  }

  return (
    <Tooltip open={open && cut} onOpenChange={onOpenChange}>
      <TooltipTrigger asChild>
        <span ref={ref} className={cn("min-w-0 truncate", className)}>
          {text}
        </span>
      </TooltipTrigger>
      {/* Laid over the cut text, letter on letter, and click-through, so the row under it still takes the click. */}
      <TooltipContent
        side="bottom"
        align="start"
        sideOffset={-(fit.height + PAD_Y)}
        alignOffset={-PAD_X}
        avoidCollisions={false}
        className="pointer-events-none animate-none data-[state=closed]:animate-none"
        style={fit.font}
      >
        {text}
      </TooltipContent>
    </Tooltip>
  );
}
