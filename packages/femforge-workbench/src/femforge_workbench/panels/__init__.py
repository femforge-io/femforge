"""Panel components: one trame template per dock panel, built inside its DivLayout with state namespaced by panel id."""

import json
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

from trame.app import TrameComponent
from trame.ui.html import DivLayout
from trame.widgets import html, react
from trame_server.state import State

from femforge.common.utils import title
from femforge_workbench.dock import template_name
from femforge_workbench.widgets.element import EVENT, Shared as SharedProps
from femforge_workbench.widgets.field import Field, FieldLabel
from femforge_workbench.widgets.select import Select, SelectContent, SelectItem, SelectTrigger, SelectValue
from femforge_workbench.widgets.switch import Switch
from femforge_workbench.widgets.toggle import ToggleGroup, ToggleGroupItem
from femforge_workbench.widgets.tooltip import TooltipProvider


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


type Listener = Callable[..., object]
"""A state listener, called with the changed state as keyword arguments."""

type Handler = Callable[..., object]
"""A client event handler, called with the values its JS arguments evaluate to."""

FILL = {"height": "100%", "width": "100%", "display": "flex", "flexDirection": "column", "minHeight": "0"}
"""The style of a panel's root: a column filling the dock body."""

TRIGGER_SIZE = "sm"
TRIGGER_WIDTH = "w-36"
"""The default width of a toolbar select; a picker over long ids widens it."""

ITEM = "item"
"""The loop variable a picker's items are named by."""

SETTING = "flex h-6 w-full shrink-0 items-center justify-between gap-2"
READOUTS = "flex items-center justify-between"
SWITCH_ID = "ff-switch-"


class Cls(StrEnum):
    """Tailwind compositions the panels share; the strings appear literally so the stylesheet build scans them."""

    TOOLBAR = "flex shrink-0 items-center gap-1 border-b border-border px-1.5 text-label h-(--ff-tab-height)"
    TOGGLE = "h-6 px-1.5 text-label"
    TOOLBAR_DIVIDER = "mx-1 h-4 w-px shrink-0 bg-border"
    FILL = "relative min-h-0 flex-1"
    SCROLL = "overflow-auto"
    CAPTION = "text-label text-muted-foreground"
    SPACER = "grow"
    STATUS = "ml-auto truncate text-label text-muted-foreground"
    EMPTY = "flex flex-1 items-center justify-center text-label text-muted-foreground"
    MONO = "font-mono"
    GROUP_TITLE = "px-2 pt-2 text-label font-medium text-muted-foreground"
    CARDS = "grid gap-2 p-2 grid-cols-[repeat(auto-fill,minmax(16rem,1fr))]"
    CARD = "gap-2 py-3"
    CARD_SECTION = "px-3"
    CARD_TITLE = "flex items-center gap-2 text-body"
    CARD_META = "flex gap-3 px-3 text-label text-muted-foreground"
    CARD_ACTIONS = "gap-1 px-3"
    FILTERS = "flex h-auto shrink-0 flex-wrap items-center gap-1 border-b border-border px-1.5 py-1 text-label"
    COLUMN = "flex min-h-0 flex-1 flex-col gap-1"
    STRIP = "flex shrink-0 flex-wrap items-center gap-1.5 px-2 py-1 text-label"
    PANE_ROW = "flex h-44 shrink-0 border-t border-border"
    PANE = "flex min-w-0 flex-1 flex-col overflow-hidden"
    SECTION = "flex items-center gap-2 px-2 pt-2 font-medium"
    PAIRS = "grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-0.5 px-2 text-label"
    CHIPS = "flex flex-wrap items-center gap-1 px-2"
    TABLE_BOX = "mx-2 h-56 shrink-0 overflow-hidden rounded-md border border-border"
    TIMELINE = "ml-3.5 flex flex-col gap-1 border-l border-border py-1 pl-3 text-label"
    TIMELINE_ITEM = "flex items-center gap-2"
    LIST = "flex min-h-0 flex-1 flex-col overflow-auto text-label"
    LIST_ITEM = "cursor-pointer px-2 py-1 hover:bg-wash-hover"
    FRAME = "h-full w-full border-0 bg-background"
    IMAGE = "m-auto max-h-full max-w-full object-contain"
    RULES = "grid grid-cols-[1fr_auto_auto] items-center gap-2"
    CONTROLS = "flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden text-label"
    CONTROL = "w-full shrink-0 gap-1"
    SECTIONS = "flex w-full shrink-0 flex-col gap-2"
    SECTION_ITEM = "border-y border-border first:border-t-0"
    SECTION_TRIGGER = "items-center rounded-none bg-card px-2 py-2 text-label font-semibold hover:bg-wash-hover hover:no-underline focus-visible:ring-inset data-[state=open]:border-b"
    SECTION_BODY = "flex flex-col gap-3 px-2 pt-3 pb-4"
    SECTION_BODY_STACK = "flex flex-col gap-3"
    SEGMENTED = "grid w-full grid-cols-3"
    SEGMENT = "h-6 px-1 text-label"


