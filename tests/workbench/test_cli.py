"""The femforge-workbench parser reads every documented flag into Options."""

import pytest


pytest.importorskip("trame")

from femforge_workbench.app import Window  # noqa: E402
from femforge_workbench.cli import Options, build_parser  # noqa: E402
from femforge_workbench.serve import DEFAULT_PORT  # noqa: E402


def test_build_parser_reads_every_flag() -> None:
    argv = ["demo", "--app", "--port", "8123", "--watch", "a", "--watch", "b", "--telemetry", "tele", "--persist", "saved", "--dev"]

    options, extra = build_parser().parse_known_args(argv, Options())

    assert extra == []
    assert options.name == "demo"
    assert options.module is None
    assert options.window is Window.APP
    assert options.port == 8123
    assert options.watch == ["a", "b"]
    assert options.telemetry == "tele"
    assert options.persist == "saved"
    assert options.dev is True


def test_build_parser_reads_a_module_target_and_keeps_the_defaults() -> None:
    options, _ = build_parser().parse_known_args(["-m", "pkg.mod:split", "--webview"], Options())

    assert options.name is None
    assert options.module == "pkg.mod:split"
    assert options.window is Window.WEBVIEW
    assert options.port == DEFAULT_PORT
    assert options.watch == []
    assert options.telemetry is None
    assert options.persist is None


def test_options_without_window_flags_open_a_browser_tab() -> None:
    options, _ = build_parser().parse_known_args([], Options())

    assert options.window is Window.BROWSER
