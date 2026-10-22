"""The run panels: result scalars, the live poll, and the detail actions with and without a shown run."""

import asyncio
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRef
from femforge.common.files import BundleFile
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.frames import Solid
from femforge.meshing.config import MeshConfig
from femforge.physical.units import quantity
from femforge.run.bundle import write_bundle, write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.result import Result, RunStatus
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import RUN_INDEX_SCHEMA, RunColumn


pytest.importorskip("trame")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Hook, StateVar as AppVar, Workbench  # noqa: E402
from femforge_workbench.grid import Row  # noqa: E402
from femforge_workbench.panels import Shared  # noqa: E402
from femforge_workbench.panels.artifacts import PairField  # noqa: E402
from femforge_workbench.panels.runs import DetailVar, PurgeVar, RunBrowserPanel, RunDetailPanel, live_ids, scalar_rows  # noqa: E402
from femforge_workbench.persist import LocalJsonStore  # noqa: E402


FIXED_DT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
RUN_ID = "bright-otter-11111111"
BROKEN_ID = "calm-otter-22222222"
CONFIG = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))


def _telemetry(root: Path) -> Telemetry:
    def row(run_id: str) -> dict[str, object]:
        filled: dict[str, object] = {name: None for name in RUN_INDEX_SCHEMA.names()}
        filled.update(
            month=FIXED_DT.date().replace(day=1),
            run_id=run_id,
            runs_dir=str(root),
            status="ok",
            physics="thermal",
            created_at=FIXED_DT,
            indexed_at=FIXED_DT,
            stages_done=3,
            source="scan",
            artifact_keys=[],
        )

        return filled

    telemetry = Telemetry(root / "telemetry")
    _ = telemetry.runs.append(pl.DataFrame([row(RUN_ID), row(BROKEN_ID)], schema=RUN_INDEX_SCHEMA))

    return telemetry


