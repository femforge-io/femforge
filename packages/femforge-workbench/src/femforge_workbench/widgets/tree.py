"""The tree: collapsible items with leaf checkboxes."""

from collections.abc import Sequence
from typing import Literal, TypedDict, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, setter


type TreeSelectable = Literal["leaf", "none"]


class TreeItem(TypedDict, total=False):
    """One node: its id, title and children."""

    id: str
    title: str
    children: list["TreeItem"]


class Tree(Element):
    """Nested items whose leaves can be checked; `model` binds the checked ids to a state variable."""

    def __init__(
        self,
        *,
        model: str | None = None,
        items: Prop[Sequence[TreeItem]] = None,
        value: Prop[Sequence[str]] = None,
        on_value_change: Event = None,
        selectable: Prop[TreeSelectable] = None,
        open_all: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the tree; a prop left None keeps the component's default.

        Args:
            model: A state variable to bind the checked ids to, unless `on_value_change` is given.
            items: The root nodes.
            value: The ids of the checked leaves.
            on_value_change: Receives the checked ids.
            selectable: Whether the leaves carry checkboxes.
            open_all: Opens every node at mount.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-tree", locals(), None, **kwargs)
