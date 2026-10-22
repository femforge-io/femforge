import { Icon } from "@/components/Icon";
import { Tip } from "@/components/Tip";
import type { IconName } from "@/icons";
import { cn } from "@/lib/utils";

/** One labelled read-only field: a number with its unit, one cell per component, a choice, or text. */
export interface FieldView {
  label: string;
  kind: "quantity" | "vector" | "text" | "choice";
  value?: string;
  values?: (string | null)[];
  axes?: string[];
  unit?: string;
  dropped?: number[];
  tip?: string;
}

/** One condition as the read-only editor its authoring form would be. */
export interface ConditionCard {
  title: string;
  icon?: IconName;
  fields: FieldView[];
  muted?: boolean;
  tip?: string;
}

const BOX =
  "flex h-6 min-w-0 items-center rounded-md bg-readonly px-2 text-readonly-foreground tabular-nums cursor-default";
const UNIT = "shrink-0 pl-1 text-muted-foreground";

/** A field's value; with `unitColumn`, a vector puts its unit, or an empty slot, in the grid's unit column so the cells of every vector end in line. */
function Value({ field, unitColumn }: { field: FieldView; unitColumn: boolean }) {
  if (field.kind === "vector") {
    const values = field.values ?? [];
    const axes = field.axes ?? [];

    return (
      <>
        <span className="flex min-w-0 items-center gap-1">
          {values.map((value, index) => {
            const dropped = field.dropped?.includes(index) ?? false;

            return (
              <span key={index} className={cn(BOX, "flex-1 gap-1.5", dropped && "opacity-50")}>
                <span className="shrink-0 text-muted-foreground">{axes[index] ?? ""}</span>
                {dropped ? (
                  <span aria-label="Not applicable">—</span>
                ) : value === null ? (
                  <span className="truncate text-muted-foreground italic">Free</span>
                ) : (
                  <span className="truncate">{value}</span>
                )}
              </span>
            );
          })}
        </span>
        {unitColumn ? <span className={UNIT}>{field.unit ?? ""}</span> : null}
      </>
    );
  }

  return (
    <span className={cn(BOX, "justify-between", unitColumn && "col-span-2")}>
      <span className="truncate">{field.value ?? ""}</span>
      {field.unit ? <span className={UNIT}>{field.unit}</span> : null}
    </span>
  );
}

export interface FieldsProps {
  fields?: FieldView[];
  className?: string;
}

/** Labelled read-only fields in aligned columns, each value in the control that would author it; a vector with a unit adds a unit column every vector ends at. */
export function Fields({ fields = [], className }: FieldsProps) {
  const unitColumn = fields.some((field) => field.kind === "vector" && field.unit);

  return (
    <div
      className={cn(
        "grid items-center gap-x-2 gap-y-1 text-label",
        unitColumn
          ? "grid-cols-[minmax(4.5rem,auto)_minmax(0,1fr)_auto]"
          : "grid-cols-[minmax(4.5rem,auto)_minmax(0,1fr)]",
        className,
      )}
    >
      {fields.map((field) => (
        <div key={field.label} className="contents">
          <span className="truncate text-muted-foreground">{field.label}</span>
          <Tip content={field.tip} definition>
            <span className={cn("grid min-w-0 items-center", unitColumn && "col-span-2 grid-cols-subgrid")}>
              <Value field={field} unitColumn={unitColumn} />
            </span>
          </Tip>
        </div>
      ))}
    </div>
  );
}

/** A condition card: its type as a read-only select led by its glyph icon, then its fields. */
export function ConditionCardView({ card }: { card: ConditionCard }) {
  return (
    <Tip content={card.tip} definition>
      <div
        className={cn(
          "flex flex-col gap-1 rounded-md border border-border bg-background p-1.5",
          card.muted && "opacity-50",
        )}
      >
        <span className={cn(BOX, "gap-1.5 font-medium")}>
          {card.icon ? <Icon name={card.icon} size={14} className="shrink-0" /> : null}
          <span className="truncate">{card.title}</span>
        </span>
        {card.fields.length ? <Fields fields={card.fields} /> : null}
      </div>
    </Tip>
  );
}
