"""Conditions and frame facts as read-only fields: each value in the control that would author it."""

import re
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Literal, cast, get_args, get_origin

from femforge.common.utils import title
from femforge.common.vector import Vec3
from femforge.physical.conditions import Condition
from femforge.physical.readout import along, readout
from femforge.physical.units import Quantity, U, quantity
from femforge_workbench.widgets.fields import ConditionCard, FieldView
from femforge_workbench.widgets.icon import IconName


AXES = ("X", "Y", "Z")
"""The authored axes a vector's components run along."""

COMPONENTS = ("x", "y", "z")
"""The field names a condition gives its per-axis values."""

SKIPPED = frozenset({"kind", "physics", "target"})
"""The fields every condition carries that say where it applies, not what it sets."""

VECTOR = re.compile(r"^\((?P<parts>[^)]*)\)(?:\s+(?P<unit>\S+))?$")
QUANTITY = re.compile(r"^(?P<number>[-+]?\d[\d,.]*(?:e[-+]?\d+)?)\s+(?P<unit>\S+)$")


def split(text: str) -> tuple[str, str]:
    """A readout's number and unit, such as ``("200", "mm²")``."""
    number, _, unit = text.partition(" ")

    return number, unit


def components(text: str) -> list[str | None]:
    """The components of a readout such as ``(0.6, 0.8, 0)``."""
    match = VECTOR.match(text)
    parts = match.group("parts") if match is not None else text

    return [part.strip() for part in parts.split(",")]


def is_choice(annotation: object) -> bool:
    """Whether a field annotation is a ``Literal`` of strings, optionally with None, so its value reads as a choice rather than free text."""
    options: list[object] = [option for option in cast("tuple[object, ...]", get_args(annotation)) if option is not type(None)]
    if get_origin(annotation) is not Literal:
        return len(options) == 1 and is_choice(options[0])

    return all(isinstance(option, str) for option in options)


def views(label: str, value: object, *, choice: bool = False) -> list[FieldView]:
    """The fields one authored value shows as: a vector quantity as magnitude and direction, a quantity as number and unit, a vector by component, a choice or text; none for an unset value.

    Args:
        label: The field's label.
        value: The authored value.
        choice: Whether a string value is one of a fixed set of options, which reads title-cased like an enum.
    """
    if value is None:
        return []

    if isinstance(value, U.Quantity):
        measured = cast("Quantity", value)
        magnitude: object = measured.magnitude
        if isinstance(magnitude, Vec3):
            number, unit = split(readout(quantity(magnitude.magnitude(), str(measured.units))))
            magnitude_view: FieldView = {"label": "Magnitude", "kind": "quantity", "value": number, "unit": unit}
            direction: FieldView = {"label": "Direction", "kind": "vector", "values": components(along(magnitude)), "axes": list(AXES)}

            return [magnitude_view, direction]

        number, unit = split(readout(measured))

        return [{"label": label, "kind": "quantity", "value": number, "unit": unit}]

    if isinstance(value, Vec3):
        return [{"label": label, "kind": "vector", "values": [f"{component:.3g}" for component in value.to_tuple()], "axes": list(AXES)}]

    if isinstance(value, bool):
        return [{"label": label, "kind": "choice", "value": "On" if value else "Off"}]

    if isinstance(value, Enum):
        chosen: object = cast("object", cast("Enum", value).value)

        return [{"label": label, "kind": "choice", "value": title(str(chosen))}]

    if isinstance(value, int | float):
        return [{"label": label, "kind": "text", "value": f"{value:.4g}"}]

    if isinstance(value, str) and choice:
        return [{"label": label, "kind": "choice", "value": title(value)}]

    if isinstance(value, str):
        return [{"label": label, "kind": "text", "value": value}]

    return []


def per_axis(condition: Condition, label: str, dropped: Sequence[str], tip: str) -> FieldView:
    """A condition's ``x``, ``y`` and ``z`` values as one vector in millimetres, an unset axis free and an axis the frame drops muted.

    Args:
        condition: The authored condition.
        label: The vector's label, such as ``Displacement``.
        dropped: The authored components the solve frame drops.
        tip: Why the frame drops them.
    """
    values: list[str | None] = []
    for name in COMPONENTS:
        value: object = getattr(condition, name, None)
        values.append(split(readout(cast("Quantity", value), unit=U.mm))[0] if isinstance(value, U.Quantity) else None)

    view: FieldView = {"label": label, "kind": "vector", "values": values, "axes": list(AXES), "unit": "mm"}
    muted = [index for index, name in enumerate(COMPONENTS) if name in dropped]
    if muted:
        view["dropped"] = muted
        view["tip"] = tip

    return view


def condition_card(condition: Condition, *, icon: IconName | None, dropped: Sequence[str] = (), dropped_tip: Callable[[str], str] = lambda _: "") -> ConditionCard:
    """One condition as its read-only editor: its type, then each authored field in order.

    Args:
        condition: The authored condition.
        icon: The condition's glyph icon, None for one the scene draws no glyph for.
        dropped: The authored components the solve frame drops, such as ``z`` in a 2D frame.
        dropped_tip: Why the frame drops a component, by its name.
    """
    fields: list[FieldView] = []
    model_fields = type(condition).model_fields
    names = [name for name in model_fields if name not in SKIPPED]
    kind = str(getattr(condition, "kind", ""))
    for name in names:
        if name in COMPONENTS:
            if name == next(component for component in names if component in COMPONENTS):
                fields.append(per_axis(condition, title(kind.rsplit("_", 1)[-1]), dropped, dropped_tip(next(iter(dropped), ""))))

            continue

        found = views(title(name), cast("object", getattr(condition, name)), choice=is_choice(model_fields[name].annotation))
        for view in found:
            if view["kind"] == "vector" and view["label"] == "Direction" and dropped:
                view["dropped"] = [index for index, axis in enumerate(COMPONENTS) if axis in dropped]
                view["tip"] = dropped_tip(dropped[0])

        fields.extend(found)

    card: ConditionCard = {"title": title(kind), "fields": fields}
    if icon is not None:
        card["icon"] = icon

    return card


def automatic_card(kind: str, note: str) -> ConditionCard:
    """A condition femforge set itself, which has no authored fields to show."""
    return {"title": title(kind), "fields": [{"label": "Source", "kind": "text", "value": note}]}


def fact_field(label: str, value: str, tip: str = "") -> FieldView:
    """A frame fact as a read-only field: ``(0, 0, 1)`` by component, ``10 mm`` as a number and unit, anything else as text.

    Args:
        label: The field's label, such as ``Plane State``.
        value: The fact's value as the frame reads it.
        tip: What the value means; empty for none.
    """
    view: FieldView = {"label": label, "kind": "text", "value": value}
    vector = VECTOR.match(value)
    measured = QUANTITY.match(value)
    if vector is not None:
        view = {"label": label, "kind": "vector", "values": components(value), "axes": list(AXES)}
        if vector.group("unit"):
            view["unit"] = vector.group("unit")

    elif measured is not None:
        view = {"label": label, "kind": "quantity", "value": measured.group("number"), "unit": measured.group("unit")}

    if tip:
        view["tip"] = tip

    return view
