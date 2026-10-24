import * as React from "react"
import { cn } from "cn"
import { Slider as SliderPrimitive } from "radix-ui"

import { holdSelection } from "@/components/slider/drag"
import { stepped, wheelSteps } from "@/components/slider/wheel"

const COMMIT_DELAY = 300

function Slider({
  className,
  defaultValue,
  value,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  onValueChange,
  onValueCommit,
  "aria-label": label,
  onPointerDown,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Root>) {
  const root = React.useRef<HTMLSpanElement>(null)
  const latest = React.useRef({ value, min, max, step, disabled, onValueChange, onValueCommit })
  latest.current = { value, min, max, step, disabled, onValueChange, onValueCommit }

  // React registers wheel listeners as passive, and only an active one may keep the page from scrolling under a stepped slider.
  React.useEffect(() => {
    const element = root.current
    if (!element) return undefined

    let timer = 0
    const wheel = (event: WheelEvent): void => {
      const { value: values, min: low, max: high, step: grid, disabled: off, onValueChange: change, onValueCommit: commit } = latest.current
      if (off || !Array.isArray(values) || event.deltaY === 0) return

      event.preventDefault()
      const { left, width } = element.getBoundingClientRect()
      const next = stepped(values, width ? (event.clientX - left) / width : 0, wheelSteps(event.deltaY, event.deltaMode), low, high, grid)
      if (next.every((each, at) => each === values[at])) return

      change?.(next)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => commit?.(next), COMMIT_DELAY)
    }

    element.addEventListener("wheel", wheel, { passive: false })

    return () => {
      window.clearTimeout(timer)
      element.removeEventListener("wheel", wheel)
    }
  }, [])

  // A drag that leaves the track would select text across the page: selection stays off until the pointer is released anywhere.
  const grab = (event: React.PointerEvent<HTMLSpanElement>): void => {
    onPointerDown?.(event)
    if (disabled || event.button !== 0) return

    window.getSelection()?.removeAllRanges()
    const release = holdSelection(document.documentElement.style)
    const end = (): void => {
      release()
      window.removeEventListener("pointerup", end)
      window.removeEventListener("pointercancel", end)
    }

    window.addEventListener("pointerup", end)
    window.addEventListener("pointercancel", end)
  }

  const _values = React.useMemo(
    () =>
      Array.isArray(value)
        ? value
        : Array.isArray(defaultValue)
          ? defaultValue
          : [min, max],
    [value, defaultValue, min, max]
  )

  return (
    <SliderPrimitive.Root
      ref={root}
      data-slot="slider"
      defaultValue={defaultValue}
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onValueChange={onValueChange}
      onValueCommit={onValueCommit}
      onPointerDown={grab}
      className={cn(
        "relative flex w-full touch-none items-center select-none data-[disabled]:opacity-50 data-[orientation=vertical]:h-full data-[orientation=vertical]:min-h-44 data-[orientation=vertical]:w-auto data-[orientation=vertical]:flex-col",
        className
      )}
      {...props}
    >
      <SliderPrimitive.Track
        data-slot="slider-track"
        className={cn(
          "relative grow overflow-hidden rounded-full bg-muted data-[orientation=horizontal]:h-1.5 data-[orientation=horizontal]:w-full data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5"
        )}
      >
        <SliderPrimitive.Range
          data-slot="slider-range"
          className={cn(
            "absolute bg-primary data-[orientation=horizontal]:h-full data-[orientation=vertical]:w-full"
          )}
        />
      </SliderPrimitive.Track>
      {Array.from({ length: _values.length }, (_, index) => (
        <SliderPrimitive.Thumb
          data-slot="slider-thumb"
          key={index}
          aria-label={label === undefined ? undefined : _values.length === 2 ? `${label} ${index === 0 ? "minimum" : "maximum"}` : label}
          className="block size-4 shrink-0 rounded-full border border-primary bg-foreground shadow-sm ring-ring/50 transition-[color,box-shadow] hover:ring-4 focus-visible:ring-4 focus-visible:outline-hidden disabled:pointer-events-none disabled:opacity-50"
        />
      ))}
    </SliderPrimitive.Root>
  )
}

export { Slider }
