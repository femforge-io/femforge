"""Fields: a labelled control with its description and error."""

from typing import Literal, Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


type FieldOrientation = Literal["horizontal", "responsive", "vertical"]
type FieldLegendVariant = Literal["label", "legend"]


class Field(Element):
    """A control with its label, laid out along `orientation`."""

    def __init__(self, children: object = None, *, orientation: Prop[FieldOrientation] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the field; a prop left None keeps the component's default.

        Args:
            children: The field's label and control.
            orientation: The direction the label and the control lay out along.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-field", locals(), children, **kwargs)


class FieldLabel(Element):
    """The label of a field."""

    def __init__(self, children: object = None, *, html_for: Prop[str] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the label; a prop left None keeps the component's default.

        Args:
            children: The label's text.
            html_for: The id of the control the label names.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-field-label", locals(), children, **kwargs)


class FieldContent(Element):
    """The column holding a field's control and description."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the column around `children`."""
        super().__init__("ff-field-content", locals(), children, **kwargs)


class FieldDescription(Element):
    """Help text under a field."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the help text from `children`."""
        super().__init__("ff-field-description", locals(), children, **kwargs)


class FieldError(Element):
    """The error text of a field, or the `errors` it lists."""

    def __init__(self, children: object = None, *, errors: Prop[list[dict[str, str]]] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the error; a prop left None keeps the component's default.

        Args:
            children: The error's text.
            errors: Errors listed by their `message` when no children are given.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-field-error", locals(), children, **kwargs)


class FieldGroup(Element):
    """A stack of fields."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the stack around `children`."""
        super().__init__("ff-field-group", locals(), children, **kwargs)


class FieldSet(Element):
    """A fieldset of related fields."""

    def __init__(self, children: object = None, *, disabled: Prop[bool] = None, name: Prop[str] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the fieldset; a prop left None keeps the component's default.

        Args:
            children: The fieldset's legend and fields.
            disabled: Disables every control in the fieldset.
            name: The fieldset's form name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-field-set", locals(), children, **kwargs)


class FieldLegend(Element):
    """The legend of a fieldset."""

    def __init__(self, children: object = None, *, variant: Prop[FieldLegendVariant] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the legend; a prop left None keeps the component's default.

        Args:
            children: The legend's text.
            variant: Whether the legend reads as a legend or as a label.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-field-legend", locals(), children, **kwargs)


class FieldSeparator(Element):
    """A rule between fields, with optional text."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the rule, with `children` as its text."""
        super().__init__("ff-field-separator", locals(), children, **kwargs)


class FieldTitle(Element):
    """A title styled like a label without being one."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the title from `children`."""
        super().__init__("ff-field-title", locals(), children, **kwargs)
