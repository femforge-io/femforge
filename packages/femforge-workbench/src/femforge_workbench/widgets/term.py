"""The terminal: an xterm fed from the server."""

from collections.abc import Mapping
from typing import Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, next_ref


KIND = "term"


class Term(Element):
    """An xterm whose `options` are read at mount; `on_data` carries typed text, `on_resize` the new size."""

    def __init__(self, *, options: Prop[Mapping[str, object]] = None, on_data: Event = None, on_resize: Event = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the terminal; a prop left None keeps the component's default.

        Args:
            options: xterm options, read once at mount.
            on_data: Receives the text the user types.
            on_resize: Receives the terminal's new size in columns and rows.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        kwargs.setdefault("ref", next_ref(KIND))
        super().__init__("ff-term", locals(), None, **kwargs)

    def write(self, text: str) -> None:
        """Writes `text` to the terminal."""
        self.call("write", text)

    def clear(self) -> None:
        """Clears the terminal."""
        self.call("clear")

    def fit(self) -> None:
        """Fits the grid to the host's size."""
        self.call("fit")

    def scroll_to_bottom(self) -> None:
        """Scrolls to the newest line."""
        self.call("scrollToBottom")
