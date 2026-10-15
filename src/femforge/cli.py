"""The ``femforge`` command: start a project, browse and copy out artifacts, purge runs, read the telemetry store, build Docker images and check the machine."""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
from typing import Iterable, cast

from rich.console import Console
from rich.json import JSON
from rich.table import Table

from femforge import doctor, scaffold
from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.local import LocalArtifactStore, StoredArtifact, artifact_dir_of, store_root_of
from femforge.artifacts.sidecar import ArtifactRef
from femforge.artifacts.store import ArtifactError
from femforge.common.cli import CliError
from femforge.common.files import BundleFile
from femforge.elmer.runtime.docker_image import ELMER_IMAGE
from femforge.execution.docker import image
from femforge.execution.docker.cli import DockerError
from femforge.run import purge
from femforge.run.artifacts import rehydrate_bundle
from femforge.run.manifest import RunManifest
from femforge.telemetry import cli as telemetry


ROOT_ENV = "FEMFORGE_ARTIFACTS"
"""The environment variable naming the local store root when ``--root`` is not given."""

PIPED_WIDTH = 200
"""Console width when stdout is not a terminal, so piped tables keep their columns."""

IMAGES = (ELMER_IMAGE,)
"""The Docker images ``femforge image`` builds, the default first."""


def resolve_root(root: str | None, ref: str | None = None) -> Path:
    """The store root: ``--root``, else ``FEMFORGE_ARTIFACTS``, else the store the ``ref`` path lies inside."""
    if root:
        return Path(root)

    env: str | None = os.environ.get(ROOT_ENV)
    if env:
        return Path(env)

    inferred: Path | None = store_root_of(Path(ref)) if ref else None
    if inferred is None:
        raise CliError(f"no artifact store given: pass --root or set {ROOT_ENV}")

    return inferred


def locate(store: LocalArtifactStore, ref: str) -> list[StoredArtifact]:
    """The artifacts ``ref`` names; a path anywhere inside an artifact directory names that artifact."""
    inside: Path | None = artifact_dir_of(Path(ref))
    return store.locate(str(inside) if inside is not None else ref)


def locate_one(store: LocalArtifactStore, ref: str) -> StoredArtifact:
    """Exactly one artifact for ``ref``, or a ``CliError`` naming none or the several."""
    matches: list[StoredArtifact] = locate(store, ref)
    if not matches:
        raise CliError(f"{ref}: no artifact in {store.root}")

    if len(matches) > 1:
        names: str = ", ".join(f"{match.kind}/{match.name}" for match in matches)
        raise CliError(f"{ref} is ambiguous: {names}")

    return matches[0]


def listing_table(artifacts: Iterable[StoredArtifact], root: Path) -> Table:
    """A table of ``artifacts``, one row each, titled with the store ``root``."""
    table = Table(title=f"Artifacts under {root}")
    table.add_column("Kind")
    table.add_column("Name")
    table.add_column("Key")
    table.add_column("Created")
    table.add_column("Files", justify="right")
    table.add_column("MB", justify="right")
    table.add_column("Spec")

    for artifact in artifacts:
        record = artifact.record
        table.add_row(
            record.kind,
            artifact.name,
            record.key[:12],
            record.created_at.strftime("%Y-%m-%d %H:%M"),
            str(len(record.files)),
            f"{record.total_bytes / 1e6:.1f}",
            record.spec_type.rsplit(":", 1)[-1],
        )

    return table


def files_table(artifact: StoredArtifact) -> Table:
    """A table of one artifact's files with their sizes and hash prefixes."""
    table = Table(title=f"{artifact.kind}/{artifact.name}")
    table.add_column("File")
    table.add_column("Bytes", justify="right")
    table.add_column("sha256")

    for entry in artifact.record.files:
        table.add_row(entry.name, str(entry.size), entry.sha256[:12])

    return table


