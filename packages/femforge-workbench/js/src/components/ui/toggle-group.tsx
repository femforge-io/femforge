import * as React from "react"
import { type VariantProps } from "class-variance-authority"
import { cn } from "cn"
import { ToggleGroup as ToggleGroupPrimitive } from "radix-ui"

import { toggleVariants } from "@/components/ui/toggle"
import { Glide } from "@/lib/glide"

const ToggleGroupContext = React.createContext<
  VariantProps<typeof toggleVariants> & {
    spacing?: number
    /** A single-choice group, whose one marker slides between its items. */
    gliding?: boolean
  }
>({
  size: "default",
  variant: "default",
  spacing: 0,
})

function ToggleGroup({
  className,
  variant,
  size,
  spacing = 0,
  children,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Root> &
  VariantProps<typeof toggleVariants> & {
    spacing?: number
  }) {
  const single = props.type === "single" ? props : undefined
  const [own, setOwn] = React.useState(single?.defaultValue)
  const chosen = single ? (single.value ?? own) : undefined
  const root = single
    ? {
        ...single,
        onValueChange: (next: string) => {
          setOwn(next)
          single.onValueChange?.(next)
        },
      }
    : props

  return (
    <ToggleGroupPrimitive.Root
      data-slot="toggle-group"
      data-variant={variant}
      data-size={size}
      data-spacing={spacing}
      style={{ "--gap": spacing } as React.CSSProperties}
      className={cn(
        "group/toggle-group flex w-fit items-center gap-px rounded-md border border-input p-px",
        single && "relative",
        className
      )}
      {...root}
    >
      {single ? (
        <Glide
          chosen={chosen}
          selector="[data-slot=toggle-group-item][data-state=on]"
          className="rounded-[calc(var(--radius-md)-1px)] bg-wash-selected"
        />
      ) : null}
      <ToggleGroupContext.Provider value={{ variant, size, spacing, gliding: single !== undefined }}>
        {children}
      </ToggleGroupContext.Provider>
    </ToggleGroupPrimitive.Root>
  )
}

function ToggleGroupItem({
  className,
  children,
  variant,
  size,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item> &
  VariantProps<typeof toggleVariants>) {
  const context = React.useContext(ToggleGroupContext)

  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      data-variant={context.variant || variant}
      data-size={context.size || size}
      data-spacing={context.spacing}
      className={cn(
        toggleVariants({
          variant: context.variant || variant,
          size: context.size || size,
        }),
        "w-auto min-w-0 shrink-0 px-3 focus:z-10 focus-visible:z-10",
        "rounded-[calc(var(--radius-md)-1px)] border-0",
        context.gliding && "relative data-[state=on]:bg-transparent data-[state=on]:hover:bg-transparent",
        className
      )}
      {...props}
    >
      {children}
    </ToggleGroupPrimitive.Item>
  )
}

export { ToggleGroup, ToggleGroupItem }