def _app(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Runs", server=get_server(f"runs-{name}"), persist=LocalJsonStore(root), user="tester")


def _detail(name: str, root: Path) -> RunDetailPanel:
    manifest = RunManifest.build(CONFIG, status=RunStatus.OK, run_id=RUN_ID, created_at=FIXED_DT)
    _ = write_bundle(root / RUN_ID, manifest, Result(frame=Solid(), status=RunStatus.OK))
    (root / BROKEN_ID).mkdir()
    _ = (root / BROKEN_ID / BundleFile.MANIFEST).write_text("not json", encoding="utf-8")

    return RunDetailPanel(_app(name, root), "run_detail", "Run", telemetry=_telemetry(root), store=LocalArtifactStore(root / "store"))


def _status(panel: RunDetailPanel) -> str:
    return str(panel.state[AppVar.STATUS])


def test_scalar_rows_list_every_scalar_the_result_carries() -> None:
    result = Result(
        frame=Solid(),
        status=RunStatus.OK,
        residual=1.25e-9,
        flux_density_max=quantity(1.23456, "tesla"),
        temperature_max=quantity(330.123, "kelvin"),
        force=quantity(Vec3(1.0, -2.0, 0.5), "newton"),
    )

    rows = {str(row[PairField.LABEL]): row[PairField.VALUE] for row in scalar_rows(result)}

    assert rows["Residual"] == "1.25e-09"
    assert rows["|B| Max [T]"] == 1.2346
    assert rows["T Max [K]"] == 330.12
    assert rows["Force [N]"] == "(1, -2, 0.5)"


def test_live_ids_are_empty_without_the_stale_column() -> None:
    assert live_ids(pl.DataFrame({RunColumn.RUN_ID: ["a"]})) == set()


def test_the_poll_refreshes_once_a_claim_goes_away_and_a_blank_pick_selects_nothing(tmp_path: Path) -> None:
    app = _app("poll", tmp_path)
    browser = RunBrowserPanel(app, "runs", "Runs", telemetry=_telemetry(tmp_path))
    browser.live = {RUN_ID}

    refreshed = browser.poll()

    assert refreshed is not None and refreshed.height == 2

    browser.publish(refreshed)

    assert browser.poll() is None

    browser.on_pick({})

    assert app.state[Shared.SELECTED_RUN_ID] in (None, "")


def test_actions_without_a_run_ask_for_one_first(tmp_path: Path) -> None:
    panel = _detail("empty", tmp_path)
    actions: list[Callable[[], None]] = [panel.open_fields, panel.reveal, panel.rehydrate, panel.open_purge, panel.preview_purge]

    for action in actions:
        panel.state[AppVar.STATUS] = ""
        action()

        assert _status(panel) == "select a run first"

    panel.apply_purge()

    assert _status(panel) == "preview the purge first"


def test_the_purge_dialog_follows_the_client(tmp_path: Path) -> None:
    panel = _detail("dialog", tmp_path)

    panel.on_dialog(True)
    assert panel.state[panel.key(PurgeVar.DIALOG)] is True

    panel.close_purge()
    assert panel.state[panel.key(PurgeVar.DIALOG)] is False


def test_show_reports_an_unreadable_bundle_and_drops_a_superseded_read(tmp_path: Path) -> None:
    panel = _detail("show", tmp_path)

    async def run() -> None:
        await panel.on_selected()
        await panel.show(BROKEN_ID)
        broken = _status(panel)
        _ = await asyncio.gather(panel.show("never-indexed-00000000"), panel.show(RUN_ID))
        assert broken.startswith("ValidationError:")

    asyncio.run(run())

    assert panel.state[panel.key(DetailVar.TITLE)] == RUN_ID
    assert panel.run_id == RUN_ID


def test_a_reread_of_another_run_is_dropped(tmp_path: Path) -> None:
    panel = _detail("reread", tmp_path)
    panel.publish(panel.load(RUN_ID))
    panel.run_id = RUN_ID

    panel.refresh((BROKEN_ID, panel.load(BROKEN_ID.replace("calm", "gone"))))

    assert panel.state[panel.key(DetailVar.TITLE)] == RUN_ID


def test_open_fields_hands_the_bundle_to_a_registered_viewer(tmp_path: Path) -> None:
    panel = _detail("fields", tmp_path)
    opened: list[object] = []
    panel.publish(panel.load(RUN_ID))

    panel.open_fields()
    assert opened == [], "a bundle without a VTU has no fields to open"

    panel.state[panel.key(DetailVar.HAS_FIELDS)] = True
    panel.open_fields()
    assert _status(panel) == "no field viewer in this workbench"

    panel.ctrl[Hook.FIELD_OPEN] = opened.append
    panel.open_fields()
    assert opened == [tmp_path / RUN_ID]


def test_open_ref_selects_the_input_artifact(tmp_path: Path) -> None:
    panel = _detail("ref", tmp_path)

    panel.open_ref("store/step/part")

    assert panel.state[Shared.SELECTED_ARTIFACT_PATH] == "store/step/part"


def test_reveal_opens_the_bundle_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _detail("reveal", tmp_path)
    opened: list[object] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    _ = panel.load(RUN_ID)

    panel.reveal()

    assert opened == [tmp_path / RUN_ID]


def test_rehydrate_counts_the_fetched_files_and_reports_a_failure(tmp_path: Path) -> None:
    panel = _detail("rehydrate", tmp_path)
    _ = panel.load(RUN_ID)

    panel.rehydrate()
    assert _status(panel) == f"rehydrated 0 files into {RUN_ID}"

    ref = ArtifactRef(kind="weird", key="a" * 64, origin=ArtifactOrigin.LOCAL)
    manifest = RunManifest.build(CONFIG, status=RunStatus.OK, run_id=RUN_ID, created_at=FIXED_DT)
    _ = write_manifest(tmp_path / RUN_ID, manifest.model_copy(update={"artifacts": [ref]}))
    panel.rehydrate()

    assert _status(panel).startswith("ArtifactMissing:")


def test_apply_purge_removes_the_previewed_paths_and_reloads(tmp_path: Path) -> None:
    panel = _detail("purge", tmp_path)
    panel.publish(panel.load(RUN_ID))
    panel.open_purge()
    panel.preview_purge()

    panel.apply_purge()

    assert "removed" in _status(panel)
    assert panel.plan is None
    assert panel.state[panel.key(PurgeVar.DIALOG)] is False
    assert panel.state[panel.key(DetailVar.TITLE)] == RUN_ID
    assert cast("list[Row]", panel.state[panel.key(DetailVar.META)])


def test_a_successor_with_no_selected_run_stays_blank(tmp_path: Path) -> None:
    panel = _detail("succeed", tmp_path)

    panel.succeed(panel)

    assert panel.showing is None
