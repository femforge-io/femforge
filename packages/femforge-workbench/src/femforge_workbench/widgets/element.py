"""The Element base: a wrapper's declared parameters become React props by name, those named on_* events."""

import itertools
import json
from collections.abc import Mapping
from typing import Literal, TypedDict, Unpack

from trame.widgets import react
from trame_client.widgets.core import AbstractElement
from trame_client.widgets.react import SHARED_EVENTS, SHARED_PROPS

from femforge_workbench.widgets.module import descriptor


RESERVED = frozenset({"self", "children", "kwargs", "__class__"})
"""Names of a wrapper's locals that are never props."""

MODEL = "model"
"""The Python-only parameter of the control wrappers: the state variable the control binds and writes client-side."""

RENAMES: Mapping[str, str] = {"aria_label": "aria-label", "aria_disabled": "aria-disabled", "aria_pressed": "aria-pressed"}
"""Parameters whose React prop is not their camelCase."""

EVENT_PREFIX = "on_"
REF_PREFIX = "ff"
EVENT = "$event"

SHARED = frozenset(name if isinstance(name, str) else name[0] for name in (*SHARED_PROPS, *SHARED_EVENTS))
"""The props and events trame registers on every React element."""

REFS = itertools.count(1)

type Prop[T] = T | react.Bind | None
"""A prop: a static value, a state binding, or unset."""

type Event = react.Callback | None
"""An event handler, or unset."""

type Orientation = Literal["horizontal", "vertical"]
type Side = Literal["bottom", "left", "right", "top"]
type Align = Literal["center", "end", "start"]


class Shared(TypedDict, total=False):
    """The trame-shared props and events a wrapper accepts beside its own."""

    classes: str | react.Bind
    id: str
    style: Mapping[str, object]
    title: str | react.Bind
    hidden: bool | react.Bind
    tabIndex: int
    ref: str
    key: str | react.Bind
    connect_parent: bool
    on_click: react.Callback
    on_double_click: react.Callback
    on_context_menu: react.Callback
    on_mouse_down: react.Callback
    on_mouse_up: react.Callback
    on_mouse_enter: react.Callback
    on_mouse_leave: react.Callback
    on_mouse_move: react.Callback
    on_key_down: react.Callback
    on_key_up: react.Callback
    on_submit: react.Callback
    on_input: react.Callback
    on_change: react.Callback
    on_focus: react.Callback
    on_blur: react.Callback


def react_name(name: str, rename: Mapping[str, str] = RENAMES) -> str:
    """The React prop of a parameter: one trailing underscore stripped, then the rename map, else camelCase."""
    stripped = name.removesuffix("_")
    if stripped in rename:
        return rename[stripped]

    head, *tail = stripped.split("_")

    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def is_event(name: str) -> bool:
    """Whether a parameter is an event handler: its name starts with `on_`."""
    return name.startswith(EVENT_PREFIX)


def next_ref(kind: str) -> str:
    """A name for trame.refs, unique for the process."""
    return f"{REF_PREFIX}_{kind}_{next(REFS)}"


def setter(model: str, value: str = EVENT) -> react.Callback:
    """A client-side handler writing the event payload into the state variable."""
    return react.Callback(f"trame.state.set({json.dumps(model)}, {value})")


class Element(AbstractElement):
    """A component of the Ff library: its wrapper's parameters are its props and events, trame's shared names pass through."""

    def __init__(self, tag: str, params: Mapping[str, object], children: object = None, *, rename: Mapping[str, str] = RENAMES, **kwargs: Unpack[Shared]) -> None:
        """Declares the wrapper's parameters as props and events and enables the Ff module on the server.

        Args:
            tag: The element's custom tag, such as `ff-button`.
            params: The wrapper's locals; each declared parameter becomes a prop, or an event when named `on_*`.
            children: The content nested inside the element.
            rename: Parameters whose React prop is not their camelCase.
            kwargs: Shared trame props and events, passed through as they are.
        """
        declared = {name: value for name, value in params.items() if name not in RESERVED and name != MODEL}
        fresh = [name for name in declared if name not in SHARED]
        props = [(name, react_name(name, rename)) for name in fresh if not is_event(name)]
        events = [(name, react_name(name, rename)) for name in fresh if is_event(name)]

        super().__init__(tag, children, **declared, **kwargs)

        self._attr_names.extend(props)
        self._event_names.extend(events)
        _ = self.server.enable_module(descriptor())

    @property
    def reference(self) -> str:
        """The name the client registers this element under on trame.refs; KeyError when it has none."""
        return str(self["ref"])

    def call(self, method: str, *args: object) -> None:
        """Calls a method of the mounted component through its ref."""
        self.server.js_call(self.reference, method, *args)
