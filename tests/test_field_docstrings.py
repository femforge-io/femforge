"""Every physical field or constant in the documented modules carries a ``\\`glyph\\` \\`[unit]\\`: meaning`` docstring whose unit pint can parse."""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from collections.abc import Callable
from types import ModuleType, UnionType
from typing import NamedTuple, Union, cast, get_args, get_origin, get_type_hints, is_typeddict

import pytest
from pydantic import BaseModel

from femforge.common.introspect import FIELD_DOCSTRING, attribute_docstrings
from femforge.physical.property_functions import PropertyFunctionField
from femforge.physical.units import Quantity, dimensionality_matches, quantity


DOCUMENTED_MODULES: tuple[str, ...] = (
    "femforge.materials.copper",
    "femforge.materials.neodymium",
    "femforge.physical.conditions",
    "femforge.geometry.winding",
    "femforge.optimize.max_current",
    "femforge.run.result",
    "femforge.run.convergence",
    "femforge.run.mechanical",
    "femforge.elmer.solver_log",
    "femforge.config",
    "femforge.meshing.config",
    "femforge.meshing.gmsh_options",
    "femforge.physical.domain",
    "femforge.frames.base",
    "femforge.frames.solid",
    "femforge.frames.axisymmetric",
    "femforge.frames.cartesian_2d",
    "femforge.meshing.section_edges",
    "femforge.materials.properties",
    "femforge.run.driver",
    "femforge.viz.field_display",
    "femforge.viz.field_lines",
)
"""Modules whose float, Quantity and property-function fields and annotated constants must follow the docstring convention."""

_INHERITED_UNITS = {"-", "?"}
"""``-`` is dimensionless; ``?`` means the unit is whatever the caller's quantity carried (Richardson ladders, solver norms)."""

_SPELLINGS = {"√": "sqrt", "%": "percent", "·turn": "*turn"}


def _normalized(unit: str) -> str:
    text = unit
    for pretty, plain in _SPELLINGS.items():
        text = text.replace(pretty, plain)
    return re.sub(r"sqrt(\w+)", r"(\1)**0.5", text)


def _leaves(hint: object) -> list[object]:
    origin: object = get_origin(hint)
    args: tuple[object, ...] = tuple(get_args(hint))
    if origin is Callable:
        return []
    if origin is None or not args:
        return [hint]
    leaves: list[object] = [] if origin is Union or origin is UnionType else [origin]
    for arg in args:
        leaves.extend(_leaves(arg))
    return leaves


def _property_dimensionality(hint: object) -> str | None:
    """The dimensionality a ``PropertyFunctionField`` declares for a property-function field, or None for any other field."""
    declared = [leaf.dimensionality for leaf in _leaves(hint) if isinstance(leaf, PropertyFunctionField)]

    return declared[0] if declared else None


def _is_physical(hint: object) -> bool:
    leaves = _leaves(hint)
    return float in leaves or Quantity in leaves or _property_dimensionality(hint) is not None


def _is_record(cls: type[object]) -> bool:
    is_dataclass = hasattr(cls, "__dataclass_fields__")
    is_named_tuple = tuple in cls.__mro__ and hasattr(cls, "_fields")
    is_typed_dict = is_typeddict(cast("object", cls))
    return is_dataclass or issubclass(cls, BaseModel) or is_typed_dict or is_named_tuple


def _docstrings(target: type[object] | ModuleType) -> dict[str, str]:
    """Field docstrings of a module or record class, inherited fields included."""
    if isinstance(target, ModuleType):
        return attribute_docstrings(target)
    if issubclass(target, BaseModel):
        return {name: info.description for name, info in target.model_fields.items() if info.description}
    docs: dict[str, str] = {}
    for base in reversed(target.__mro__):
        if _is_record(base):
            docs.update(attribute_docstrings(base))
    return docs


