"""The workbench catalogue: output kinds, spec builders, target resolution and discovery over injected entry points."""

import os
import sys
import time
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest

from femforge_workbench import registry
from femforge_workbench.registry import (
    BROWSERS,
    BUILTIN_GROUP,
    DEMO,
    ENTRY_POINT_GROUP,
    FIELD_VIEWER,
    LAUNCHER,
    MONITORS,
    OutputKind,
    OutputSpec,
    WorkbenchKind,
    WorkbenchSpec,
    discover,
    import_root,
    legacy,
    panel,
    renderable,
    resolve,
    script,
    script_module,
    split_target,
    target_of,
)


@dataclass(frozen=True)
class _Point:
    name: str
    payload: object

    def load(self) -> object:
        return self.payload


def _script(name: str, group: str = "Lab", title: str | None = None) -> WorkbenchSpec:
    return script(name, title or name.title(), target=f"lab.{name}", group=group)


@pytest.mark.parametrize(
    ("suffix", "kind"),
    [
        (".csv", OutputKind.TABLE),
        (".parquet", OutputKind.TABLE),
        (".HTML", OutputKind.HTML),
        (".png", OutputKind.IMAGE),
        (".svg", OutputKind.IMAGE),
        (".md", OutputKind.MARKDOWN),
        (".txt", OutputKind.TEXT),
        ("", OutputKind.TEXT),
    ],
)
def test_output_kind_follows_the_suffix(suffix: str, kind: OutputKind) -> None:
    assert OutputKind.from_path(Path(f"report{suffix}")) is kind


def test_output_spec_and_last_run_follow_the_files(tmp_path: Path) -> None:
    older = tmp_path / "sweep.csv"
    newer = tmp_path / "report.html"
    _ = older.write_text("a,b", encoding="utf-8")
    _ = newer.write_text("<p/>", encoding="utf-8")
    later = time.time() + 100
    os.utime(newer, (later, later))
    spec = script(
        "sweep",
        "Sweep",
        target="lab.sweep",
        outputs=[OutputSpec.at(older), OutputSpec.at(newer, "Report"), OutputSpec.at(tmp_path / "missing.png")],
    )

    last = spec.last_run()

    assert spec.outputs[0].kind is OutputKind.TABLE and spec.outputs[0].title == "sweep.csv"
    assert spec.outputs[1].title == "Report"
    assert spec.outputs[2].modified() is None
    assert last is not None and abs(last.timestamp() - later) < 2.0
    assert script("idle", "Idle", target="lab.idle").last_run() is None


def test_commands_and_targets() -> None:
    run = script("sweep", "Sweep", target="lab.sweep", args=("--fast",), cwd=Path("/lab"))
    dock = panel("board", "Board", target="lab.board:workbench")
    viewer = renderable("preview", "Preview", target="lab.preview:build")

    assert run.command() == [sys.executable, "-m", "lab.sweep", "--fast"] and run.cwd == Path("/lab")
    assert dock.command() == [sys.executable, "-m", "femforge_workbench.cli", "board"]
    assert viewer.kind is WorkbenchKind.RENDERABLE and viewer.module == "lab.preview"
    assert split_target("lab.board:workbench") == ("lab.board", "workbench")
    assert split_target("lab.board") == ("lab.board", None)


def test_legacy_wraps_the_old_main_contract() -> None:
    spec = legacy("lab.tubular.workbench")

    assert spec.kind is WorkbenchKind.RENDERABLE
    assert spec.name == "workbench" and spec.target == "lab.tubular.workbench:main"


def test_resolve_imports_the_module_and_picks_the_attribute() -> None:
    assert resolve("femforge_workbench.registry:ENTRY_POINT_GROUP") == ENTRY_POINT_GROUP
    module = resolve("femforge_workbench.registry")
    assert isinstance(module, ModuleType) and module is registry
    with pytest.raises(AttributeError):
        _ = resolve("femforge_workbench.registry:absent")


def test_a_function_target_is_named_by_its_module_and_name() -> None:
    def nested() -> None: ...

    assert panel("cards", "Cards", target=discover).target == "femforge_workbench.registry:discover"
    assert renderable("cards", "Cards", target=discover).target == "femforge_workbench.registry:discover"
    assert target_of("lab.board:workbench") == "lab.board:workbench"
    for local in (nested, lambda: None):
        with pytest.raises(ValueError, match="define the workbench function at module level"):
            _ = target_of(local)


def test_a_function_of_the_running_script_is_named_by_the_module_its_file_imports_as(monkeypatch: pytest.MonkeyPatch) -> None:
    def flux() -> None: ...

    flux.__module__ = "__main__"
    flux.__qualname__ = "flux"
    running = ModuleType("__main__")
    monkeypatch.setitem(sys.modules, "__main__", running)

    with pytest.raises(ValueError, match="interactive session"):
        _ = script_module()

    running.__file__ = str(Path("/lab/src/bearing_seat.py"))
    as_file = target_of(flux)
    running.__spec__ = ModuleSpec("lab.bearing_seat", None)

    assert as_file == "bearing_seat:flux" and target_of(flux) == "lab.bearing_seat:flux"


def test_a_function_imports_from_its_script_directory_or_from_above_its_top_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def flux() -> None: ...

    flux.__module__ = "__main__"
    running = ModuleType("__main__")
    monkeypatch.setitem(sys.modules, "__main__", running)
    no_file = import_root(flux)

    running.__file__ = str(tmp_path / "lab" / "main.py")
    as_script = import_root(flux)
    running.__spec__ = ModuleSpec("lab.main", None)
    as_module = import_root(flux)
    running.__file__ = str(tmp_path / "lab" / "board" / "__init__.py")
    running.__spec__ = ModuleSpec("lab.board", None)
    as_package = import_root(flux)

    assert no_file is None
    assert as_script == (tmp_path / "lab").resolve(), "a script imports from its own directory"
    assert as_module == as_package == tmp_path.resolve(), "a package module imports from above its top package"
    assert import_root(discover) == Path(registry.__file__).resolve().parents[1]


def test_discover_merges_builtins_with_entry_points_sorted_by_group_and_title() -> None:
    points = [_Point("lab", [_script("zeta"), _script("alpha")]), _Point("extra", _script("mid", group="Apps"))]

    specs = discover(points)

    assert [spec.name for spec in specs] == ["mid", "demo", "field-viewer", "launcher", "monitors", "browsers", "alpha", "zeta"]
    assert specs[1] == DEMO and specs[1].group == BUILTIN_GROUP
    assert specs[3] == LAUNCHER and LAUNCHER.kind is WorkbenchKind.PANEL
    assert discover([]) == [DEMO, FIELD_VIEWER, LAUNCHER, MONITORS, BROWSERS]


def test_discover_tolerates_the_same_spec_twice_and_rejects_a_conflicting_name() -> None:
    assert discover([_Point("demo", DEMO)]) == [DEMO, FIELD_VIEWER, LAUNCHER, MONITORS, BROWSERS]

    with pytest.raises(ValueError, match="demo"):
        _ = discover([_Point("other", panel("demo", "Other demo", target="elsewhere:workbench"))])

    with pytest.raises(TypeError, match="broken"):
        _ = discover([_Point("broken", "not a spec")])


def test_discover_reads_the_installed_entry_points() -> None:
    specs = discover()

    assert DEMO in specs
    assert all(isinstance(spec, WorkbenchSpec) for spec in specs)
