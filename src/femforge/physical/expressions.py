"""Typed expression AST over bare SI magnitudes for variable-dependent material properties: evaluates in Python, renders to Elmer MATC or Lua, serializes to JSON."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "Expr",
    "ExprNode",
    "Const",
    "Param",
    "Add",
    "Sub",
    "Mul",
    "Div",
    "Pow",
    "Neg",
    "as_expr",
    "render",
    "evaluate",
]


# The two Elmer runtime expression backends this AST renders to. MATC indexes the sif `Variable` list as tx(0);
# Lua (when Elmer is built with it) as tx[0].
Backend = Literal["matc", "lua"]


class ExprNode(BaseModel):
    """Base node: shared config + Python operator composition, so laws are authored as plain arithmetic."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    def evaluate(self, env: Mapping[str, float]) -> float:
        """This subtree's value with each parameter bound to a bare SI float."""
        raise NotImplementedError

    def render(self, refs: Mapping[str, str]) -> str:
        """This subtree as backend source, with each parameter name replaced by its reference (e.g. ``tx(0)``)."""
        raise NotImplementedError

    def parameters(self) -> frozenset[str]:
        """Every parameter name referenced in this subtree."""
        raise NotImplementedError

    # -- operator composition (floats auto-wrap into Const) -------------------

    def __add__(self, other: "ExprNode | float") -> "Add":
        return Add(left=as_expr(self), right=as_expr(other))

    def __radd__(self, other: float) -> "Add":
        return Add(left=as_expr(other), right=as_expr(self))

    def __sub__(self, other: "ExprNode | float") -> "Sub":
        return Sub(left=as_expr(self), right=as_expr(other))

    def __rsub__(self, other: float) -> "Sub":
        return Sub(left=as_expr(other), right=as_expr(self))

    def __mul__(self, other: "ExprNode | float") -> "Mul":
        return Mul(left=as_expr(self), right=as_expr(other))

    def __rmul__(self, other: float) -> "Mul":
        return Mul(left=as_expr(other), right=as_expr(self))

    def __truediv__(self, other: "ExprNode | float") -> "Div":
        return Div(left=as_expr(self), right=as_expr(other))

    def __rtruediv__(self, other: float) -> "Div":
        return Div(left=as_expr(other), right=as_expr(self))

    def __pow__(self, other: "ExprNode | float") -> "Pow":
        return Pow(base=as_expr(self), exponent=as_expr(other))

    def __neg__(self) -> "Neg":
        return Neg(operand=as_expr(self))


class Const(ExprNode):
    """A literal SI magnitude."""

    op: Literal["const"] = "const"
    """Discriminator that names this node type in serialized JSON."""

    value: float
    """`c` `[?]`: The literal magnitude, in SI units."""

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The literal value."""
        return self.value

    def render(self, refs: Mapping[str, str]) -> str:
        """The literal at full float precision."""
        # repr keeps full float precision; MATC and Lua both read e-notation.
        return repr(self.value)

    def parameters(self) -> frozenset[str]:
        """The empty set: a literal references no parameter."""
        return frozenset()


class Param(ExprNode):
    """A named parameter (e.g. ``temperature``), bound at evaluation/render time."""

    op: Literal["param"] = "param"
    """Discriminator that names this node type in serialized JSON."""

    name: str
    """The parameter name that ``env`` and ``refs`` bind."""

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The value ``env`` binds to this parameter."""
        if self.name not in env:
            raise KeyError(f"expression parameter {self.name!r} not bound; supplied: {sorted(env)}")
        return env[self.name]

    def render(self, refs: Mapping[str, str]) -> str:
        """The backend reference ``refs`` binds to this parameter."""
        if self.name not in refs:
            raise KeyError(f"expression parameter {self.name!r} has no backend reference; supplied: {sorted(refs)}")
        return refs[self.name]

    def parameters(self) -> frozenset[str]:
        """This parameter's name."""
        return frozenset({self.name})


