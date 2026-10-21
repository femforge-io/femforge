import { Children, type ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export interface TipProps {
  content?: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  children?: ReactNode;
}

/** Gives its one child a tooltip that opens on hover and on keyboard focus; empty content renders the child alone. */
export function Tip({ content, side, children }: TipProps) {
  const child = Children.toArray(children)[0];
  if (content === undefined || content === null || content === "") return <>{child}</>;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{child}</TooltipTrigger>
      <TooltipContent side={side}>{content}</TooltipContent>
    </Tooltip>
  );
}
