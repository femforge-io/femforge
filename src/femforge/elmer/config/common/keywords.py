"""The sif keyword-model base: fields carry their Elmer keyword as ``Map(...)`` metadata and ``to_elmer()`` reflects them out."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

from femforge.common.pydantic import get_map_key, mapped_items


# An Elmer sif keyword value: copied verbatim into sif blocks, so it is exactly what sif syntax allows (a number, bool, or quoted/bare string).
SifValue = str | int | float | bool
"""An Elmer sif keyword value, copied verbatim into the sif."""


class EquationScope:
    """``Annotated`` marker: the field's keyword belongs in the Equation block, not the block of the model that declares it."""


def _equation_scoped(field: FieldInfo) -> bool:
    return any(isinstance(meta, EquationScope) for meta in field.metadata)  # pyright: ignore[reportAny]  # FieldInfo.metadata is list[Any]


def equation_items(model: BaseModel) -> Iterator[tuple[str, object]]:
    """Yield ``(keyword, value)`` for each ``EquationScope()`` field of ``model`` that carries a ``Map()`` and is not None."""
    for name, field in type(model).model_fields.items():
        key: str | None = get_map_key(field)

        if key is None or not _equation_scoped(field):
            continue

        value: object = getattr(model, name)  # pyright: ignore[reportAny]  # model field values are dynamic

        if value is None:
            continue

        yield key, value


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

        None fields are left out so Elmer keeps its defaults, ``EquationScope()`` fields are left to the Equation block, and a list expands to a numbered family such as ``Vector Field 1..n``.
        """
        scoped: dict[str, object] = dict(equation_items(self))
        out: dict[str, SifValue] = {}
        for key, value in mapped_items(self):
            if key in scoped:
                continue

            if isinstance(value, list):
                for index, item in enumerate(value, 1):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]  # mapped_items erases the item type
                    if isinstance(item, (str, int, float)):
                        out[f"{key} {index}"] = item
            elif isinstance(value, (str, int, float)):
                out[key] = value
        out.update(self.extra_keywords)
        return out
