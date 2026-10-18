"""The guided tour: a spotlight and a card stepping over elements of the live app."""

from collections.abc import Sequence
from typing import NotRequired, TypedDict, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, Side, setter


class TourStep(TypedDict):
    """One stop: the CSS selector of the element to highlight, the card's title and body, and its preferred side."""

    target: str
    title: str
    body: str
    side: NotRequired[Side]


class Tour(Element):
    """A guided tour: a spotlight and a card over each step's target; `model` binds the open flag to a state variable."""

    def __init__(
        self,
        children: object = None,
        *,
        model: str | None = None,
        steps: Prop[Sequence[TourStep]] = None,
        open_: Prop[bool] = None,
        on_open_change: Event = None,
        step: Prop[int] = None,
        on_step_change: Event = None,
        on_finish: Event = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the tour; a prop left None keeps the component's default.

        Args:
            children: Unused; the tour renders only its spotlight and card.
            model: A state variable to bind the open flag to, unless `on_open_change` is given.
            steps: The stops in order; a stop whose target is not mounted is skipped.
            open_: Whether the tour is showing.
            on_open_change: Receives False when the tour closes itself.
            step: The current stop's index among the mounted ones; the tour keeps its own while unset.
            on_step_change: Receives the new stop index.
            on_finish: Receives `"done"` from the last stop's Done, or `"skipped"` from Skip Tour and Escape.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_open_change is None:
            open_ = react.Bind(model)
            on_open_change = setter(model)

        super().__init__("ff-tour", locals(), children, **kwargs)
