import { useRef, useState } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export interface TruncateProps {
  text: string;
  className?: string;
}

/** One line of text cut with an ellipsis, whose tooltip shows the full text only while it is cut. */
export function Truncate({ text, className }: TruncateProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);

  function onOpenChange(next: boolean) {
    const element = ref.current;
    if (next && element) setCut(element.scrollWidth > element.clientWidth);
    setOpen(next);
  }

  return (
    <Tooltip open={open && cut} onOpenChange={onOpenChange}>
      <TooltipTrigger asChild>
        <span ref={ref} className={cn("min-w-0 truncate", className)}>
          {text}
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}