def _hints(owner: type[object] | ModuleType) -> dict[str, object]:
    """The evaluated annotations, leaving out one that names an import made only for type checking; such a field is never physical."""
    try:
        return get_type_hints(owner, include_extras=True)
    except NameError:
        pass

    hints: dict[str, object] = {}
    scopes: list[type[object] | ModuleType] = [owner] if isinstance(owner, ModuleType) else [base for base in reversed(owner.__mro__) if base is not object]
    for scope in scopes:
        module = scope if isinstance(scope, ModuleType) else sys.modules[scope.__module__]
        annotations = cast("dict[str, object]", inspect.get_annotations(scope))
        for name, annotation in annotations.items():
            try:
                hints[name] = cast("object", eval(annotation, vars(module), dict(vars(scope)))) if isinstance(annotation, str) else annotation
            except NameError:
                continue

    return hints


class PhysicalField(NamedTuple):
    """One physical field or constant and the docstring that must describe it."""

    reference: str
    """The module, owner and field name; a test id that ``-k`` selects by package."""

    docstring: str | None
    """The field's docstring, if any."""

    dimensionality: str | None
    """The dimensionality a property-function field declares; None for a float or Quantity field."""


def _physical_fields() -> list[PhysicalField]:
    """Every float, Quantity or property-function field and annotated constant across the checked modules."""
    found: list[PhysicalField] = []
    for module_name in DOCUMENTED_MODULES:
        module = importlib.import_module(module_name)
        members: dict[str, object] = dict(vars(module))
        owners: list[tuple[str, type[object] | ModuleType]] = [(module_name, module)]
        owners += [(f"{module_name}.{obj.__name__}", obj) for obj in members.values() if isinstance(obj, type) and obj.__module__ == module.__name__ and _is_record(obj)]
        for owner_name, owner in owners:
            docs = _docstrings(owner)
            for name, hint in _hints(owner).items():
                if not name.startswith("_") and _is_physical(hint):
                    found.append(PhysicalField(f"{owner_name}.{name}", docs.get(name), _property_dimensionality(hint)))
    return found


PHYSICAL_FIELDS = _physical_fields()


def test_the_documented_modules_expose_physical_fields() -> None:
    assert len(PHYSICAL_FIELDS) > 40


@pytest.mark.parametrize("field", PHYSICAL_FIELDS, ids=[field.reference for field in PHYSICAL_FIELDS])
def test_physical_field_docstring_format(field: PhysicalField) -> None:
    reference = field.reference
    assert field.docstring, f"{reference} needs a `glyph` `[unit]`: meaning docstring"
    first_line = inspect.cleandoc(field.docstring).splitlines()[0]
    match = FIELD_DOCSTRING.match(first_line)
    assert match, f"{reference}: first line must read `glyph` `[unit]`: meaning, got {first_line!r}"
    unit = match.group("unit")
    if field.dimensionality is not None:
        documented = "" if unit == "-" else str(quantity(1.0, _normalized(unit)).dimensionality)
        assert dimensionality_matches(field.dimensionality, documented), f"{reference}: [{unit}] is not {field.dimensionality or 'dimensionless'}"
    if unit in _INHERITED_UNITS:
        return
    try:
        _ = quantity(1.0, _normalized(unit))
    except Exception as error:  # noqa: BLE001 - pint raises several error types for an unparseable unit
        pytest.fail(f"{reference}: pint cannot parse the unit {unit!r} ({error})")


def test_format_accepts_the_conventional_spellings() -> None:
    assert FIELD_DOCSTRING.match("`OD_mag` `[mm]`: Outer diameter.")
    assert FIELD_DOCSTRING.match("`[-]`: A dimensionless knob.")
    assert not FIELD_DOCSTRING.match("Outer diameter (mm).")
    for unit in ("mm", "°C", "Ω", "A/mm²", "W/(m²·K)", "N/√W", "A·turn", "%", "1/√Ω"):
        _ = quantity(1.0, _normalized(unit))
