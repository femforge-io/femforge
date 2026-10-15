"""The browsers workbench: the run index over one run's detail, beside the artifact index over one artifact's detail."""

import os
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import polars as pl

from femforge.artifacts.local import LocalArtifactStore
from femforge.telemetry.paths import Telemetry, require_root
from femforge.telemetry.schemas import ArtifactColumn
from femforge_workbench.dock import Panel, Split
from femforge_workbench.panels.artifacts import ArtifactBrowserPanel, ArtifactDetailPanel
from femforge_workbench.panels.runs import RunBrowserPanel, RunDetailPanel
from femforge_workbench.registry import BROWSERS


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


SPEC = BROWSERS
"""The catalogue card this workbench is opened by."""

STORE_ENV = "FEMFORGE_ARTIFACTS"
"""The environment variable naming the artifact store when the index does not name one."""


class BrowserPanel(StrEnum):
    """The four panel ids of the browsers dock."""

    RUNS = "runs"
    RUN_DETAIL = "run_detail"
    ARTIFACTS = "artifacts"
    ARTIFACT_DETAIL = "artifact_detail"


def telemetry() -> Telemetry:
    """The telemetry root the browsers read, from ``--telemetry`` or the environment; [`TelemetryRootMissing`][femforge.telemetry.paths.TelemetryRootMissing] names both when neither is set."""
    return Telemetry(require_root())


def artifact_store(store: Telemetry) -> LocalArtifactStore:
    """The store the artifact panels read: ``FEMFORGE_ARTIFACTS`` when set, else the root the index rows name."""
    named: str | None = os.environ.get(STORE_ENV)
    if named:
        return LocalArtifactStore(named)

    roots: pl.DataFrame = store.artifacts.scan().select(ArtifactColumn.STORE_ROOT).drop_nulls().unique().sort(ArtifactColumn.STORE_ROOT).collect()
    found: list[str] = cast("list[str]", roots[ArtifactColumn.STORE_ROOT].to_list())

    return LocalArtifactStore(found[0] if found else store.root)


def workbench() -> Split:
    """The dock: runs over the run detail on the left, artifacts over the artifact detail on the right."""
    store = telemetry()
    artifacts = artifact_store(store)

    def runs(app: "Workbench", id: str, title: str) -> RunBrowserPanel:
        return RunBrowserPanel(app, id, title, telemetry=store)

    def run_detail(app: "Workbench", id: str, title: str) -> RunDetailPanel:
        return RunDetailPanel(app, id, title, telemetry=store, store=artifacts)

    def artifact_browser(app: "Workbench", id: str, title: str) -> ArtifactBrowserPanel:
        return ArtifactBrowserPanel(app, id, title, telemetry=store, store=artifacts)

    def artifact_detail(app: "Workbench", id: str, title: str) -> ArtifactDetailPanel:
        return ArtifactDetailPanel(app, id, title, telemetry=store, store=artifacts)

    return Split(
        [
            Split([Panel("Runs", runs, id=BrowserPanel.RUNS, weight=1.4), Panel("Run", run_detail, id=BrowserPanel.RUN_DETAIL)]),
            Split([Panel("Artifacts", artifact_browser, id=BrowserPanel.ARTIFACTS), Panel("Artifact", artifact_detail, id=BrowserPanel.ARTIFACT_DETAIL)]),
        ]
    )
