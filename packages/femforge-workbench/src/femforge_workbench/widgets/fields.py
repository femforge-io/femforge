"""Read-only fields: an authored value shown in the control that would edit it."""

from collections.abc import Sequence
from typing import Literal, NotRequired, TypedDict, Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared
from femforge_workbench.widgets.icon import IconName


type FieldKind = Literal["quantity", "vector", "text", "choice"]


class FieldView(TypedDict):
    """One labelled read-only field."""

    label: str
    """The field's label."""

    kind: FieldKind
    """How the value shows: a number with its unit, one cell per component, plain text, or a select's choice."""

    value: NotRequired[str]
    """The value of a quantity, text or choice field."""

    values: NotRequired[list[str | None]]
    """Each component of a vector field; None for a free one."""

    axes: NotRequired[list[str]]
    """The label of each component of a vector field."""

    unit: NotRequired[str]
    """The unit after the value or the last component."""

    dropped: NotRequired[list[int]]
    """The components of a vector the solve frame drops, shown muted."""

    tip: NotRequired[str]
    """What the screen does not show about the field, such as why a component is muted."""


class ConditionCard(TypedDict):
    """One condition as the read-only editor its authoring form would be."""

    title: str
    """The condition type, such as ``Surface Force``."""

    icon: NotRequired[IconName]
    """The condition's glyph icon; absent for a condition the scene draws no glyph for."""

    fields: list[FieldView]
    """The condition's fields in authoring order."""

    muted: NotRequired[bool]
    """Whether the condition never reached the solver."""

    tip: NotRequired[str]
    """Why a muted condition never reached the solver."""


class Fields(Element):
    """Labelled read-only fields in two aligned columns."""

    def __init__(self, *, fields: Prop[Sequence[FieldView]] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the fields; a prop left None keeps the component's default.

        Args:
            fields: The fields in order.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-fields", locals(), None, **kwargs)
