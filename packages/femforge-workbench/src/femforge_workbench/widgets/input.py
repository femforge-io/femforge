"""Text inputs: uncontrolled, committing to the state on blur and Enter, named after the state key they bind."""

import json
from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import EVENT, Element, Prop, Shared, setter


type AddonAlign = Literal["block-end", "block-start", "inline-end", "inline-start"]

COMMIT_VALUE = f"{EVENT}.target.value"
ENTER = "Enter"


def committing(model: str) -> react.Callback:
    """A key handler writing the input text into the state variable on Enter."""
    return react.Callback(f"{EVENT}.key === {json.dumps(ENTER)} && trame.state.set({json.dumps(model)}, {COMMIT_VALUE})")


class Input(Element):
    """A text input seeded from `model` and writing it back on blur and Enter; a controlled value would drop keystrokes on the round trip."""

    def __init__(
        self,
        *,
        model: str | None = None,
        default_value: Prop[str] = None,
        placeholder: Prop[str] = None,
        type_: Prop[str] = None,
        name: str | None = None,
        disabled: Prop[bool] = None,
        read_only: Prop[bool] = None,
        min_: Prop[float] = None,
        max_: Prop[float] = None,
        step: Prop[float] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the input; a prop left None keeps the component's default.

        Args:
            model: A state variable that seeds the text and takes it back on blur and Enter; it also names the input.
            default_value: The text at mount.
            placeholder: The text shown while the input is empty.
            type_: The HTML input type.
            name: The form field name; `model` by default.
            disabled: Disables the input.
            read_only: Makes the text read-only.
            min_: The smallest value of a number input.
            max_: The largest value of a number input.
            step: The step of a number input.
            aria_label: The accessible name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None:
            default_value = react.Bind(model)
            name = name or model
            kwargs.setdefault("on_blur", setter(model, COMMIT_VALUE))
            kwargs.setdefault("on_key_down", committing(model))

        super().__init__("ff-input", locals(), None, **kwargs)


class Textarea(Element):
    """A multi-line input seeded from `model` and writing it back on blur."""

    def __init__(
        self,
        *,
        model: str | None = None,
        default_value: Prop[str] = None,
        placeholder: Prop[str] = None,
        rows: Prop[int] = None,
        name: str | None = None,
        disabled: Prop[bool] = None,
        read_only: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the textarea; a prop left None keeps the component's default.

        Args:
            model: A state variable that seeds the text and takes it back on blur; it also names the textarea.
            default_value: The text at mount.
            placeholder: The text shown while the textarea is empty.
            rows: The visible line count.
            name: The form field name; `model` by default.
            disabled: Disables the textarea.
            read_only: Makes the text read-only.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None:
            default_value = react.Bind(model)
            name = name or model
            kwargs.setdefault("on_blur", setter(model, COMMIT_VALUE))

        super().__init__("ff-textarea", locals(), None, **kwargs)


class InputGroup(Element):
    """An input with addons on its sides."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the group around `children`."""
        super().__init__("ff-input-group", locals(), children, **kwargs)


class InputGroupAddon(Element):
    """An addon of an input group, aligned to one side."""

    def __init__(self, children: object = None, *, align: Prop[AddonAlign] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the addon; a prop left None keeps the component's default.

        Args:
            children: The addon's text, icon or button.
            align: The side of the input the addon sits on.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-input-group-addon", locals(), children, **kwargs)


class InputGroupText(Element):
    """Text inside an input group addon."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the text from `children`."""
        super().__init__("ff-input-group-text", locals(), children, **kwargs)


class InputGroupInput(Element):
    """The input of an input group, with the same commit rule as Input."""

    def __init__(
        self,
        *,
        model: str | None = None,
        default_value: Prop[str] = None,
        placeholder: Prop[str] = None,
        type_: Prop[str] = None,
        name: str | None = None,
        disabled: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the input; a prop left None keeps the component's default.

        Args:
            model: A state variable that seeds the text and takes it back on blur and Enter; it also names the input.
            default_value: The text at mount.
            placeholder: The text shown while the input is empty.
            type_: The HTML input type.
            name: The form field name; `model` by default.
            disabled: Disables the input.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None:
            default_value = react.Bind(model)
            name = name or model
            kwargs.setdefault("on_blur", setter(model, COMMIT_VALUE))
            kwargs.setdefault("on_key_down", committing(model))

        super().__init__("ff-input-group-input", locals(), None, **kwargs)


class ColorInput(Element):
    """A native color picker writing its hex value to `model` on every change; controlled, since a pick sends whole values, not keystrokes."""

    def __init__(
        self,
        *,
        model: str | None = None,
        value: Prop[str] = None,
        disabled: Prop[bool] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the picker; a prop left None keeps the component's default.

        Args:
            model: A state variable bound to the colour and written on every change.
            value: The colour as `#rrggbb`.
            disabled: Disables the picker.
            aria_label: The accessible name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        type_ = "color"
        if model is not None:
            value = react.Bind(model)
            kwargs.setdefault("on_change", setter(model, COMMIT_VALUE))

        super().__init__("ff-input", locals(), None, **kwargs)
