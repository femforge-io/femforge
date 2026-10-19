"""The workbench catalogue: specs of what can be opened, discovered from the built-ins and the femforge.workbenches entry points, imported only on open."""

import importlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from typing import Protocol, cast


ENTRY_POINT_GROUP = "femforge.workbenches"
BUILTIN_GROUP = "Built-in"
CLI_MODULE = "femforge_workbench.cli"
LEGACY_ATTR = "main"
MAIN_MODULE = "__main__"
PACKAGE_INIT = "__init__"

type Target = str | Callable[[], object]
"""A `pkg.module:attr` string, or the module-level function it names."""


class WorkbenchKind(StrEnum):
    """How a spec is opened: a dock of panels, one Renderable in a plotter, or a script run in a subprocess."""

    PANEL = "panel"
    RENDERABLE = "renderable"
    SCRIPT = "script"


class OutputKind(StrEnum):
    """How a script output is shown."""

    TABLE = "table"
    HTML = "html"
    IMAGE = "image"
    MARKDOWN = "markdown"
    TEXT = "text"

    @classmethod
    def from_path(cls, path: Path) -> "OutputKind":
        """The kind a file's suffix implies, text when unknown."""
        match path.suffix.lower():
            case ".csv" | ".parquet":
                return cls.TABLE
            case ".html" | ".htm":
                return cls.HTML
            case ".png" | ".jpg" | ".jpeg" | ".svg" | ".gif" | ".webp":
                return cls.IMAGE
            case ".md":
                return cls.MARKDOWN
            case _:
                return cls.TEXT


@dataclass(frozen=True)
class OutputSpec:
    """One file a script produces."""

    path: Path
    title: str
    kind: OutputKind

    @classmethod
    def at(cls, path: Path, title: str | None = None) -> "OutputSpec":
        """An output typed from its suffix and titled by its name unless given."""
        return cls(path=path, title=title or path.name, kind=OutputKind.from_path(path))

    def modified(self) -> datetime | None:
        """When the file was last written, None while it does not exist."""
        if not self.path.exists():
            return None

        return datetime.fromtimestamp(self.path.stat().st_mtime)


@dataclass(frozen=True)
class WorkbenchSpec:
    """One card of the catalogue: what it is, how it opens, and what it leaves behind."""

    name: str
    title: str
    kind: WorkbenchKind
    target: str
    description: str = ""
    group: str = ""
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    outputs: tuple[OutputSpec, ...] = ()

    @property
    def module(self) -> str:
        """The module the target names."""
        return split_target(self.target)[0]

    def last_run(self) -> datetime | None:
        """The newest output timestamp, None when nothing has been produced."""
        stamps = [stamp for stamp in (output.modified() for output in self.outputs) if stamp is not None]
        if not stamps:
            return None

        return max(stamps)

    def command(self) -> list[str]:
        """The subprocess that runs this spec: the module for a script, the workbench CLI for the rest."""
        if self.kind is WorkbenchKind.SCRIPT:
            return [sys.executable, "-m", self.module, *self.args]

        return [sys.executable, "-m", CLI_MODULE, self.name, *self.args]


class EntryPointLike(Protocol):
    """What discover needs from an entry point."""

    @property
    def name(self) -> str:
        """The entry point's name."""
        ...

    def load(self) -> object:
        """Imports and returns what the entry point names."""
        ...


def split_target(target: str) -> tuple[str, str | None]:
    """The module and optional attribute of a `pkg.module:attr` target."""
    module, _, attr = target.partition(":")

    return module, attr or None


def resolve(target: str) -> object:
    """The object a target names, importing its module now: `pkg.module:attr` gives the attribute, `pkg.module` the module."""
    module_name, attr = split_target(target)
    module = importlib.import_module(module_name)
    if attr is None:
        return module

    return cast("object", getattr(module, attr))


def script_module() -> str:
    """The module name the running script imports as: its `-m` name, else its file's stem, since the script's directory leads sys.path."""
    main = sys.modules[MAIN_MODULE]
    if main.__spec__ is not None:
        return main.__spec__.name

    file = getattr(main, "__file__", None)
    if not isinstance(file, str):
        raise ValueError("an interactive session has no module a workbench can be imported from")

    return Path(file).stem


def import_root(function: Callable[[], object]) -> Path | None:
    """The directory a module-level function's module imports from: the running script's own directory, else the one holding its top package; None for a module with no file."""
    module = sys.modules.get(function.__module__)
    file = getattr(module, "__file__", None)
    if not isinstance(file, str):
        return None

    spec = getattr(module, "__spec__", None)
    name = getattr(spec, "name", None)
    path = Path(file).resolve()
    dotted = name if isinstance(name, str) else path.stem
    depth = dotted.count(".") + (path.stem == PACKAGE_INIT)

    return path.parents[depth]


def target_of(target: Target) -> str:
    """The `pkg.module:attr` string of a target; a function defined in the running script is named by the module its file imports as."""
    if isinstance(target, str):
        return target

    name = target.__qualname__
    if not name.isidentifier():
        raise ValueError(f"{name} is not a module attribute; define the workbench function at module level so a save can import it again")

    module = script_module() if target.__module__ == MAIN_MODULE else target.__module__

    return f"{module}:{name}"


