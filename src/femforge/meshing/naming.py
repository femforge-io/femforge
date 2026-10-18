"""The physical-group name grammar: the normalization and composition rules shared by the mesher (emit) and the bundle readers (require)."""

from typing import Callable

from femforge.materials.properties import MaterialProperties
from femforge.physical.domain import EntityTag


# Elmer reads names into CHARACTER(LEN=MAX_NAME_LEN) buffers, MAX_NAME_LEN = 128 (fem/src/Types.F90). Adjacency-derived
# boundary names join two body names ("A__B"), so warn while a single body name still leaves the pair headroom.
MAX_SAFE_GROUP_NAME = 60


def name_fragment(text: str) -> str:
    """One normalized segment of a group name: spaces become underscores, uppercased."""
    return text.replace(" ", "_").upper()


def compose_group_name(material: MaterialProperties, tags: list[EntityTag], warn: Callable[[str], None], authored: str | None = None) -> str:
    """The physical-group name shared verbatim with Elmer: the ``authored`` part name when given, else the compound MATERIAL_TAG1_TAG2.

    Args:
        material: The material of the group's entities.
        tags: The entity tags the group matched, in domain order.
        warn: Receives the warning when the name risks Elmer's name length limit.
        authored: The ``Name`` tag the entities were authored with, or None to compose the name.
    """
    parts = [authored] if authored else [material.tag, *(tag.name for tag in tags)]
    name = "_".join(name_fragment(part) for part in parts if part)
    if len(name) > MAX_SAFE_GROUP_NAME:
        warn(f"Group name {name!r} is {len(name)} chars; boundary pair names may exceed Elmer's 128-char MAX_NAME_LEN. Shorten tag keys/values (see compose_group_name).")
    return name


def face_group_name(authored: str | None, tags: list[EntityTag]) -> str | None:
    """A named face group's name: the ``authored`` face name, else the names of the matched tags that select something, else None for a face nothing names."""
    if authored:
        return name_fragment(authored)

    selecting: list[str] = [tag.name for tag in tags if tag.selector]
    if selecting:
        return name_fragment("_".join(selecting))

    return None
