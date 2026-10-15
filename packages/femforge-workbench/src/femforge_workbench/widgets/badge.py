"""The badge."""

from typing import Literal, Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


type BadgeVariant = Literal["default", "destructive", "ghost", "link", "outline", "secondary"]


class Badge(Element):
    """A small status label."""

    def __init__(self, children: object = None, *, variant: Prop[BadgeVariant] = None, as_child: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the badge; a prop left None keeps the component's default.

        Args:
            children: The badge's text.
            variant: The badge's style.
            as_child: Renders the child element itself, styled as the badge.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-badge", locals(), children, **kwargs)
