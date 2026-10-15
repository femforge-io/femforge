"""The femforge-workbench command: parses its flags and shows the launcher, a catalogue entry or a module through serve.show."""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from femforge.common.log import TELEMETRY_ENV, LogLevel, setup_logging
from femforge.telemetry.paths import TelemetryRootMissing
from femforge_workbench.app import Window
from femforge_workbench.dev import DEV_FLAG, supervise
from femforge_workbench.registry import CLI_MODULE, LAUNCHER, WorkbenchSpec, discover, legacy, panel, split_target
from femforge_workbench.serve import DEFAULT_PORT, NotAWorkbench, show


class CliError(Exception):
    """A user-facing failure: main prints the message and exits 2."""


@dataclass
class Options:
    """The parsed command line."""

    name: str | None = None
    module: str | None = None
    app: bool = False
    webview: bool = False
    dev: bool = False
    port: int = DEFAULT_PORT
    watch: list[str] = field(default_factory=list)
    telemetry: str | None = None
    persist: str | None = None

    @property
    def window(self) -> Window:
        """The window the flags ask for: an app window, pywebview, else a browser tab."""
        if self.app:
            return Window.APP

        if self.webview:
            return Window.WEBVIEW

        return Window.BROWSER


def select(name: str | None, module: str | None) -> WorkbenchSpec:
    """The spec to open: a catalogue entry by name, a module given as `pkg.module[:attr]` (main() -> Renderable when no attr), else the launcher."""
    if module:
        module_name, attr = split_target(module)
        if attr is None:
            return legacy(module_name)

        return panel(attr, attr.replace("_", " "), target=module, description=module)

    if not name:
        return LAUNCHER

    catalogue = discover()
    spec = next((spec for spec in catalogue if spec.name == name), None)
    if spec is None:
        names = ", ".join(spec.name for spec in catalogue) or "none"
        raise CliError(f"unknown workbench {name!r}; known: {names}")

    return spec


def build_parser() -> argparse.ArgumentParser:
    """The ``femforge-workbench`` argument parser with every flag registered."""
    parser = argparse.ArgumentParser(prog="femforge-workbench", description="Open a workbench in the trame shell.")
    _ = parser.add_argument("name", nargs="?", help="a catalogue entry, e.g. demo")
    _ = parser.add_argument("-m", "--module", help="pkg.module[:attr]: attr returns a dock Split or a Renderable; without attr, main() -> Renderable")
    _ = parser.add_argument("--app", action="store_true", help="open a Chrome/Edge app window and stop the server when it closes")
    _ = parser.add_argument("--webview", action="store_true", help="open in pywebview (needs the app extra)")
    _ = parser.add_argument(DEV_FLAG, action="store_true", help="run the server as a child restarted on every save under the shell package; the page reloads itself")
    _ = parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    _ = parser.add_argument("--watch", action="append", default=[], metavar="DIR", help="extra directory to watch and purge when a workbench is rebuilt (repeatable)")
    _ = parser.add_argument("--telemetry", default=None, metavar="DIR", help=f"the telemetry root the browsers read (default: ${TELEMETRY_ENV})")
    _ = parser.add_argument("--persist", default=None, metavar="DIR", help="where layouts and choices are saved (default: the per-user config directory)")

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Parses the command line and shows the chosen workbench, or supervises the server under --dev."""
    parser = build_parser()
    options, _ = parser.parse_known_args(argv, Options())

    setup_logging(level=LogLevel.INFO)

    if options.dev:
        supervise(CLI_MODULE, sys.argv[1:] if argv is None else argv)
        return

    try:
        spec = select(options.name, options.module)
        show(spec, window=options.window, port=options.port, telemetry=options.telemetry, persist=options.persist, watch=options.watch)
    except (CliError, NotAWorkbench, TelemetryRootMissing) as error:
        parser.exit(2, f"femforge-workbench: {error}\n")


if __name__ == "__main__":
    main()
