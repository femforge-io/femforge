"""The expression AST: evaluation, MATC/Lua rendering, operator composition, and JSON round-trip."""

import pytest
from pydantic import TypeAdapter

from femforge.physical.expressions import Add, Const, Div, Expr, Mul, Neg, Param, Pow, Sub, as_expr, evaluate, render


T = Param(name="temperature")

# sigma(T) = 5.96e7 / (1 + 3.93e-3 (T - 293.15)) — the copper law, built by operator composition.
SIGMA = 5.96e7 / (1.0 + 3.93e-3 * (T - 293.15))


def test_operator_composition_builds_the_expected_tree() -> None:
    assert isinstance(SIGMA, Div)
    assert isinstance(SIGMA.left, Const)
    assert isinstance(SIGMA.right, Add)


def test_evaluation_matches_the_law() -> None:
    assert SIGMA.evaluate({"temperature": 293.15}) == pytest.approx(5.96e7)
    assert SIGMA.evaluate({"temperature": 393.15}) == pytest.approx(5.96e7 / (1 + 0.393))
    assert evaluate(as_expr(SIGMA), {"temperature": 293.15}) == pytest.approx(5.96e7)


def test_matc_and_lua_rendering_reference_tx() -> None:
    matc = render(as_expr(SIGMA), refs={"temperature": "tx(0)"})
    assert "tx(0)" in matc
    assert matc.count("(") == matc.count(")")
    lua = render(as_expr(SIGMA), refs={"temperature": "tx[0]"})
    assert "tx[0]" in lua
    # The rendered MATC source evaluates identically to the Python evaluation (parentheses preserved semantics).
    assert eval(matc.replace("tx(0)", "293.15")) == pytest.approx(5.96e7)  # noqa: S307 - test-only sanity eval of our own rendering


def test_every_operator_evaluates_and_renders() -> None:
    x = Param(name="x")
    cases: list[tuple[Expr, float, str]] = [
        (x + 2.0, 5.0, "(X + 2.0)"),
        (2.0 + x, 5.0, "(2.0 + X)"),
        (x - 2.0, 1.0, "(X - 2.0)"),
        (2.0 - x, -1.0, "(2.0 - X)"),
        (x * 2.0, 6.0, "(X * 2.0)"),
        (2.0 * x, 6.0, "(2.0 * X)"),
        (x / 2.0, 1.5, "(X / 2.0)"),
        (6.0 / x, 2.0, "(6.0 / X)"),
        (x**2.0, 9.0, "(X ^ 2.0)"),
        (-x, -3.0, "(-X)"),
    ]
    for expr, expected, source in cases:
        assert expr.evaluate({"x": 3.0}) == pytest.approx(expected)
        assert expr.render({"x": "X"}) == source


def test_parameters_are_collected_across_the_tree() -> None:
    x, y = Param(name="x"), Param(name="y")
    assert (x * y + 1.0).parameters() == {"x", "y"}
    assert (-(x**2.0)).parameters() == {"x"}
    assert Const(value=1.0).parameters() == frozenset()


def test_unbound_parameter_raises_with_names() -> None:
    with pytest.raises(KeyError, match="temperature"):
        _ = SIGMA.evaluate({})
    with pytest.raises(KeyError, match="temperature"):
        _ = SIGMA.render({})


def test_json_round_trip_revives_exact_node_types() -> None:
    adapter: TypeAdapter[Expr] = TypeAdapter(Expr)
    for expr in (SIGMA, Pow(base=Param(name="x"), exponent=Const(value=2.0)), Neg(operand=Const(value=1.0)), Sub(left=Const(value=1.0), right=Param(name="x")), Mul(left=Const(value=2.0), right=Param(name="x"))):
        dumped: object = adapter.dump_python(expr)  # pyright: ignore[reportAny]  # TypeAdapter.dump_python returns Any
        revived = adapter.validate_python(dumped)
        assert revived == expr
    # And through actual JSON text.
    revived = adapter.validate_json(adapter.dump_json(SIGMA))
    assert revived.evaluate({"temperature": 293.15}) == pytest.approx(5.96e7)


def test_as_expr_wraps_numbers() -> None:
    wrapped = as_expr(2)
    assert isinstance(wrapped, Const) and wrapped.value == 2.0
    assert as_expr(T) is T
