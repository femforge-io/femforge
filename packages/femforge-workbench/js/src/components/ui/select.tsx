import * as React from "react"
import { cn } from "cn"
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react"
import { Select as SelectPrimitive } from "radix-ui"

import { useLiveChoice } from "@/components/combobox/live"
import { Glide } from "@/lib/glide"
import { token } from "@/lib/motion"

const STEP_KEYS = new Set(["ArrowDown", "ArrowUp", "Home", "End"])

/** Applies the item an arrow key highlights, for a select whose value is bound: the key marks a step, and the item it focuses applies itself. */
const SelectStep = React.createContext<
  { apply: (value: string) => void; stepping: React.RefObject<boolean> } | undefined
>(undefined)

function Select({
  value,
  onValueChange,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Root>) {
  const [shown, apply, later] = useLiveChoice(value, onValueChange)
  const stepping = React.useRef(false)
  const bound = value !== undefined

  return (
    <SelectStep.Provider value={bound ? { apply, stepping } : undefined}>
      <SelectPrimitive.Root
        data-slot="select"
        value={bound ? shown : undefined}
        // A pick closes the list, and the scene redraws once it has closed.
        onValueChange={bound ? (next) => later(next, token("--duration-panel")) : onValueChange}
        {...props}
      />
    </SelectStep.Provider>
  )
}

function SelectGroup({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Group>) {
  return <SelectPrimitive.Group data-slot="select-group" {...props} />
}

function SelectValue({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Value>) {
  return <SelectPrimitive.Value data-slot="select-value" {...props} />
}

function SelectTrigger({
  className,
  size = "default",
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger> & {
  size?: "sm" | "default"
}) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      data-size={size}
      className={cn(
        "flex w-fit items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 data-[placeholder]:text-muted-foreground data-[size=default]:h-9 data-[size=sm]:h-8 *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-2 dark:bg-input/30 dark:hover:bg-input/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground",
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDownIcon className="size-4 opacity-50" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

function SelectContent({
  className,
  children,
  position = "item-aligned",
  align = "center",
  onKeyDown,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  const stepper = React.useContext(SelectStep)

  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        className={cn(
          "relative z-50 max-h-(--radix-select-content-available-height) min-w-[8rem] origin-(--radix-select-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          position === "popper" &&
            "data-[side=bottom]:translate-y-1 data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1 data-[side=top]:-translate-y-1",
          className
        )}
        position={position}
        align={align}
        onKeyDown={(event) => {
          onKeyDown?.(event)
          if (stepper) stepper.stepping.current = STEP_KEYS.has(event.key)
        }}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport
          className={cn(
            "relative p-1",
            position === "popper" &&
              "h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)] scroll-my-1"
          )}
        >
          {/* The hover wash and the chosen marker each slide from item to item, under every item. */}
          <Glide selector="[data-highlighted]" observe="data-highlighted" speed="micro" className="rounded-r-sm bg-wash-hover" />
          <Glide
            selector="[data-state=checked]"
            observe="data-state"
            speed="micro"
            className="rounded-r-sm border-l-2 border-primary bg-wash-selected"
          />
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
}

function SelectLabel({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      data-slot="select-label"
      className={cn("px-2 py-1.5 text-xs text-muted-foreground", className)}
      {...props}
    />
  )
}

function SelectItemDetail({
  id,
  flag,
  parts,
}: {
  id: string
  flag?: string
  parts: string[]
}) {
  const pieces = [
    ...(flag ? [{ text: flag, className: "font-sans text-foreground/70" }] : []),
    ...parts.map((text) => ({ text, className: "font-mono" })),
  ]
  return (
    <div
      id={id}
      data-slot="select-item-detail"
      className="ml-auto min-w-0 truncate text-right text-label text-muted-foreground"
    >
      {pieces.map((piece, index) => (
        <React.Fragment key={index}>
          {index > 0 && <span aria-hidden="true"> ·</span>}
          {index > 0 && " "}
          <span className={piece.className}>{piece.text}</span>
        </React.Fragment>
      ))}
    </div>
  )
}

function SelectItem({
  className,
  children,
  flag,
  detail,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item> & {
  /** A state word after the label, set apart from the muted detail. */
  flag?: string
  /** Muted parts after the label, outside the text Radix copies into the trigger and matches on typeahead. */
  detail?: string[]
}) {
  const stepper = React.useContext(SelectStep)
  const textId = React.useId()
  const detailId = React.useId()
  const detailed = Boolean(flag) || (detail !== undefined && detail.length > 0)
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      data-value={props.value}
      aria-labelledby={detailed ? `${textId} ${detailId}` : textId}
      className={cn(
        "relative flex w-full cursor-pointer items-center gap-2 rounded-r-sm border-l-2 border-transparent py-1.5 pr-2 pl-1.5 text-sm outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2",
        className
      )}
      {...props}
      onFocus={(event) => {
        props.onFocus?.(event)
        if (!stepper?.stepping.current) return

        stepper.stepping.current = false
        stepper.apply(props.value)
      }}
    >
      {/* ItemText drops a className, since Radix copies it into the trigger, so a wrapper keeps the label on one line. */}
      <span className={detailed ? "shrink-0 whitespace-nowrap" : undefined}>
        <SelectPrimitive.ItemText id={textId}>{children}</SelectPrimitive.ItemText>
      </span>
      {detailed && " "}
      {detailed && (
        <SelectItemDetail id={detailId} flag={flag} parts={detail ?? []} />
      )}
    </SelectPrimitive.Item>
  )
}

function SelectSeparator({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Separator>) {
  return (
    <SelectPrimitive.Separator
      data-slot="select-separator"
      className={cn("pointer-events-none -mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

function SelectScrollUpButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollUpButton>) {
  return (
    <SelectPrimitive.ScrollUpButton
      data-slot="select-scroll-up-button"
      className={cn(
        "flex cursor-default items-center justify-center py-1",
        className
      )}
      {...props}
    >
      <ChevronUpIcon className="size-4" />
    </SelectPrimitive.ScrollUpButton>
  )
}

function SelectScrollDownButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollDownButton>) {
  return (
    <SelectPrimitive.ScrollDownButton
      data-slot="select-scroll-down-button"
      className={cn(
        "flex cursor-default items-center justify-center py-1",
        className
      )}
      {...props}
    >
      <ChevronDownIcon className="size-4" />
    </SelectPrimitive.ScrollDownButton>
  )
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
}
