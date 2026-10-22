"""The artifact detail panel reads, materializes, rehydrates and reveals stored artifacts, and the browsers dock names its store."""

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRecord, ArtifactRef
from femforge.common.files import BundleFile
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.run.bundle import write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.result import RunStatus
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import RUN_INDEX_SCHEMA


pytest.importorskip("trame")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import StateVar as AppVar, Workbench  # noqa: E402
from femforge_workbench.grid import Row  # noqa: E402
from femforge_workbench.panels import Shared  # noqa: E402
from femforge_workbench.panels.artifacts import (  # noqa: E402
    ArtifactBrowserPanel,
    ArtifactDetailPanel,
    ArtifactRow,
    DetailVar,
    FileColumn,
    PairField,
    RefColumn,
    open_folder,
)
from femforge_workbench.panels.browsers import STORE_ENV, artifact_store  # noqa: E402
from femforge_workbench.persist import LocalJsonStore  # noqa: E402


FIXED_DT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
RUN_ID = "bright-otter-11111111"
OTHER_RUN = "calm-otter-22222222"
BUNDLE_KEY = "e" * 64
PLAIN_KEY = "f" * 64
STEP_KEY = "d" * 64


class _Spec(BaseModel):
    a: int = 1


KIND = ArtifactKind(name="run_bundle", spec_type=_Spec)


def _publish(store: LocalArtifactStore, key: str, manifest: str) -> Path:
    typed = ArtifactKey[_Spec](key)
    staging = store.staging_dir(KIND, typed)
    _ = (staging / BundleFile.MANIFEST).write_text(manifest, encoding="utf-8")
    record = ArtifactRecord.build(kind=KIND.name, key=key, producer_version=1, spec_type="t:_Spec", spec={"a": 1}, workdir=staging, files=[BundleFile.MANIFEST])
    assert store.put(KIND, typed, staging, record)

    return store.path(KIND, typed)


def _manifest(*refs: ArtifactRef) -> str:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    manifest = RunManifest.build(config, status=RunStatus.OK, run_id=RUN_ID, created_at=FIXED_DT)

    return manifest.model_copy(update={"artifacts": list(refs)}).model_dump_json()


def _telemetry(root: Path) -> Telemetry:
    names = RUN_INDEX_SCHEMA.names()
    row: dict[str, object] = {name: None for name in names}
    row.update(
        month=FIXED_DT.date().replace(day=1),
        run_id=OTHER_RUN,
        runs_dir=str(root),
        status="ok",
        physics="thermal",
        created_at=FIXED_DT,
        indexed_at=FIXED_DT,
        stages_done=3,
        source="scan",
        artifact_keys=[BUNDLE_KEY],
        mesh_key="other",
    )
    telemetry = Telemetry(root / "telemetry")
    _ = telemetry.runs.append(pl.DataFrame([row], schema=RUN_INDEX_SCHEMA))

    return telemetry


def _detail(name: str, root: Path) -> ArtifactDetailPanel:
    app = Workbench(name, title="Artifacts", server=get_server(f"artifacts-{name}"), persist=LocalJsonStore(root), user="tester")

    return ArtifactDetailPanel(app, "artifact_detail", "Artifact", telemetry=_telemetry(root), store=LocalArtifactStore(root / "store"))


def _status(panel: ArtifactDetailPanel) -> str:
    return str(panel.state[AppVar.STATUS])


def test_a_run_bundle_artifact_shows_its_provenance_inputs_and_users(tmp_path: Path) -> None:
    panel = _detail("load", tmp_path)
    ref = ArtifactRef(kind="step", key=STEP_KEY, origin=ArtifactOrigin.LOCAL, files=["part.step"])
    home = _publish(panel.store, BUNDLE_KEY, _manifest(ref))

    panel.publish(panel.load(home))

    meta = cast("list[Row]", panel.state[panel.key(DetailVar.META)])
    files = cast("list[Row]", panel.state[panel.key(DetailVar.FILES)])
    refs = cast("list[Row]", panel.state[panel.key(DetailVar.REFS)])
    assert [row[PairField.LABEL] for row in meta][:3] == ["Created", "Producer", "Spec Type"]
    assert [row[FileColumn.NAME] for row in files] == [BundleFile.MANIFEST]
    assert refs[0][RefColumn.PATH] == "not in store" and refs[0][RefColumn.FILES] == 1
    assert panel.state[panel.key(DetailVar.USES)] == [OTHER_RUN]
    assert panel.state[panel.key(DetailVar.RUN_ID)] == RUN_ID

    panel.open_run()
    assert panel.state[Shared.SELECTED_RUN_ID] == RUN_ID

    panel.open_use(OTHER_RUN)
    assert panel.state[Shared.SELECTED_RUN_ID] == OTHER_RUN