def refs_table(refs: Iterable[ArtifactRef], store: LocalArtifactStore, title: str) -> Table:
    """A bundle's store-sourced inputs, each resolved to its store directory when the store still holds it.

    Args:
        refs: The references the bundle's manifest keeps.
        store: The store to resolve them in.
        title: The table title.
    """
    table = Table(title=title)
    table.add_column("Kind")
    table.add_column("Name")
    table.add_column("Key")
    table.add_column("Origin")
    table.add_column("Files", justify="right")
    table.add_column("Store path")

    for ref in refs:
        matches: list[StoredArtifact] = store.locate(ref.key)
        location: str = str(matches[0].path) if matches else "not in store"
        table.add_row(
            ref.kind,
            ref.name,
            ref.key[:12],
            ref.origin.value,
            str(len(ref.files)),
            location,
        )

    return table


def show(store: LocalArtifactStore, ref: str, console: Console, *, spec: bool = False) -> None:
    """Print an artifact's record and files (its spec on request); a run bundle, in the store or not, also gets its manifest's inputs.

    Args:
        store: The store to look in.
        ref: A key, key prefix, name, artifact or bundle directory, or a path inside an artifact.
        console: Where to print.
        spec: Also print the hashed spec payload.
    """
    matches: list[StoredArtifact] = locate(store, ref)
    bundle = Path(ref)
    if not matches and (bundle / BundleFile.MANIFEST).is_file():
        manifest: RunManifest = RunManifest.load(bundle)
        console.print(f"bundle {manifest.run_id}  mesh {manifest.mesh_name} ({manifest.mesh_key[:12]})")
        console.print(refs_table(manifest.artifacts, store, "Store-sourced inputs"))
        return

    if not matches:
        raise CliError(f"{ref}: no artifact in {store.root}")

    for artifact in matches:
        record = artifact.record
        tools = record.tools
        console.print(f"{artifact.kind}/{artifact.name}  {artifact.path}")
        console.print(f"key {record.key}")
        console.print(f"created {record.created_at:%Y-%m-%d %H:%M:%S} UTC on {record.producer_host} pid {record.producer_pid}")
        console.print(f"tools femforge {tools.femforge}, code {tools.code_sha}, gmsh {tools.gmsh}, elmer {tools.elmer}")
        console.print(files_table(artifact))
        console.print(f"spec {record.spec_type}")
        if spec:
            console.print(JSON.from_data(record.spec))

        if (artifact.path / BundleFile.MANIFEST).is_file():
            manifest = RunManifest.load(artifact.path)
            console.print(refs_table(manifest.artifacts, store, "Store-sourced inputs"))


def rehydrate(store: LocalArtifactStore, bundle: Path, console: Console) -> None:
    """Fetch every store-sourced input a bundle's manifest names back into the bundle directory.

    Args:
        store: The store to fetch from.
        bundle: The run bundle directory.
        console: Where to report each artifact fetched.
    """
    if not (bundle / BundleFile.MANIFEST).is_file():
        raise CliError(f"{bundle}: not a run bundle (no {BundleFile.MANIFEST})")

    for handle in rehydrate_bundle(bundle, ArtifactCache(store)):
        console.print(f"{handle.kind.name}/{handle.key.name}: {', '.join(handle.files)}")

    if artifact_dir_of(bundle) is not None:
        console.print("The bundle lives in the store: the rehydrated files sit beside its copy and are not part of the artifact.")