class Shared(StrEnum):
    """The state keys panels share: a selection made in one panel is read by the others."""

    SELECTED_RUN_ID = "selected_run_id"
    SELECTED_ARTIFACT_PATH = "selected_artifact_path"
    SELECTED_WORKBENCH = "selected_workbench"


class ItemField(StrEnum):
    """The keys of a picker item: the label shown, the value chosen and, optionally, whether it is offered but unavailable, a state word and the muted parts after its label."""

    TITLE = "title"
    VALUE = "value"
    DISABLED = "disabled"
    FLAG = "flag"
    DETAIL = "detail"


class Carry(StrEnum):
    """What a disposed panel leaves in `legacy` for the panel rebuilt in its place."""

    CAMERA = "camera"
    SHOWN = "shown"
    MODE = "mode"


def captioned(label: str) -> Field:
    """A caption over the control built inside the returned field: the controls column is too narrow for one beside it."""
    field = Field(orientation="vertical", classes=str(Cls.CONTROL))
    with field:
        FieldLabel(title(label), classes=str(Cls.CAPTION))

    return field


def setting(model: StrEnum, label: str, *, disabled: react.Bind | None = None) -> None:
    """A switch over `model` at the right of its label, every switch of the controls in one column.

    Args:
        model: The state variable the switch is bound to.
        label: The caption at the left.
        disabled: A client expression that disables the switch; None keeps it enabled.
    """
    caption = str(Cls.CAPTION)
    dimmed = react.Bind(f"({disabled.js_expression}) ? {json.dumps(f'{caption} opacity-50')} : {json.dumps(caption)}") if disabled is not None else caption
    with Field(orientation="horizontal", classes=SETTING):
        FieldLabel(title(label), html_for=f"{SWITCH_ID}{model}", classes=dimmed)
        Switch(model=str(model), id=f"{SWITCH_ID}{model}", disabled=disabled, aria_disabled=disabled)


TWO_SEGMENTS = "grid-cols-2"
"""The track of a two-way choice, which overrides the three columns `Cls.SEGMENTED` lays out."""

FOUR_SEGMENTS = "grid-cols-4"
"""The track of a four-way choice."""


def segmented(model: StrEnum, label: str, values: Sequence[str], *, read: Callable[[str], str] = title) -> None:
    """A captioned single choice among `values` as one row of equal segments; pressing the chosen segment again changes nothing.

    Args:
        model: The state variable the choice is bound to.
        label: The caption over the segments.
        values: The values offered, in order.
        read: Makes the text each value reads as; Title Case by default.
    """
    with captioned(label):
        segments(model, label, values, read=read)


