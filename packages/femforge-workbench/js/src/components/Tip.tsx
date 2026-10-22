import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export interface TipProps {
  content?: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  /** Marks a definition, which explains a value, with the accent edge; a label, which names what a control does, goes without. */
  definition?: boolean;
  children?: ReactNode;
}

interface TriggerProps {
  child: ReactElement;
  onClick?: unknown;
  [prop: string]: unknown;
}

/** Hands the trigger's props to the child, all but its click handler: trame spreads them over the child's own props, so it would replace the child's click. Pointer down still closes the tooltip. */
function Trigger({ child, onClick: _close, ...props }: TriggerProps) {
  return cloneElement(child, props);
}

/** Gives its one child a tooltip, below it by default, that opens on hover and on keyboard focus; empty content renders the child alone. */
export function Tip({ content, side = "bottom", definition = false, children }: TipProps) {
  const child = Children.toArray(children)[0];
  if (content === undefined || content === null || content === "") return <>{child}</>;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{isValidElement(child) ? <Trigger child={child} /> : child}</TooltipTrigger>
      <TooltipContent side={side} definition={definition}>
        {content}
      </TooltipContent>
    </Tooltip>
  );
}
