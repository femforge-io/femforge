"""Helpers over the JSON tree the React serializer renders: {tag, props, children} nodes."""

from collections.abc import Iterator, Mapping
from typing import cast


type Node = Mapping[str, object]


def nodes(root: object) -> Iterator[Node]:
    """Every element node under `root`, depth first, `root` included when it is one; a tagless mapping (a slot) only contributes its children."""
    if not isinstance(root, Mapping):
        return

    node = cast("Node", root)
    if "tag" in node:
        yield node

    for child in cast("list[object]", node.get("children", [])):
        yield from nodes(child)


def find(root: object, tag: str) -> list[Node]:
    """The nodes of that tag under `root`, in document order."""
    return [node for node in nodes(root) if node.get("tag") == tag]


def props(node: Node) -> Mapping[str, object]:
    return cast("Mapping[str, object]", node.get("props", {}))


def prop(node: Node, name: str) -> object:
    """The serialized value of a prop, None when the node has none."""
    return props(node).get(name)


def texts(node: Node) -> list[str]:
    """The literal string children of a node."""
    return [child for child in cast("list[object]", node.get("children", [])) if isinstance(child, str)]