def segments(model: StrEnum, label: str, values: Sequence[str], *, read: Callable[[str], str] = title, disabled: react.Bind | None = None) -> None:
    """A single choice among `values` as one row of equal segments, under a heading that names it; pressing the chosen segment again changes nothing.

    Args:
        model: The state variable the choice is bound to.
        label: The choice's accessible name.
        values: The values offered, in order.
        read: Makes the text each value reads as; Title Case by default.
        disabled: A client expression that disables every segment; None keeps them enabled.
    """
    keep = react.Callback(f"{EVENT} && trame.state.set({json.dumps(str(model))}, {EVENT})")
    columns = {2: TWO_SEGMENTS, 4: FOUR_SEGMENTS}.get(len(values), "")
    track = f"{Cls.SEGMENTED} {columns}".strip()
    with ToggleGroup(type_="single", variant="outline", size="sm", value=react.Bind(str(model)), on_value_change=keep, disabled=disabled, classes=track, aria_label=title(label)):
        for value in values:
            ToggleGroupItem(read(value), value=value, classes=str(Cls.SEGMENT))


def readouts(left: str | react.Bind, right: str | react.Bind) -> None:
    """The row over a slider: a caption or its low end at the left, its high end at the right."""
    with html.Div(classes=READOUTS):
        html.Span([title(left) if isinstance(left, str) else left], classes=str(Cls.CAPTION))
        html.Span([right], classes=str(Cls.CAPTION))


def labelled(label: str) -> Field:
    """A caption beside the control built inside the returned field: a toolbar row has no room for a floating label."""
    field = Field(orientation="horizontal", classes="w-auto gap-1")
    with field:
        # A crowded app bar would otherwise break a two-word caption over two lines and grow the bar.
        FieldLabel(title(label), classes=f"{Cls.CAPTION} whitespace-nowrap")

    return field


def chooser(model: str, values: Sequence[str], *, width: str = TRIGGER_WIDTH, disabled: bool = False, label: Callable[[str], str] = title) -> None:
    """A single-choice select over fixed values, bound to a state variable.

    Args:
        model: The state variable the choice is bound to.
        values: The values offered, in order.
        width: The trigger's Tailwind width class.
        disabled: Whether the select is disabled.
        label: Makes the text each value reads as; Title Case by default.
    """
    with Select(model=model, disabled=disabled):
        with SelectTrigger(size=TRIGGER_SIZE, classes=width):
            SelectValue()

        with SelectContent():
            for value in values:
                SelectItem(label(value), value=value)


def picker(
    model: str,
    items: str,
    *,
    placeholder: str | react.Bind = "",
    width: str = TRIGGER_WIDTH,
    disabled: bool | react.Bind = False,
    label: str | None = None,
    menu: str = "",
    on_open_change: react.Callback | None = None,
) -> None:
    """A single-choice select over the `{title, value}` items a state variable holds, bound to another; an item's `flag` and `detail` parts read after its title.

    Args:
        model: The state variable the choice is bound to.
        items: The state variable holding the items.
        placeholder: The text shown while nothing is chosen.
        width: The trigger's Tailwind width class.
        disabled: Whether the select is disabled.
        label: The trigger's accessible name; None leaves it to the chosen title.
        menu: Tailwind classes added to the dropdown's own.
        on_open_change: Receives whether the dropdown opened.
    """
    content: SharedProps = {"classes": menu} if menu else {}
    with Select(model=model, disabled=disabled, on_open_change=on_open_change):
        with SelectTrigger(size=TRIGGER_SIZE, classes=width, aria_label=label):
            SelectValue(placeholder=placeholder)

        with SelectContent(**content), react.For(items=items, name=ITEM):
            SelectItem(
                [react.Bind(f"{ITEM}.{ItemField.TITLE}")],
                value=react.Bind(f"{ITEM}.{ItemField.VALUE}"),
                disabled=react.Bind(f"{ITEM}.{ItemField.DISABLED}"),
                flag=react.Bind(f"{ITEM}.{ItemField.FLAG}"),
                detail=react.Bind(f"{ITEM}.{ItemField.DETAIL}"),
            )


def select_run(state: State, run_id: str) -> None:
    """Publishes `run_id` as the shared selection; picking the selected run again still notifies every listener, so each one re-reads the run."""
    with state:
        state[Shared.SELECTED_RUN_ID] = run_id
        state.dirty(Shared.SELECTED_RUN_ID)


