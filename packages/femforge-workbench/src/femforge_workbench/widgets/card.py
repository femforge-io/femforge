"""Cards: a bordered box with header, content and footer."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Shared


class Card(Element):
    """A bordered box."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the card around `children`."""
        super().__init__("ff-card", locals(), children, **kwargs)


class CardHeader(Element):
    """The title row of a card."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the header around `children`."""
        super().__init__("ff-card-header", locals(), children, **kwargs)


class CardTitle(Element):
    """A card's title."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the title from `children`."""
        super().__init__("ff-card-title", locals(), children, **kwargs)


class CardDescription(Element):
    """A card's description."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the description from `children`."""
        super().__init__("ff-card-description", locals(), children, **kwargs)


class CardAction(Element):
    """A control set in the header's corner."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the corner control around `children`."""
        super().__init__("ff-card-action", locals(), children, **kwargs)


class CardContent(Element):
    """A card's body."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the body around `children`."""
        super().__init__("ff-card-content", locals(), children, **kwargs)


class CardFooter(Element):
    """A card's action row."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the action row around `children`."""
        super().__init__("ff-card-footer", locals(), children, **kwargs)
