"""Field-level ``Annotated`` markers read back by reflection: ``Map()`` ties a field to an external key name and ``HashExclude()`` keeps it out of a content hash."""

from collections.abc import Iterator

from pydantic import BaseModel
from pydantic.fields import FieldInfo


class Map:
    """
    ``Annotated`` metadata marker tying a model field to an external key name.

    Example: ``Annotated[bool, Map("Geometry.OCCImportLabels"), Field(...)]`` -- the gmsh option name lives on the field.
    """

    def __init__(self, map: str) -> None:
        """Tie the field to the external key ``map``."""
        self.map = map


class HashExclude:
    """``Annotated`` marker: the field changes what is solved on a geometry, not the geometry, so it stays out of the geometry hash."""


def get_map_key(field: FieldInfo) -> str | None:
    """The external key from a field's ``Map()`` metadata, or None if the field carries no ``Map()``."""
    for meta in field.metadata:  # pyright: ignore[reportAny]  # FieldInfo.metadata is list[Any]
        if isinstance(meta, Map):
            return meta.map

    return None


def mapped_items(model: BaseModel) -> Iterator[tuple[str, object]]:
    """Yield ``(external_key, value)`` for each field carrying a ``Map()`` whose current value is not None."""
    for name, field in type(model).model_fields.items():
        key: str | None = get_map_key(field)
        if key is None:
            continue

        value: object = getattr(model, name)  # pyright: ignore[reportAny]  # model field values are dynamic
        if value is None:
            continue

        yield key, value


def hash_excluded_fields(model: type[BaseModel]) -> set[str]:
    """The names of the model's fields carrying ``HashExclude()``."""
    return {
        name
        for name, field in model.model_fields.items()
        if any(isinstance(meta, HashExclude) for meta in field.metadata)  # pyright: ignore[reportAny]  # FieldInfo.metadata is list[Any]
    }


def hash_exclude_spec(model: BaseModel) -> dict[str | int, object]:
    """
    A nested pydantic ``exclude`` spec naming every ``HashExclude()`` field at any depth of ``model``.

    Walked from the instance, so a polymorphic (``SerializeAsAny``) subclass contributes its own marked fields and list items are addressed by index.
    """
    spec: dict[str | int, object] = {}
    for name in type(model).model_fields:
        if name in hash_excluded_fields(type(model)):
            spec[name] = True
            continue

        nested: dict[str | int, object] = _nested_hash_exclude(getattr(model, name))  # pyright: ignore[reportAny]  # model field values are dynamic
        if nested:
            spec[name] = nested
    return spec


def _nested_hash_exclude(value: object) -> dict[str | int, object]:
    """The exclude spec below one field value: a model, a sequence of them (by index), or a mapping of them (by key)."""
    if isinstance(value, BaseModel):
        return hash_exclude_spec(value)

    nested: dict[str | int, object] = {}
    if isinstance(value, (list, tuple)):
        items: list[object] = list(value)  # pyright: ignore[reportUnknownArgumentType]  # heterogeneous containers
        for index, item in enumerate(items):
            below: dict[str | int, object] = _nested_hash_exclude(item)
            if below:
                nested[index] = below
    elif isinstance(value, dict):
        entries: dict[object, object] = dict(value)  # pyright: ignore[reportUnknownArgumentType]  # heterogeneous containers
        for key, item in entries.items():
            below = _nested_hash_exclude(item)
            if below and isinstance(key, str):
                nested[key] = below
    return nested