def test_rehydrate_reports_a_missing_input_and_counts_what_it_fetched(tmp_path: Path) -> None:
    panel = _detail("rehydrate", tmp_path)
    ref = ArtifactRef(kind="step", key=STEP_KEY, origin=ArtifactOrigin.LOCAL, files=["part.step"])
    missing = _publish(panel.store, BUNDLE_KEY, _manifest(ref))
    plain = _publish(panel.store, PLAIN_KEY, _manifest())

    _ = panel.load(missing)
    panel.rehydrate()
    assert _status(panel).startswith("ArtifactMissing:")

    _ = panel.load(plain)
    panel.rehydrate()
    assert _status(panel) == f"rehydrated 0 files into {plain.name}"


def test_materialize_copies_the_files_and_reports_a_corrupt_copy(tmp_path: Path) -> None:
    panel = _detail("materialize", tmp_path)
    home = _publish(panel.store, PLAIN_KEY, _manifest())
    into = tmp_path / "out"
    _ = panel.load(home)

    panel.open_dialog()
    assert panel.state[panel.key(DetailVar.DIALOG)] is True

    panel.state[panel.key(DetailVar.INTO)] = str(into)
    panel.materialize()

    assert (into / BundleFile.MANIFEST).is_file()
    assert panel.state[panel.key(DetailVar.DIALOG)] is False
    assert _status(panel) == f"1 files into {into}"

    with (home / BundleFile.MANIFEST).open("a", encoding="utf-8") as file:
        _ = file.write(" ")

    panel.materialize()

    assert _status(panel).startswith("ArtifactCorrupt:")


def test_actions_without_an_artifact_ask_for_one_first(tmp_path: Path) -> None:
    panel = _detail("empty", tmp_path)
    actions: list[Callable[[], None]] = [panel.reveal, panel.open_dialog, panel.materialize, panel.rehydrate]

    for action in actions:
        panel.state[AppVar.STATUS] = ""
        action()

        assert _status(panel) == "select an artifact first"

    panel.open_run()

    assert _status(panel) == "this artifact is not a run bundle"
    assert panel.state[panel.key(DetailVar.DIALOG)] is False


def test_the_dialog_follows_the_client(tmp_path: Path) -> None:
    panel = _detail("dialog", tmp_path)

    panel.on_dialog(True)
    assert panel.state[panel.key(DetailVar.DIALOG)] is True

    panel.close_dialog()
    assert panel.state[panel.key(DetailVar.DIALOG)] is False


def test_show_publishes_a_selection_and_reports_an_unreadable_one(tmp_path: Path) -> None:
    panel = _detail("show", tmp_path)
    home = _publish(panel.store, PLAIN_KEY, _manifest())
    broken = _publish(panel.store, BUNDLE_KEY, "not json")

    async def run() -> None:
        await panel.on_selected()
        panel.state[Shared.SELECTED_ARTIFACT_PATH] = str(home)
        await panel.on_selected()
        await panel.show(broken)

    asyncio.run(run())

    assert panel.state[panel.key(DetailVar.TITLE)] == f"{KIND.name}/{home.name}"
    assert _status(panel).startswith("ValidationError:")


def test_reveal_opens_the_artifact_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _detail("reveal", tmp_path)
    home = _publish(panel.store, PLAIN_KEY, _manifest())
    opened: list[object] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    _ = panel.load(home)

    panel.reveal()

    assert opened == [home]


@pytest.mark.parametrize(("platform", "opener"), [("darwin", "open"), ("linux", "xdg-open")])
def test_open_folder_runs_the_desktop_opener_on_the_parent_of_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, opener: str) -> None:
    launched: list[list[str]] = []
    file = tmp_path / "a.txt"
    _ = file.write_text("a", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(subprocess, "Popen", launched.append)

    open_folder(file)

    assert launched == [[opener, str(tmp_path)]]


def test_open_folder_logs_a_failure_rather_than_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_: list[str]) -> None:
        raise OSError("no opener")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", refuse)

    open_folder(tmp_path)


def test_a_pick_without_a_path_selects_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = Workbench("pick", title="Artifacts", server=get_server("artifacts-pick"), persist=LocalJsonStore(tmp_path), user="tester")
    telemetry = _telemetry(tmp_path)
    monkeypatch.setenv(STORE_ENV, str(tmp_path / "store"))
    browser = ArtifactBrowserPanel(app, "artifacts", "Artifacts", telemetry=telemetry, store=artifact_store(telemetry))

    browser.on_pick({ArtifactRow.NAME: "nameless"})

    assert app.state[Shared.SELECTED_ARTIFACT_PATH] in (None, "")


def test_the_store_env_names_the_artifact_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STORE_ENV, str(tmp_path / "named"))

    assert artifact_store(Telemetry(tmp_path / "telemetry")).root == tmp_path / "named"
