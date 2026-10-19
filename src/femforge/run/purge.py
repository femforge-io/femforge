"""Post-hoc retention over existing run bundles: one bundle or a whole runs directory, dry-run by default."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable, cast

from rich.table import Table

from femforge.common.files import BundleFile, RunFile
from femforge.common.log import get_console, log
from femforge.run.bundle import write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.result import LifecycleStatus, ManifestStatus, RunStatus
from femforge.run.retention import Cleanup, CleanupRecord, CleanupReport, CleanupTrigger, Preset, RetentionPolicy, apply_cleanup, plan_cleanup, remove_run_dir


DEFAULT_RUNNING_GRACE = timedelta(hours=6)
"""How stale a ``running`` manifest must be before a purge treats the run as dead; the driver rewrites it every stage."""


class SkipReason(StrEnum):
    """Why a purge left a directory alone."""

    NOT_A_BUNDLE = "not_a_bundle"
    LINK = "link"
    UNREADABLE = "unreadable"
    RUNNING = "running"
    STATUS = "status"
    AGE = "age"
    FAILED = "failed"


@dataclass
class PurgeReport:
    """Every bundle a purge touched or skipped, with the bytes it freed."""

    runs_dir: Path
    policy: RetentionPolicy
    dry_run: bool
    reports: list[CleanupReport] = field(default_factory=list)
    skipped: list[tuple[Path, SkipReason]] = field(default_factory=list)

    @property
    def bytes_freed(self) -> int:
        """Total bytes removed, or that a dry run would remove."""
        return sum(report.bytes_freed for report in self.reports)

    @property
    def files_removed(self) -> int:
        """Total files removed, or that a dry run would remove."""
        return sum(len(report.removed) for report in self.reports)

    def bytes_by_class(self) -> dict[RunFile, int]:
        """Bytes removed per retention class."""
        totals: dict[RunFile, int] = {}
        for report in self.reports:
            for entry in report.removed:
                totals[entry.file_class] = totals.get(entry.file_class, 0) + entry.size
        return totals

    def files_by_class(self) -> dict[RunFile, int]:
        """Files removed per retention class."""
        counts: dict[RunFile, int] = {}
        for report in self.reports:
            for entry in report.removed:
                counts[entry.file_class] = counts.get(entry.file_class, 0) + 1
        return counts

    def skipped_by_reason(self) -> dict[SkipReason, int]:
        """Directories skipped per reason."""
        counts: dict[SkipReason, int] = {}
        for _, reason in self.skipped:
            counts[reason] = counts.get(reason, 0) + 1
        return counts


def cached_inputs(manifest: RunManifest) -> frozenset[str]:
    """The bundle-relative paths the manifest says came from the artifact store, which retention may drop freely."""
    return manifest.cached_inputs()


def cleanup_bundle(sim_dir: str | Path, policy: RetentionPolicy, *, dry_run: bool = False, cached: Iterable[str] | None = None) -> CleanupReport:
    """Apply ``policy`` to one finished bundle and append a [`CleanupRecord`][femforge.run.retention.CleanupRecord] to its manifest when anything left.

    Args:
        sim_dir: The run bundle directory; one without a manifest raises ``FileNotFoundError``.
        policy: The retention policy, its ``ON_SUCCESS`` rules resolved against the manifest status.
        dry_run: Report what would leave without removing anything.
        cached: Bundle-relative paths the store can re-fetch; None reads them from the manifest.
    """
    bundle: Path = Path(sim_dir)
    manifest: RunManifest = RunManifest.load(bundle)

    plan = plan_cleanup(
        bundle,
        policy,
        status=manifest.status,
        cached=cached if cached is not None else cached_inputs(manifest),
    )
    report: CleanupReport = apply_cleanup(plan, dry_run=dry_run)
    if dry_run:
        return report

    if plan.remove_run_dir:
        _ = remove_run_dir(bundle)
        return report

    if report.removed:
        manifest.cleanups.append(
            CleanupRecord(
                applied_at=datetime.now(timezone.utc),
                trigger=CleanupTrigger.PURGE,
                status=manifest.status,
                policy=policy,
                removed=report.removed,
            )
        )
        _ = write_manifest(bundle, manifest)

    return report


def _skip_reason(
    bundle: Path,
    *,
    only_status: Iterable[ManifestStatus] | None,
    older_than: timedelta | None,
    running_grace: timedelta,
    now: datetime,
) -> SkipReason | None:
    """Why ``bundle`` is left alone, or None when the purge may proceed."""
    if bundle.is_symlink() or bundle.is_junction():
        return SkipReason.LINK

    if not bundle.is_dir() or not (bundle / BundleFile.MANIFEST).is_file():
        return SkipReason.NOT_A_BUNDLE

    try:
        manifest: RunManifest = RunManifest.load(bundle)
    except Exception:  # noqa: BLE001 - a foreign or schema-drifted manifest is skipped, never rewritten blind
        return SkipReason.UNREADABLE

    if manifest.status is LifecycleStatus.RUNNING:
        touched = datetime.fromtimestamp((bundle / BundleFile.MANIFEST).stat().st_mtime, tz=timezone.utc)
        if now - touched < running_grace:
            return SkipReason.RUNNING

    if only_status is not None and manifest.status not in set(only_status):
        return SkipReason.STATUS

    if older_than is not None and now - manifest.created_at < older_than:
        return SkipReason.AGE

    return None


def purge_runs(
    runs_dir: str | Path,
    policy: RetentionPolicy,
    *,
    dry_run: bool = False,
    only_status: Iterable[ManifestStatus] | None = None,
    older_than: timedelta | None = None,
    running_grace: timedelta = DEFAULT_RUNNING_GRACE,
) -> PurgeReport:
    """Apply ``policy`` to every bundle under ``runs_dir``, skipping links, directories that are not bundles and unreadable manifests.

    Args:
        runs_dir: The runs directory holding the bundles.
        policy: The retention policy to apply.
        dry_run: Report what would leave without removing anything.
        only_status: Touch only bundles in these statuses; None touches every status.
        older_than: Touch only bundles created at least this long ago; None ignores age.
        running_grace: Skip a ``running`` bundle whose manifest was written more recently than this.
    """
    root: Path = Path(runs_dir)
    statuses: list[ManifestStatus] | None = list(only_status) if only_status is not None else None
    report = PurgeReport(runs_dir=root, policy=policy, dry_run=dry_run)
    now: datetime = datetime.now(timezone.utc)

    for bundle in sorted(root.iterdir()):
        reason: SkipReason | None = _skip_reason(
            bundle,
            only_status=statuses,
            older_than=older_than,
            running_grace=running_grace,
            now=now,
        )
        if reason is not None:
            report.skipped.append((bundle, reason))
            continue

        try:
            report.reports.append(cleanup_bundle(bundle, policy, dry_run=dry_run))
        except Exception as error:  # noqa: BLE001 - one bundle must not abort the sweep
            log.bind(bundle=bundle.name, error=str(error)).warning("Purge skipped a bundle.")
            report.skipped.append((bundle, SkipReason.FAILED))

    log.bind(
        runs_dir=str(root),
        bundles=len(report.reports),
        files=report.files_removed,
        gb_freed=round(report.bytes_freed / 1e9, 2),
        dry_run=dry_run,
    ).info("Purge finished.")
    return report


def render_report(report: PurgeReport) -> Table:
    """A rich table of bytes and files per class, plus totals and skip counts."""
    table = Table(title=f"{'Would free' if report.dry_run else 'Freed'} under {report.runs_dir}")
    table.add_column("Class")
    table.add_column("Files", justify="right")
    table.add_column("GB", justify="right")

    sizes: dict[RunFile, int] = report.bytes_by_class()
    counts: dict[RunFile, int] = report.files_by_class()

    for file_class in RunFile:
        if file_class in sizes:
            table.add_row(
                file_class.value,
                str(counts[file_class]),
                f"{sizes[file_class] / 1e9:.2f}",
            )

    table.add_section()
    table.add_row("total", str(report.files_removed), f"{report.bytes_freed / 1e9:.2f}")
    table.add_row("bundles touched", str(len(report.reports)), "")

    for reason, count in sorted(report.skipped_by_reason().items()):
        table.add_row(f"skipped: {reason.value}", str(count), "")

    return table


def _policy_from_args(args: argparse.Namespace) -> RetentionPolicy:
    policy: RetentionPolicy = RetentionPolicy.from_preset(Preset(cast("str", args.preset)))
    overrides: dict[str, Cleanup] = {}
    for name in RetentionPolicy.model_fields:
        value = cast("str | None", getattr(args, name))
        if value is not None:
            overrides[name] = Cleanup(value)
    return policy.model_copy(update=overrides) if overrides else policy


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The purge options, on a standalone parser or a ``femforge purge`` subcommand."""
    _ = parser.add_argument(
        "--runs-dir",
        required=True,
        help="The runs directory holding the bundles.",
    )
    _ = parser.add_argument(
        "--preset",
        default=Preset.DEFAULT.value,
        choices=[preset.value for preset in Preset],
        help="The policy to start from.",
    )

    for name in RetentionPolicy.model_fields:
        _ = parser.add_argument(
            "--" + name.replace("_", "-"),
            choices=[rule.value for rule in Cleanup],
            default=None,
            help=f"Override the {name} rule.",
        )

    _ = parser.add_argument(
        "--only-status",
        nargs="*",
        default=None,
        choices=[status.value for status in (*LifecycleStatus, *RunStatus)],
        help="Touch only bundles in these statuses.",
    )
    _ = parser.add_argument(
        "--older-than",
        type=float,
        default=None,
        metavar="DAYS",
        help="Touch only bundles created at least this many days ago.",
    )
    _ = parser.add_argument(
        "--running-grace",
        type=float,
        default=DEFAULT_RUNNING_GRACE.total_seconds() / 3600,
        metavar="HOURS",
        help="Leave running bundles whose manifest is younger than this.",
    )

    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument("--dry-run", action="store_true", help="Report only (the default).")
    _ = mode.add_argument("--apply", action="store_true", help="Actually delete.")


def run(args: argparse.Namespace) -> int:
    """Purge per the parsed options and print the report."""
    policy: RetentionPolicy = _policy_from_args(args)
    dry_run: bool = not cast("bool", args.apply)
    only_status = cast("list[ManifestStatus] | None", args.only_status)
    older_days = cast("float | None", args.older_than)

    report: PurgeReport = purge_runs(
        cast("str", args.runs_dir),
        policy,
        dry_run=dry_run,
        only_status=only_status,
        older_than=timedelta(days=older_days) if older_days is not None else None,
        running_grace=timedelta(hours=cast("float", args.running_grace)),
    )
    console = get_console()
    console.print(render_report(report))
    if dry_run:
        console.print("Dry run: nothing was deleted. Re-run with --apply to delete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the purge command line and return its exit code."""
    parser = argparse.ArgumentParser(description="Apply a retention policy to existing run bundles (dry run unless --apply).")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