class _Binary(ExprNode):
    """Shared shape for the binary operators."""

    left: "Expr"
    """The left operand."""

    right: "Expr"
    """The right operand."""

    _symbol: str = ""

    def parameters(self) -> frozenset[str]:
        return self.left.parameters() | self.right.parameters()

    def render(self, refs: Mapping[str, str]) -> str:
        # Fully parenthesized: correct under both MATC and Lua precedence without a precedence table.
        return f"({self.left.render(refs)} {self._symbol} {self.right.render(refs)})"


class Add(_Binary):
    """The sum of two subexpressions."""

    op: Literal["add"] = "add"
    """Discriminator that names this node type in serialized JSON."""

    _symbol: str = "+"

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The sum of both operands."""
        return self.left.evaluate(env) + self.right.evaluate(env)


class Sub(_Binary):
    """The difference of two subexpressions."""

    op: Literal["sub"] = "sub"
    """Discriminator that names this node type in serialized JSON."""

    _symbol: str = "-"

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The left operand minus the right one."""
        return self.left.evaluate(env) - self.right.evaluate(env)


class Mul(_Binary):
    """The product of two subexpressions."""

    op: Literal["mul"] = "mul"
    """Discriminator that names this node type in serialized JSON."""

    _symbol: str = "*"

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The product of both operands."""
        return self.left.evaluate(env) * self.right.evaluate(env)


class Div(_Binary):
    """The quotient of two subexpressions."""

    op: Literal["div"] = "div"
    """Discriminator that names this node type in serialized JSON."""

    _symbol: str = "/"

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The left operand divided by the right one."""
        return self.left.evaluate(env) / self.right.evaluate(env)


class Pow(ExprNode):
    """One subexpression raised to the power of another."""

    op: Literal["pow"] = "pow"
    """Discriminator that names this node type in serialized JSON."""

    base: "Expr"
    """The base."""

    exponent: "Expr"
    """The exponent."""

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The base raised to the exponent."""
        # math.pow keeps the operation float-typed (the ** operator's stub admits complex).
        return math.pow(self.base.evaluate(env), self.exponent.evaluate(env))

    def render(self, refs: Mapping[str, str]) -> str:
        """The power as ``(base ^ exponent)``."""
        # ^ is exponentiation in both MATC and Lua.
        return f"({self.base.render(refs)} ^ {self.exponent.render(refs)})"

    def parameters(self) -> frozenset[str]:
        """The parameters of the base and the exponent."""
        return self.base.parameters() | self.exponent.parameters()


class Neg(ExprNode):
    """The negation of a subexpression."""

    op: Literal["neg"] = "neg"
    """Discriminator that names this node type in serialized JSON."""

    operand: "Expr"
    """The negated subexpression."""

    def evaluate(self, env: Mapping[str, float]) -> float:
        """The negated operand."""
        return -self.operand.evaluate(env)

    def render(self, refs: Mapping[str, str]) -> str:
        """The negation as ``(-operand)``."""
        return f"(-{self.operand.render(refs)})"

    def parameters(self) -> frozenset[str]:
        """The operand's parameters."""
        return self.operand.parameters()


# The serializable node union: `op` discriminates, so a JSON dump revives to the exact node types.
Expr = Annotated[
    Union[Const, Param, Add, Sub, Mul, Div, Pow, Neg],
    Field(discriminator="op"),
]
"""Any expression node, discriminated by its ``op``."""

# The forward references inside the node models point at the annotated union above.
_Binary.model_rebuild()
Pow.model_rebuild()
Neg.model_rebuild()


def as_expr(value: "ExprNode | float") -> "Expr":
    """Wrap a bare number into [`Const`][]; pass nodes through (the operator-composition coercion point)."""
    if isinstance(value, ExprNode):
        return value  # pyright: ignore[reportReturnType]  # every concrete node is a union member; ExprNode itself is never instantiated
    return Const(value=float(value))


def evaluate(expr: "Expr", env: Mapping[str, float]) -> float:
    """Module-level convenience mirroring ``expr.evaluate``."""
    return expr.evaluate(env)


def render(expr: "Expr", *, refs: Mapping[str, str]) -> str:
    """Backend source for ``expr`` with parameter references substituted (e.g. ``{"temperature": "tx(0)"}``)."""
    return expr.render(refs)
