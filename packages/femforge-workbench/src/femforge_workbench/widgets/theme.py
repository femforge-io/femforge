"""The theme switch: `.dark` on <html>, where the Radix portals outside the layout root see it."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Theme(Element):
    """Sets the colour scheme of the page; mount it first in the layout root."""

    def __init__(self, *, dark: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the switch, painting the dark scheme while `dark` holds."""
        super().__init__("ff-theme", locals(), None, **kwargs)
