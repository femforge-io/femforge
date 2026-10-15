import { CircleHelp, type LucideProps } from "lucide-react";
import { ICONS, type IconName } from "@/icons";

export interface IconProps {
  name: IconName;
  size?: LucideProps["size"];
  color?: string;
  strokeWidth?: LucideProps["strokeWidth"];
  className?: string;
}

export function Icon({ name, size, color, strokeWidth, className }: IconProps) {
  const known = Object.hasOwn(ICONS, name);
  if (!known) console.warn(`ff-icon: unknown icon "${name}"`);

  const Component = known ? ICONS[name] : CircleHelp;
  return <Component size={size} color={color} strokeWidth={strokeWidth} className={className} />;
}
