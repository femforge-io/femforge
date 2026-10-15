"""The code editor: Monaco over a text value."""

from collections.abc import Mapping
from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Code(Element):
    """An editor over `value` in `language`; `on_change` (shared) carries the edited text."""

    def __init__(
        self,
        *,
        value: Prop[str] = None,
        language: Prop[str] = None,
        theme: Prop[str] = None,
        options: Prop[Mapping[str, object]] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the editor; a prop left None keeps the component's default.

        Args:
            value: The text to edit.
            language: The Monaco language that highlights the text.
            theme: The Monaco theme.
            options: Monaco editor options.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-code", locals(), None, **kwargs)
