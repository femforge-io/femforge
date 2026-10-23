"use client"

import * as React from "react"
import { cn } from "cn"
import { CheckIcon } from "lucide-react"
import { ContextMenu as ContextMenuPrimitive } from "radix-ui"

const RIGHT_BUTTON = 2

function ContextMenu({
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.Root>) {
  return <ContextMenuPrimitive.Root data-slot="context-menu" {...props} />
}

/** The area a right click opens the menu over; with `dragTolerance`, a right press that moved further than that many pixels drags instead, as the 3D view's right-drag zoom does. */
function ContextMenuTrigger({
  dragTolerance,
  ref,
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.Trigger> & {
  dragTolerance?: number
}) {
  const [area, setArea] = React.useState<HTMLElement | null>(null)

  // A VTK.wasm canvas cancels every contextmenu it receives, which Radix reads as a request not to open, so the area takes the event
  // before the canvas does and hands Radix an uncancelled copy of a press that stayed put.
  React.useEffect(() => {
    if (!area || dragTolerance === undefined) return undefined

    let pressed: { x: number; y: number } | null = null
    const press = (event: PointerEvent): void => {
      if (event.button === RIGHT_BUTTON) pressed = { x: event.clientX, y: event.clientY }
    }
    const menu = (event: MouseEvent): void => {
      if (event.target === area) return

      event.preventDefault()
      event.stopPropagation()
      const start = pressed
      pressed = null
      if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > dragTolerance) return

      area.dispatchEvent(
        new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: event.clientX,
          clientY: event.clientY,
          screenX: event.screenX,
          screenY: event.screenY,
          button: RIGHT_BUTTON,
        })
      )
    }

    area.addEventListener("pointerdown", press, true)
    area.addEventListener("contextmenu", menu, true)

    return () => {
      area.removeEventListener("pointerdown", press, true)
      area.removeEventListener("contextmenu", menu, true)
    }
  }, [area, dragTolerance])

  const attach = React.useCallback(
    (node: HTMLElement | null) => {
      setArea(node)
      if (typeof ref === "function") ref(node)
      else if (ref) ref.current = node
    },
    [ref]
  )

  return <ContextMenuPrimitive.Trigger data-slot="context-menu-trigger" ref={attach} {...props} />
}

function ContextMenuContent({
  className,
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.Content>) {
  return (
    <ContextMenuPrimitive.Portal>
      <ContextMenuPrimitive.Content
        data-slot="context-menu-content"
        className={cn(
          "z-50 max-h-(--radix-context-menu-content-available-height) min-w-[8rem] origin-(--radix-context-menu-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          className
        )}
        {...props}
      />
    </ContextMenuPrimitive.Portal>
  )
}

function ContextMenuItem({
  className,
  inset,
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.Item> & {
  inset?: boolean
}) {
  return (
    <ContextMenuPrimitive.Item
      data-slot="context-menu-item"
      data-inset={inset}
      className={cn(
        "relative flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none focus:bg-wash-hover data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[inset]:pl-8 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground",
        className
      )}
      {...props}
    />
  )
}

function ContextMenuCheckboxItem({
  className,
  children,
  checked,
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.CheckboxItem>) {
  return (
    <ContextMenuPrimitive.CheckboxItem
      data-slot="context-menu-checkbox-item"
      className={cn(
        "relative flex cursor-pointer items-center gap-2 rounded-sm py-1.5 pr-2 pl-8 text-sm outline-hidden select-none focus:bg-wash-hover data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      checked={checked}
      {...props}
    >
      <span className="pointer-events-none absolute left-2 flex size-3.5 items-center justify-center">
        <ContextMenuPrimitive.ItemIndicator>
          <CheckIcon className="size-4" />
        </ContextMenuPrimitive.ItemIndicator>
      </span>
      {children}
    </ContextMenuPrimitive.CheckboxItem>
  )
}

function ContextMenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.Separator>) {
  return (
    <ContextMenuPrimitive.Separator
      data-slot="context-menu-separator"
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

export {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuCheckboxItem,
  ContextMenuSeparator,
}
