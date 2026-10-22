"""The shell's one-shot messages to the user: a file to save and a toast."""

from typing import NotRequired, TypedDict, Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared


class DownloadFile(TypedDict):
    """A file for the browser to save: a sequence number that makes each one new, its name, media type and base64 bytes."""

    seq: int
    name: str
    type: str
    data: str


class ToastMessage(TypedDict):
    """A toast: a sequence number that makes each one new, and its text."""

    seq: int
    text: NotRequired[str]


class Download(Element):
    """Saves each new file it is handed to the browser's downloads; renders nothing."""

    def __init__(self, *, file: Prop[DownloadFile | None] = None, on_done: Event = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the saver; a prop left None keeps the component's default.

        Args:
            file: The file to save; a new `seq` saves again.
            on_done: Called once the browser has the file.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-download", locals(), None, **kwargs)


class Toast(Element):
    """A short message rising in the bottom-right corner for a few seconds."""

    def __init__(self, *, message: Prop[ToastMessage | None] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the toast; a prop left None keeps the component's default.

        Args:
            message: The message to show; a new `seq` shows it again.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-toast", locals(), None, **kwargs)
