"""The sif keyword-model base: fields carry their Elmer keyword as ``Map(...)`` metadata and ``to_elmer()`` reflects them out."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.pydantic import mapped_items


# An Elmer sif keyword value: copied verbatim into sif blocks, so it is exactly what sif syntax allows (a number, bool, or quoted/bare string).
SifValue = str | int | float | bool
"""An Elmer sif keyword value, copied verbatim into the sif."""


class SifKeywordModel(BaseModel):
    """
    Base for a config model whose fields ARE its sif keywords: each field carries its Elmer keyword as ``Map(...)``
    metadata, and [`to_elmer`][] reflects the pairs back out via ``mapped_items`` (no parallel string-keyed table).
    """

    model_config = ConfigDict(validate_assignment=True, use_attribute_docstrings=True)

    extra_keywords: dict[str, SifValue] = Field(default_factory=dict)
    """Escape hatch: verbatim sif keyword/value pairs merged (last, so they win) into [`to_elmer`][] — any Elmer
    keyword the typed fields don't model yet stays authorable without subclassing."""

    def to_elmer(self) -> dict[str, SifValue]:
        """The sif keyword/value dict reflected from each field's ``Map(...)`` keyword, with ``extra_keywords`` merged last.

        None fields are left out so Elmer keeps its defaults, and a list expands to a numbered family such as ``Vector Field 1..n``.
        """
        out: dict[str, SifValue] = {}
        for key, value in mapped_items(self):
            if isinstance(value, list):
                for index, item in enumerate(value, 1):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]  # mapped_items erases the item type
                    if isinstance(item, (str, int, float)):
                        out[f"{key} {index}"] = item
            elif isinstance(value, (str, int, float)):
                out[key] = value
        out.update(self.extra_keywords)
        return out
