"""The femforge-workbench parser reads every documented flag into Options, and the command picks the card and the dev supervisor."""

from collections.abc import Sequence

import pytest


pytest.importorskip("trame")

from femforge_workbench import cli  # noqa: E402
from femforge_workbench.app import Window  # noqa: E402
from femforge_workbench.cli import CliError, Options, build_parser, select  # noqa: E402
from femforge_workbench.dev import DEV_FLAG  # noqa: E402
from femforge_workbench.registry import CLI_MODULE, discover, legacy  # noqa: E402
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


def test_a_module_without_an_attribute_opens_its_main() -> None:
    assert select(None, "lab.viewer") == legacy("lab.viewer")


def test_an_unknown_name_lists_the_known_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "discover", lambda: discover([]))

    with pytest.raises(CliError, match="unknown workbench 'nope'; known: .*demo"):
        _ = select("nope", None)


def test_dev_hands_the_command_line_to_the_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    supervised: list[tuple[str, Sequence[str]]] = []

    def supervise(module: str, argv: Sequence[str]) -> None:
        supervised.append((module, argv))

    def keep_logging(**_: object) -> None:
        return None

    monkeypatch.setattr(cli, "supervise", supervise)
    monkeypatch.setattr(cli, "setup_logging", keep_logging)

    cli.main(["demo", DEV_FLAG])

    assert supervised == [(CLI_MODULE, ["demo", DEV_FLAG])]