def script(name: str, title: str, *, target: str, description: str = "", group: str = "", args: Sequence[str] = (), cwd: Path | None = None, outputs: Sequence[OutputSpec] = ()) -> WorkbenchSpec:
    """A script card, run in a subprocess.

    Args:
        name: The card's unique name, which the command line opens it by.
        title: The title the card and the title menu show.
        target: The module run with `-m`.
        description: The one-line summary under the title.
        group: The catalogue group the card is listed under.
        args: The arguments the script runs with.
        cwd: The directory the script runs in; None keeps the shell's.
        outputs: The files the script produces, which the launcher shows.
    """
    return WorkbenchSpec(
        name=name,
        title=title,
        kind=WorkbenchKind.SCRIPT,
        target=target,
        description=description,
        group=group,
        args=tuple(args),
        cwd=cwd,
        outputs=tuple(outputs),
    )


def panel(name: str, title: str, *, target: Target, description: str = "", group: str = "", args: Sequence[str] = ()) -> WorkbenchSpec:
    """A dock card.

    Args:
        name: The card's unique name, which the command line opens it by.
        title: The title the card and the title menu show.
        target: A module-level function returning the dock Split, or its `pkg.module:attr` name.
        description: The one-line summary under the title.
        group: The catalogue group the card is listed under.
        args: The arguments the card's command passes to the workbench command line.
    """
    return WorkbenchSpec(
        name=name,
        title=title,
        kind=WorkbenchKind.PANEL,
        target=target_of(target),
        description=description,
        group=group,
        args=tuple(args),
    )


def renderable(name: str, title: str, *, target: Target, description: str = "", group: str = "", args: Sequence[str] = ()) -> WorkbenchSpec:
    """A viewer card, rebuilt on save.

    Args:
        name: The card's unique name, which the command line opens it by.
        title: The title the card and the title menu show.
        target: A module-level function returning the Renderable, or its `pkg.module:attr` name.
        description: The one-line summary under the title.
        group: The catalogue group the card is listed under.
        args: The arguments the card's command passes to the workbench command line.
    """
    return WorkbenchSpec(
        name=name,
        title=title,
        kind=WorkbenchKind.RENDERABLE,
        target=target_of(target),
        description=description,
        group=group,
        args=tuple(args),
    )


def legacy(module: str) -> WorkbenchSpec:
    """The spec of a module following the old `main() -> Renderable` contract."""
    name = module.rsplit(".", 1)[-1]

    return renderable(name, name.replace("_", " "), target=f"{module}:{LEGACY_ATTR}", description=f"{module}.main()")


DEMO = panel(
    "demo",
    "Demo",
    target="femforge_workbench.demo:workbench",
    description="A sphere, a line figure and a grouped table; exercises the shell without lab data.",
    group=BUILTIN_GROUP,
)

LAUNCHER = panel(
    "launcher",
    "Launcher",
    target="femforge_workbench.launcher:workbench",
    description="Every catalogue card: open docks and previews, run scripts, browse what they produced.",
    group=BUILTIN_GROUP,
)

BROWSERS = panel(
    "browsers",
    "Runs and Artifacts",
    target="femforge_workbench.panels.browsers:workbench",
    description="Browse the telemetry indexes: runs with their stages, results, files and retention; artifacts with their records and specs.",
    group=BUILTIN_GROUP,
)

FIELD_VIEWER = panel(
    "field-viewer",
    "Field Viewer",
    target="femforge_workbench.panels.field_viewer:workbench",
    description="A solved run's fields: the 3D scene, the plotly 2D view, colormaps, body visibility and field lines.",
    group=BUILTIN_GROUP,
)

MONITORS = panel(
    "monitors",
    "Live Monitors",
    target="femforge_workbench.panels.monitors:workbench",
    description="Watch a run's heartbeat, stages, convergence and logs; a sweep's progress and failures; the live log stream; a terminal.",
    group=BUILTIN_GROUP,
)

BUILTIN: tuple[WorkbenchSpec, ...] = (LAUNCHER, DEMO, BROWSERS, FIELD_VIEWER, MONITORS)
"""The specs discover() carries without an entry point."""


def discover(entry_points: Sequence[EntryPointLike] | None = None) -> list[WorkbenchSpec]:
    """Every spec: the built-ins plus what each entry point in the group provides (a spec or a sequence), sorted by group and title; a name given two different specs is an error."""
    points: Sequence[EntryPointLike] = entry_points if entry_points is not None else list(metadata.entry_points(group=ENTRY_POINT_GROUP))
    specs = list(BUILTIN)

    def provided(point: EntryPointLike) -> list[WorkbenchSpec]:
        loaded = point.load()
        kind = type(loaded).__name__
        if isinstance(loaded, WorkbenchSpec):
            return [loaded]

        if isinstance(loaded, Sequence):
            items = cast("Sequence[object]", loaded)
            if all(isinstance(item, WorkbenchSpec) for item in items):
                return [cast("WorkbenchSpec", item) for item in items]

        raise TypeError(f"entry point {point.name!r} in {ENTRY_POINT_GROUP} must provide a WorkbenchSpec or a sequence of them, got {kind}")

    for point in points:
        for spec in provided(point):
            same_name = next((known for known in specs if known.name == spec.name), None)
            if same_name is None:
                specs.append(spec)
                continue

            if same_name != spec:
                raise ValueError(f"workbench name {spec.name!r} is provided twice with different specs ({same_name.target} and {spec.target})")

    return sorted(specs, key=lambda spec: (spec.group, spec.title))