class PanelComponent(TrameComponent):
    """A dock panel: its template is built once inside `layout`; state variables go through `key`."""

    def __init__(self, app: "Workbench", id: str, title: str) -> None:
        """Builds the panel's template inside its own layout.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
        """
        self.listeners: list[Listener] = []
        """The state listeners registered through `listen`, dropped again by `dispose`."""
        self.bound: list[Handler] = []
        """The callables handed to `callback`, whose triggers `dispose` unregisters."""
        self.legacy: dict[Carry, object] = {}
        """What `dispose` leaves for a successor; read by its `succeed`, never through the reloadable classes."""

        super().__init__(app.server)
        self.app = app
        self.id = id
        self.title = title
        self.template_name = template_name(id)
        self.layout = DivLayout(app.server, template_name=self.template_name, style=FILL)

        with self.layout, TooltipProvider():
            self.build()

    def build(self) -> None:
        """Adds the panel's widgets to its layout."""
        raise NotImplementedError

    def key(self, name: str) -> str:
        """A state variable or trigger name namespaced to this panel."""
        return f"{self.id}_{name}".replace("-", "_")

    def status(self, message: str) -> None:
        """Reports in the shell's status line."""
        self.app.status(message)

    def toast(self, text: str) -> None:
        """Shows a short message in the shell's bottom-right corner."""
        self.app.toast(text)

    def download(self, name: str, data: bytes, media_type: str) -> None:
        """Hands the browser a file to save, through the shell.

        Args:
            name: The file name the browser suggests.
            data: The file's bytes.
            media_type: The file's media type, such as ``image/png``.
        """
        self.app.download(name, data, media_type)

    def refresh(self) -> None:
        """Pushes the template again after its widgets changed."""
        self.layout.flush_content()

    def callback(self, fn: Handler, args: str | None = None) -> react.Callback:
        """A client event handler calling `fn` with the JS `args`, unregistered again when the panel is disposed."""
        self.bound.append(fn)

        return react.Callback(fn, args=args)

    def optimistic(self, fn: Handler, model: str, value: str, args: str) -> react.Callback:
        """A client event handler that writes the JS `value` into the state variable `model` at once, then calls `fn` with the JS `args` on the server.

        Args:
            fn: The server handler, which writes the same value and so changes nothing the control shows.
            model: The state variable the control binds.
            value: The JS expression the control's new state evaluates to.
            args: The JS expression of `fn`'s argument list, evaluated after the write, so the model already holds the new value.
        """
        self.bound.append(fn)
        name = json.dumps(self.server.trigger_name(fn))

        return react.Callback(f"trame.state.set({json.dumps(model)}, {value}), trame.trigger({name}, {args})")

    def listen[L: Listener](self, *names: str) -> Callable[[L], L]:
        """Registers a state listener like `state.change`, one the panel drops again when disposed."""

        def register(listener: L) -> L:
            _ = self.state.change(*names)(listener)
            self.listeners.append(listener)

            return listener

        return register

    def dispose(self) -> None:
        """Releases what the panel registered on the server once it left the dock: triggers, state listeners and controller slots."""
        self._unbind_annotated_methods()

        # trame offers no public removal of a listener, nor a list of the triggers it named for a widget's callbacks; its own unbind reaches into the same dicts.
        for registered in self.state._change_callbacks.values():
            registered[:] = [(fn, translator) for fn, translator in registered if fn not in self.listeners]

        owned = [fn for fn in self.ctrl._triggers_fn2name if getattr(fn, "__self__", None) is self or fn in self.bound]
        for fn in owned:
            _ = self.ctrl.trigger_unregister(fn)

        self.listeners.clear()
        self.bound.clear()

    def succeed(self, previous: "PanelComponent") -> None:
        """Called on a panel rebuilt under the id of `previous`, already disposed, to carry over what a fresh build would lose."""

    def on_visible(self, visible: bool) -> None:
        """Called when dockview shows or hides the panel."""

    def restyle(self) -> None:
        """Called after the render settings changed, for a panel that draws with them to redraw."""

    def on_layout_changed(self) -> None:
        """Called after the dock re-laid out its groups."""