def build_parser() -> argparse.ArgumentParser:
    """The ``femforge`` argument parser with every subcommand registered."""
    parser = argparse.ArgumentParser(
        prog="femforge",
        description=(
            "new starts a project, artifacts browses an artifact store, purge trims run bundles, index, runs, logs, sweeps and telemetry read the telemetry store, "
            "image manages the Docker images, and doctor checks this machine."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scaffold.add_arguments(commands.add_parser(scaffold.COMMAND, help="Start a project: one runnable script, its README and a .gitignore."))

    artifacts = commands.add_parser("artifacts", help="Browse a local artifact store by name, key, or path.")
    actions = artifacts.add_subparsers(dest="action", required=True)
    root_help: str = f"The store root (default: ${ROOT_ENV}, else the store a path argument lies inside)."

    ls = actions.add_parser("ls", help="List every committed artifact.")
    _ = ls.add_argument("--root", default=None, help=root_help)
    _ = ls.add_argument("--kind", default=None, help="Only this kind directory.")
    _ = ls.add_argument("--telemetry", default=None, help=telemetry.ROOT_HELP)

    show_parser = actions.add_parser("show", help="Print an artifact's record, files, spec, and (for a bundle) its inputs.")
    _ = show_parser.add_argument("ref", help="A key, key prefix, name, artifact directory, or a path inside one.")
    _ = show_parser.add_argument("--spec", action="store_true", help="Also print the hashed spec payload.")
    _ = show_parser.add_argument("--root", default=None, help=root_help)

    path = actions.add_parser("path", help="Print the store directory of an artifact.")
    _ = path.add_argument("ref", help="A key, key prefix, name, artifact directory, or a path inside one.")
    _ = path.add_argument("--root", default=None, help=root_help)

    materialize = actions.add_parser("materialize", help="Copy an artifact's files into a directory.")
    _ = materialize.add_argument("ref", help="A key, key prefix, name, artifact directory, or a path inside one.")
    _ = materialize.add_argument("--into", required=True, help="The directory to write into.")
    _ = materialize.add_argument("--verify", action="store_true", help="Check every file's sha256 while copying.")
    _ = materialize.add_argument("--root", default=None, help=root_help)

    rehydrate_parser = actions.add_parser("rehydrate", help="Put a bundle's store-sourced inputs (STEP, mesh) back beside it.")
    _ = rehydrate_parser.add_argument("bundle", help="A run bundle directory.")
    _ = rehydrate_parser.add_argument("--root", default=None, help=root_help)

    purge.add_arguments(commands.add_parser("purge", help="Apply a retention policy to run bundles (dry run unless --apply)."))
    telemetry.add_arguments(commands)
    image.add_arguments(commands.add_parser(image.COMMAND, help="Print, tag, pull, build, or version the Docker images femforge runs."), IMAGES)
    doctor.add_arguments(commands.add_parser(doctor.COMMAND, help="Check that this machine can mesh and solve, and name the fixes."))

    return parser


def run(args: argparse.Namespace, console: Console) -> int:
    """Dispatch one parsed command."""
    command: str = cast("str", args.command)
    if command == scaffold.COMMAND:
        return scaffold.run(args)

    if command == "purge":
        return purge.run(args)

    if command in telemetry.TelemetryCommand:
        return telemetry.run(args, console)

    if command == image.COMMAND:
        return image.run(args, IMAGES)

    if command == doctor.COMMAND:
        return doctor.run(args, console, os.environ.get(ROOT_ENV))

    ref: str | None = cast("str | None", getattr(args, "ref", None)) or cast("str | None", getattr(args, "bundle", None))
    store = LocalArtifactStore(resolve_root(cast("str | None", args.root), ref))
    action: str = cast("str", args.action)

    if action == "ls":
        kind = cast("str | None", args.kind)
        indexed: Table | None = telemetry.indexed_artifacts(store, kind, cast("str | None", args.telemetry))
        console.print(indexed if indexed is not None else listing_table(store.scan(kind), store.root))
    elif action == "show":
        show(store, cast("str", ref), console, spec=cast("bool", args.spec))
    elif action == "path":
        print(locate_one(store, cast("str", ref)).path)
    elif action == "materialize":
        into = Path(cast("str", args.into))
        copied: list[Path] = store.materialize(locate_one(store, cast("str", ref)), into, verify=cast("bool", args.verify))
        console.print(f"{len(copied)} files into {into}")
    else:
        rehydrate(store, Path(cast("str", ref)), console)

    return 0


def console() -> Console:
    """A stdout console that survives a cp1252 Windows console and keeps tables whole when piped."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")

    return Console(width=None if sys.stdout.isatty() else PIPED_WIDTH)


def main(argv: list[str] | None = None) -> int:
    """Run the ``femforge`` command on ``argv`` (the process arguments when None) and return its exit code."""
    args: argparse.Namespace = build_parser().parse_args(argv)
    try:
        return run(args, console())
    except (CliError, ArtifactError, DockerError) as error:
        print(f"femforge: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
