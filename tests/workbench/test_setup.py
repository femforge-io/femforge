"""The Setup tab: its row builders on a flexure-shaped bundle, its template bindings and the controller paths that feed it."""

import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast, get_args

import pytest

from femforge.common.files import BundleFile
from femforge.common.log import TELEMETRY_ENV
from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.elmer.sections import SifSection
from femforge.frames import Axis, Axisymmetric, BodyMismatch, Cartesian2D, FrameCheck, GeometryCheck, Solid
from femforge.materials import Steel_1080
from femforge.meshing.config import MeshConfig
from femforge.physical.conditions import BoundarySite, CalculateMagneticForce, ConditionGlyph, Convection, CurrentDensity, FixedDisplacement, FixedVoltage, Magnetization, SurfaceForce
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import quantity
from femforge.run.bundle import write_manifest
from femforge.run.conditions import ConditionRecord, ConditionsFile, GroupConditions, SifKeyword, SolveComponent
from femforge.run.entities import Bodies, Body, BodyRecord, Boundaries, Boundary, BoundaryKind, BoundaryRecord, Entities, EntitiesFile
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.result import RunStatus
from femforge.viz.glyphs import EntityKind, EntityRef, GlyphHost, body_color
from femforge.viz.symmetry import Expansion
from tests.workbench.tree import Node, find, prop, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.dock import template_name  # noqa: E402
from femforge_workbench.panels.field_viewer import SCALE_READOUT, TIMES, FieldPanel, FieldVar, FieldViewerController, FieldViewerPanel, highlightable, padded, workbench  # noqa: E402
from femforge_workbench.panels.setup import (  # noqa: E402
    FILTER_ABOVE,
    FilterChoice,
    NoticeLevel,
    RowView,
    SetupModel,
    SetupPanel,
    SetupState,
    SetupVar,
    body_columns,
    body_rows,
    boundary_columns,
    boundary_rows,
    caption_of,
    check_line,
    condition_lines,
    count_text,
    dropped_tip,
    empty_text,
    frame_facts,
    icon_of,
    legend_label,
    legend_rows,
    measure_cell,
    notice_of,
    order,
    read_setup,
    scale_text,
    tag_rows,
)
from femforge_workbench.persist import LocalJsonStore  # noqa: E402
from femforge_workbench.widgets.entity_grid import ConditionLine  # noqa: E402
from femforge_workbench.widgets.icon import IconName  # noqa: E402


FLEXURE_FRAME = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=quantity(10, "mm"))
STEEL = Steel_1080()


def _record(kind: str, condition: object, block: int, keywords: dict[str, float], *, dropped: tuple[str, ...] = ()) -> ConditionRecord:
    return ConditionRecord(
        kind=kind,
        condition=cast("FixedDisplacement", condition),
        tag=block - 1,
        selector={TagKey.NAME: "LeftFace" if block == 1 else "RightFace"},
        section=SifSection.BOUNDARY_CONDITION,
        block=block,
        block_name=f"{kind}_{block}",
        keywords=[SifKeyword(name=name, value=value) for name, value in keywords.items()],
        solve_components=[SolveComponent(index=index, value=value) for index, value in enumerate(keywords.values(), start=1)],
        dropped=list(dropped),
    )


CLAMP = _record("fixed_displacement", FixedDisplacement(), 1, {"Displacement 1": 0.0, "Displacement 2": 0.0}, dropped=("z",))
PULL = _record("surface_force", SurfaceForce(force=quantity(Vec3(100, 0, 0), "N")), 2, {"Force 1": 500000.0, "Force 2": 0.0})
CONDITIONS = ConditionsFile(
    bodies=[GroupConditions(group="FLEXURE")],
    boundaries=[GroupConditions(group="FLEXURE__EXT"), GroupConditions(group="LEFTFACE", conditions=[CLAMP]), GroupConditions(group="RIGHTFACE", conditions=[PULL])],
)
ENTITIES = EntitiesFile(
    bodies={1: BodyRecord(name="FLEXURE", material=STEEL.tag, tags={TagKey.NAME: "Flexure"}, centroid=Vec3(0, 0, 0), measure=1.448664e-3)},
    boundaries={
        2: BoundaryRecord(name="FLEXURE__EXT", bodies=("FLEXURE",), length=0.3, centroid=Vec3(0, 0, 0)),
        3: BoundaryRecord(name="LEFTFACE", bodies=("FLEXURE",), named=True, length=0.02, measure=0.02, centroid=Vec3(-0.05, 0, 0), normal=Direction(-1, 0, 0)),
        4: BoundaryRecord(name="RIGHTFACE", bodies=("FLEXURE",), named=True, length=0.02, measure=0.02, centroid=Vec3(0.05, 0, 0), normal=Direction(1, 0, 0)),
    },
)
TAGS = [
    EntityTag(selector={TagKey.NAME: "LeftFace"}, conditions=[FixedDisplacement()]),
    EntityTag(selector={TagKey.NAME: "RightFace"}, conditions=[SurfaceForce(force=quantity(Vec3(100, 0, 0), "N"))]),
    EntityTag(selector={TagKey.NAME: "Flexure"}),
]


def _flexure(root: Path, *, conditions: bool = True, entities: bool = True, check: GeometryCheck | None = None) -> Path:
    """A bundle shaped like the flexure's Cartesian 2D run, without fields or mesh."""
    root.mkdir(parents=True)
    config = SimulationConfig(
        mesh=MeshConfig(step="data/does-not-exist.step"),
        frame=FLEXURE_FRAME,
        domain=DomainConfig(materials=[STEEL], tags=TAGS),
        physics=PhysicsSelection(selected=Physics.LINEAR_ELASTICITY),
    )
    manifest = RunManifest.build(config, status=RunStatus.OK, run_id=root.name).model_copy(update={"geometry_check": check})
    _ = write_manifest(root, manifest)
    if entities:
        _ = (root / BundleFile.ENTITIES).write_bytes(ENTITIES.model_dump_json().encode())
        _ = (root / BundleFile.BOUNDARIES).write_bytes(b"1: LEFTFACE_fixed_displacement\n2: RIGHTFACE_surface_force\n")

    if conditions:
        _ = CONDITIONS.write(root)

    return root


def _model(tmp_path: Path, **kwargs: object) -> SetupModel:
    return read_setup(Run(_flexure(tmp_path / "flexure", **kwargs)))  # pyright: ignore[reportArgumentType]  # the bundle options are the fixture's own


def _texts(lines: list[ConditionLine]) -> list[str]:
    return ["".join(part.get("text", "") for part in line["parts"]) for line in lines]


def _shell(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workbench:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    app = Workbench(name, title="Fields", server=get_server(f"setup-{name}"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
    app.add(workbench())

    return app


def _controller(app: Workbench) -> FieldViewerController:
    return app.panel(FieldPanel.VIEW_3D, FieldViewerPanel).controller


def _bound(node: Node, name: str) -> object:
    value = prop(node, name)
    if isinstance(value, dict):
        return cast("dict[str, object]", value).get("js")

    return value


# --- the builders ----------------------------------------------------------------


def test_a_flexure_run_lists_one_body_and_its_boundaries_named_faces_first(tmp_path: Path) -> None:
    model = _model(tmp_path)
    bodies = body_rows(model, RowView())
    boundaries = boundary_rows(model, RowView())
    body = bodies.rows[0]

    assert model.state is SetupState.GROUPS and model.frame == FLEXURE_FRAME and not model.has_fields and not model.legacy_names
    assert [row.get("name") for row in bodies.rows] == ["Flexure"] and body.get("detail") == "1450 mm²", "a 2D body reads its section area"
    assert (body.get("material"), body.get("material_tip"), body.get("cards"), body.get("kind_icon")) == (STEEL.tag, STEEL.name, [], "pentagon")
    assert body.get("tip") == ["FLEXURE", "Body"]
    assert [row.get("id") for row in boundaries.rows] == ["boundary:LEFTFACE", "boundary:RIGHTFACE", "boundary:FLEXURE__EXT"]
    assert [row.get("name") for row in boundaries.rows] == ["LeftFace", "RightFace", "Flexure · External"], "names stay verbatim"


def test_the_rows_show_each_condition_as_its_read_only_editor_with_the_dropped_axis_muted(tmp_path: Path) -> None:
    left, right, external = boundary_rows(_model(tmp_path), RowView()).rows
    reason = "Authored Z runs along no solve axis: solve X and Y run along +X and +Y."

    assert left.get("cards") == [{"title": "Fixed Displacement", "icon": "anchor", "fields": [{"label": "Displacement", "kind": "vector", "values": ["0", "0", "0"], "axes": ["X", "Y", "Z"], "unit": "mm", "dropped": [2], "tip": reason}]}]
    assert right.get("cards") == [
        {
            "title": "Surface Force",
            "icon": "arrow-big-right-dash",
            "fields": [{"label": "Magnitude", "kind": "quantity", "value": "100", "unit": "N"}, {"label": "Direction", "kind": "vector", "values": ["1", "0", "0"], "axes": ["X", "Y", "Z"], "dropped": [2], "tip": reason}],
        }
    ], "an axis the frame has no solve coordinate for reads inactive in every vector"
    assert external.get("cards") == [] and external.get("chips") == [{"name": "Flexure", "color": body_color(0)}, {"name": "External"}], "an unnamed group reads as the bodies it touches"
    assert "chips" not in left and [row.get("kind_icon") for row in (left, right, external)] == ["minus"] * 3, "a 2D face is a line"
    assert (left.get("detail"), left.get("detail_tip")) == ("20 mm", "Face Area 200 mm² at 10 mm Thickness")
    assert left.get("tip") == ["LEFTFACE", "Named Face", "Borders Flexure", "Boundary Condition 1"]
    assert right.get("label") == (
        "RightFace, 20 mm (Face Area 200 mm² at 10 mm Thickness), Surface Force 100 N Along (1, 0, 0), Traction (500, 0) kPa, RIGHTFACE, Named Face, Borders Flexure, Boundary Condition 2"
    ), "the label reads the detail, what the solver received and every tooltip fact"
    assert not {"lines", "icon", "fit", "glyph_shown", "glyph_note"} & {*left, *right, *external}, "a row carries no glyph toggle and no fit flag"
    assert f"Z – ({reason})" in str(left.get("label")), "the label reads the reason the tooltip shows"


def test_a_row_hidden_or_without_cells_says_so(tmp_path: Path) -> None:
    model = _model(tmp_path)
    view = RowView(visible=frozenset(), highlightable=frozenset({"boundary:RIGHTFACE"}))
    body = body_rows(model, view).rows[0]
    left, right, _ = boundary_rows(model, view).rows

    assert body.get("hidden") is True, "a body the Field Viewer hides offers to show it"
    assert str(body.get("label")).endswith("Hidden in the Field Viewer; press S to show it.")
    assert left.get("dimmed") is True and left.get("dim_tip") == "Not in the mesh files: re-solve to restore the mesh."
    assert str(left.get("label")).endswith(", Not in the mesh files: re-solve to restore the mesh.")
    assert "dimmed" not in right


def test_unreadable_cells_dim_every_row_and_keep_its_cards(tmp_path: Path) -> None:
    left, right, _ = boundary_rows(_model(tmp_path), RowView(highlightable=frozenset(), unreadable=True)).rows

    assert [card.get("icon") for card in right.get("cards", [])] == ["arrow-big-right-dash"], "a row without a scene still shows what its condition draws"
    assert left.get("dimmed") is True and right.get("dim_tip") == "Highlight unavailable: the scene's groups failed to load."


def test_many_boundary_groups_start_on_those_with_conditions_and_count_the_rest(tmp_path: Path) -> None:
    flexure = _model(tmp_path)
    named = [Boundary(name=f"FACE{index:02d}", mesh_id=index, bodies=("FLEXURE",), kind=BoundaryKind.NAMED) for index in range(FILTER_ABOVE)]
    many = SetupModel(run_id="many", frame=Solid(), entities=Entities(bodies=flexure.entities.bodies, boundaries=Boundaries([*named, *flexure.entities.boundaries])), conditions=CONDITIONS)
    listed = boundary_rows(many, RowView(filter=FilterChoice.WITH_CONDITIONS))

    assert len(many.entities.boundaries) == FILTER_ABOVE + 3
    assert [row.get("name") for row in listed.rows] == ["LeftFace", "RightFace"] and listed.unlisted == FILTER_ABOVE + 1 and listed.total == FILTER_ABOVE + 3
    assert count_text(len(listed.rows), listed.total) == "2 of 15" and count_text(3, 3) == "3"
    assert listed.rows[0].get("detail") == "20000 mm²", "a solid reads the measured area"


def test_a_shared_material_heads_its_bodies_and_a_partial_count_reads_shown_of_total() -> None:
    bodies = Bodies([Body(name=f"MAGNET{index}", mesh_id=index, material="N42") for index in range(17)] + [Body(name="AIR", mesh_id=17, material="Air")])
    motor = SetupModel(run_id="motor", frame=Solid(), entities=Entities(bodies=bodies, boundaries=Boundaries(())), conditions=None)
    listed = body_rows(motor, RowView())

    assert [row.get("name") for row in listed.rows][:2] == ["N42", "MAGNET0"] and listed.total == 18
    assert count_text(1, 18) == "1 of 18" and count_text(18, 18) == "18"
    sized = replace(motor, entities=Entities(bodies=Bodies([Body(name="AIR", mesh_id=1, material="Air", measure=2e-6)]), boundaries=Boundaries(())))
    assert body_rows(sized, RowView()).rows[0].get("detail") == "2000 mm³", "a solid reads the volume in mm³"


def test_the_frame_section_reads_the_frame_type_and_its_facts_and_a_failed_check_lists_its_lines(tmp_path: Path) -> None:
    model = _model(tmp_path)
    toroid = GeometryCheck(frame="cartesian", tolerance=0.01, measured=(BodyMismatch(body="Name=Flexure, Material=Steel1080", check=FrameCheck.PRISM, fraction=0.032), BodyMismatch(body="Name=Air", check=FrameCheck.DEPTH, fraction=0.02)))
    solid = SetupModel(run_id="motor", frame=Solid(), entities=model.entities, conditions=None)

    facts = frame_facts(model)
    passed = toroid.model_copy(update={"measured": ()})

    assert [row["label"] for row in facts] == ["Type", "Normal", "Plane Origin", "Solve X, Y", "Thickness", "Plane State"], "a computed result such as the geometry check is no setup input"
    assert facts[0] == {"label": "Type", "kind": "choice", "value": "Cartesian 2D", "tip": FLEXURE_FRAME.description}, "the frame type reads as the select that would author it"
    assert facts[2] == {"label": "Plane Origin", "kind": "vector", "values": ["0", "0", "5"], "axes": ["X", "Y", "Z"], "unit": "mm"}
    assert facts[4] == {"label": "Thickness", "kind": "quantity", "value": "10", "unit": "mm"}
    assert frame_facts(solid) == [{"label": "Type", "kind": "choice", "value": "Solid", "tip": Solid().description}], "a whole-model frame still names its type"
    expected = ["Flexure Fails Prism Test (Mismatch\u00a03.2\u00a0%)", "Air Fails Depth Test (Mismatch\u00a02\u00a0%)"]
    assert check_line(toroid, FLEXURE_FRAME) == (expected, True), "every failing body and test, worst first, the share never split across lines"
    assert check_line(passed, FLEXURE_FRAME) == (["Passed"], False)
    assert check_line(None, FLEXURE_FRAME) == (["Not Recorded"], False) and check_line(None, Solid()) == ([], False)
    assert frame_facts(SetupModel(run_id="x", frame=None, entities=model.entities, conditions=None)) == []


def test_the_measure_reads_the_area_the_length_or_a_dash() -> None:
    face = Boundary(name="RIGHTFACE", mesh_id=4, bodies=("FLEXURE",), kind=BoundaryKind.NAMED, length=0.02, centroid=Vec3(0.01, 0, 0), measure=2e-4)

    assert measure_cell(face, Solid()) == ("200 mm²", "")
    assert measure_cell(face, Axisymmetric()) == ("20 mm", "Revolved Area 2π·r̄·L = 1260 mm²")
    on_axis = replace(face, name="AIR", kind=BoundaryKind.AXIS, centroid=Vec3(0, 0.03, 0))
    assert measure_cell(on_axis, Axisymmetric()) == ("20 mm", ""), "the symmetry axis sweeps no surface, so it carries no tooltip"
    assert measure_cell(Boundary(name="A__B", mesh_id=5, bodies=("A", "B"), kind=BoundaryKind.INTERFACE), FLEXURE_FRAME) == ("", ""), "an unmeasured group leaves the cell empty"
    assert measure_cell(Boundary(name="A__B", mesh_id=5, bodies=("A", "B"), kind=BoundaryKind.INTERFACE), None) == ("", "")


def test_the_order_ranks_named_faces_then_conditioned_groups_then_interfaces_externals_and_axes() -> None:
    def boundary(kind: BoundaryKind) -> Boundary:
        return Boundary(name="X", mesh_id=1, bodies=("A",), kind=kind)

    assert order(boundary(BoundaryKind.NAMED), conditioned=False) == 0
    assert order(boundary(BoundaryKind.EXTERNAL), conditioned=True) == 1
    assert [order(boundary(kind), conditioned=False) for kind in (BoundaryKind.INTERFACE, BoundaryKind.EXTERNAL, BoundaryKind.AXIS)] == [2, 3, 4]


def test_a_line_reads_the_authored_axes_in_order_with_the_dropped_one_muted_in_place() -> None:
    sideways = Cartesian2D(normal=Axis(direction=Direction(1, 0, 0)), thickness=quantity(10, "mm"))
    roller = FixedDisplacement(y=None)
    emitted = roller.on_boundary(BoundarySite(name="LEFTFACE", measure=0.02, frame=sideways))
    record = _record("fixed_displacement", roller, 1, cast("dict[str, float]", emitted), dropped=roller.dropped_fields(sideways))

    assert condition_lines(record, sideways) == [
        {
            "parts": [
                {"text": "Fixed Displacement "},
                {"text": "X –", "muted": True, "tip": "Authored X runs along no solve axis: solve X and Y run along +Y and +Z."},
                {"text": " · Y Free · Z 0"},
            ]
        }
    ]


def test_an_expression_keeps_its_place_with_its_text_as_the_tooltip() -> None:
    magnet = ConditionRecord(
        kind="magnetization",
        condition=Magnetization(direction=Direction(0, 0, 1)),
        section=SifSection.MATERIAL,
        block=1,
        block_name="MAGNET",
        keywords=[SifKeyword(name="Magnetization 1", value=1.0e6), SifKeyword(name="Magnetization 2", value="Real MATC")],
    )

    assert condition_lines(magnet, Solid())[1] == {"parts": [{"text": "Br (1.26, expr) T", "muted": False, "tip": "Real MATC"}]}


def test_the_ghost_caption_reads_the_warp_scale_as_a_plain_grouped_number() -> None:
    assert [scale_text(scale) for scale in (22654.0, 1234.5, 120.0)] == ["22,700", "1,230", "120"]
    assert [scale_text(scale) for scale in (1.5, 2.0, 0.00123, 0.0)] == ["1.5", "2", "0.00123", "0"], "below 10 reads its significant decimals"
    assert [scale_text(scale) for scale in (1e6, 2.27e7)] == ["1,000,000", "2.27e+7"], "exponent form only above a million"


SCALES: tuple[float, ...] = (22654.0, 1234.5, 120.0, 1.5, 2.0, 0.00123, 0.0, 1e6, 2.27e7)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to evaluate the readout expression")
def test_the_field_viewer_scale_readout_reads_like_the_ghost_caption() -> None:
    state: str = str(FieldVar.WARP_SCALE)
    script = f"console.log(JSON.stringify({json.dumps(SCALES)}.map(value => {{ const {state} = [value]; return {SCALE_READOUT}; }})))"
    shown = cast("list[str]", json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout))

    assert shown == [TIMES + scale_text(scale) for scale in SCALES]


def test_the_empty_tab_says_why_no_run_is_open() -> None:
    assert empty_text(None, indexed=True) == "No run open. Pick one in the Run picker in the app bar."
    assert empty_text(None, indexed=False) == "No run index. Pass telemetry to the workbench to list runs."
    assert empty_text("FileNotFoundError: missing", indexed=True) == "The run could not be opened."


def test_a_dropped_axis_reads_a_dash_even_without_a_frame() -> None:
    held = FixedDisplacement(z=quantity(0.5, "mm"))
    record = _record("fixed_displacement", held, 1, {}, dropped=("z",))

    assert condition_lines(record, None) == [
        {"parts": [{"text": "Fixed Displacement X 0 · Y 0 · "}, {"text": "Z –", "muted": True, "tip": "Authored Z runs along no solve axis."}]}
    ]


def test_a_dropped_axis_names_the_solve_axes_in_authored_terms() -> None:
    sideways = Cartesian2D(normal=Axis(direction=Direction(1, 0, 0)), thickness=quantity(10, "mm"))

    assert dropped_tip("x", frame=sideways) == "Authored X runs along no solve axis: solve X and Y run along +Y and +Z."
    assert dropped_tip("y", frame=Axisymmetric()) == "Authored Y runs along no solve axis: radial and axial run along +X and +Z."
    assert dropped_tip("z", frame=None) == "Authored Z runs along no solve axis." and dropped_tip("z", frame=Solid()) == "Authored Z runs along no solve axis."


def test_a_block_femforge_set_ranks_by_its_kind_but_passes_the_filter() -> None:
    automatic = ConditionRecord(kind="symmetry_axis", section=SifSection.BOUNDARY_CONDITION, block=1, block_name="AIR__AXIS_symmetry_axis")
    groups = [Boundary(name="AIR__AXIS", mesh_id=1, bodies=("AIR",), kind=BoundaryKind.AXIS), Boundary(name="AIR__COIL", mesh_id=2, bodies=("AIR", "COIL"), kind=BoundaryKind.INTERFACE)]
    conditions = ConditionsFile(boundaries=[GroupConditions(group="AIR__AXIS", conditions=[automatic]), GroupConditions(group="AIR__COIL")])
    coil = SetupModel(run_id="coil", frame=Axisymmetric(), entities=Entities(bodies=Bodies([Body(name="AIR", mesh_id=1)]), boundaries=Boundaries(groups)), conditions=conditions)

    assert [row.get("id") for row in boundary_rows(coil, RowView()).rows] == ["boundary:AIR__COIL", "boundary:AIR__AXIS"]
    assert [row.get("id") for row in boundary_rows(coil, RowView(filter=FilterChoice.WITH_CONDITIONS)).rows] == ["boundary:AIR__AXIS"]


def test_the_caption_claims_glyphs_only_for_a_run_that_draws_them(tmp_path: Path) -> None:
    model = _model(tmp_path)
    bare = replace(model, conditions=None, has_mesh=True)

    assert caption_of(replace(model, has_mesh=True)) == "No fields · glyphs drawn on the mesh."
    assert caption_of(bare) == "No fields · showing the mesh." and caption_of(replace(bare, solving=True)) == "Solving · showing the mesh."
    assert caption_of(model) == "Glyphs need the mesh; re-solve to restore it." and caption_of(replace(bare, has_mesh=False)) == ""
    assert caption_of(replace(bare, has_mesh=False, legacy_names=True)) == "Names recovered from mesh.names."


def test_a_femforge_block_reads_muted() -> None:
    automatic = ConditionRecord(kind="far_field", section=SifSection.BOUNDARY_CONDITION, block=3, block_name="AIR__EXT_far_field")

    assert condition_lines(automatic, None) == [{"parts": [{"text": "Far Field "}, {"text": "(Set by femforge)", "muted": True}]}]


def test_the_notices_name_a_run_without_conditions_one_that_sets_none_and_one_that_failed_before_meshing(tmp_path: Path) -> None:
    model = _model(tmp_path)
    empty = SetupModel(run_id="bare", frame=Solid(), entities=model.entities, conditions=ConditionsFile(bodies=[GroupConditions(group="FLEXURE")]))
    authored = SetupModel(run_id="failed", frame=FLEXURE_FRAME, entities=Entities(bodies=Bodies(()), boundaries=Boundaries(())), conditions=None, tags=TAGS)

    assert notice_of(model) == ("", NoticeLevel.INFO)
    assert notice_of(replace(model, conditions=None))[0].startswith("This run recorded no conditions.json")
    assert notice_of(empty) == ("This run sets no conditions; every boundary takes the solver's natural condition.", NoticeLevel.INFO)
    assert notice_of(authored)[1] is NoticeLevel.WARNING and authored.state is SetupState.AUTHORED
    rows = tag_rows(authored).rows
    assert [row.get("name") for row in rows] == ["Name=LeftFace", "Name=RightFace", "Name=Flexure"] and {row.get("kind_icon") for row in rows} == {"file"}
    assert rows[0].get("cards", [])[0]["fields"] == [{"label": "Displacement", "kind": "vector", "values": ["0", "0", "0"], "axes": ["X", "Y", "Z"], "unit": "mm"}]
    assert rows[0].get("label") == "Name=LeftFace, Fixed Displacement X 0 · Y 0 · Z 0"
    assert [[card.get("icon") for card in row.get("cards", [])] for row in rows] == [["anchor"], ["arrow-big-right-dash"], []], "a tag shows the kind of glyph its condition draws"


def test_the_legend_counts_each_glyph_kind_the_run_draws(tmp_path: Path) -> None:
    model = _model(tmp_path)

    assert legend_rows(model) == [{"kind": "surface_force", "label": "Surface Force", "count": 1}, {"kind": "fixed_displacement", "label": "Fixed Displacement", "count": 1}]
    assert legend_label(ConditionGlyph.VOLTAGE) == "Voltage Potential"
    assert legend_label(ConditionGlyph.COIL_TERMINAL) == "Coil Terminal"


def test_every_glyph_kind_has_an_icon_and_a_negative_drive_points_into_the_plane() -> None:
    names = set(get_args(cast("object", IconName.__value__)))

    assert all(icon_of(glyph) in names for glyph in ConditionGlyph)
    assert icon_of(ConditionGlyph.CURRENT_DENSITY, negative=True) == "circle-x" and icon_of(ConditionGlyph.CURRENT_DENSITY) == "circle-dot"


def test_a_drive_into_the_plane_and_an_output_draw_their_own_icons() -> None:
    drive = ConditionRecord(kind="current_density", condition=CurrentDensity(value=quantity(-3e6, "A/m**2")), section=SifSection.BODY_FORCE, block=1, block_name="COIL", keywords=[SifKeyword(name="Current Density", value=-3e6)])
    output = ConditionRecord(kind="calculate_magnetic_force", condition=CalculateMagneticForce(), section=SifSection.COMPONENT, block=1, block_name="PLUNGER")
    model = SetupModel(
        run_id="coil",
        frame=Axisymmetric(),
        entities=Entities(bodies=Bodies([Body(name="COIL", mesh_id=1), Body(name="PLUNGER", mesh_id=2)]), boundaries=Boundaries(())),
        conditions=ConditionsFile(bodies=[GroupConditions(group="COIL", conditions=[drive]), GroupConditions(group="PLUNGER", conditions=[output])]),
    )
    coil, plunger = body_rows(model, RowView()).rows

    assert coil.get("cards") == [{"title": "Current Density", "icon": "circle-x", "fields": [{"label": "Value", "kind": "quantity", "value": "-3", "unit": "A/mm²"}]}]
    assert plunger.get("cards") == [
        {"title": "Calculate Magnetic Force", "fields": [{"label": "Calculate Magnetic Force", "kind": "choice", "value": "On"}, {"label": "Calculate Magnetic Torque", "kind": "choice", "value": "Off"}]}
    ], "an output draws no glyph, so its card has no icon"
    assert "Calculate Magnetic Force (Output, No Glyph)" in str(plunger.get("label"))


def test_a_convection_reads_each_value_after_its_glyph() -> None:
    cooled = ConditionRecord(
        kind="convection",
        condition=Convection(coefficient=quantity(10, "W/(m**2*K)"), ext_temperature=quantity(293, "K")),
        section=SifSection.BOUNDARY_CONDITION,
        block=1,
        block_name="COIL__EXT_convection",
    )
    held = ConditionRecord(kind="fixed_voltage", condition=FixedVoltage(value=quantity(0.5, "V")), section=SifSection.BOUNDARY_CONDITION, block=2, block_name="END")

    assert _texts(condition_lines(cooled, Solid())) == ["Convection h 10 W/(m²·K) · T 293 K"]
    assert _texts(condition_lines(held, Solid())) == ["Fixed Voltage 0.5 V"]


def test_a_legacy_bundle_recovers_its_names_and_a_bad_manifest_costs_only_the_frame(tmp_path: Path) -> None:
    bundle = _flexure(tmp_path / "legacy", entities=False, conditions=False)
    _ = (bundle / BundleFile.MESH_NAMES).write_bytes(b"! ----- names for bodies -----\n$ FLEXURE = 1\n! ----- names for boundaries -----\n$ LEFTFACE = 3\n")
    legacy = read_setup(Run(bundle))
    _ = (bundle / BundleFile.MANIFEST).write_bytes(b"{")
    _ = (bundle / BundleFile.CONDITIONS).write_bytes(b"{")
    broken = read_setup(Run(bundle))

    assert legacy.legacy_names and legacy.state is SetupState.GROUPS
    assert broken.frame is None and broken.tags == () and broken.conditions is None
    assert [body.name for body in legacy.entities.bodies] == ["FLEXURE"]


def test_a_material_group_heads_its_bodies_without_repeating_the_material() -> None:
    bodies = Bodies([Body(name="FRAME", mesh_id=1, material=STEEL.tag), Body(name="PLUNGER", mesh_id=2, material=STEEL.tag)])
    model = SetupModel(run_id="coil", frame=Axisymmetric(), entities=Entities(bodies=bodies, boundaries=Boundaries(())), conditions=None, materials=(STEEL,))
    group, *members = body_rows(model, RowView()).rows

    assert group.get("group") is True and group.get("detail") == "2 Bodies" and group.get("tip") == [STEEL.tag, STEEL.name]
    assert "kind_icon" not in group and "cards" not in group, "a material heading is no group of the mesh"
    assert [(member.get("member"), member.get("kind_icon"), "material" in member) for member in members] == [(True, "pentagon", False), (True, "pentagon", False)]


def test_a_pull_mapped_onto_a_sideways_plane_reads_the_traction_with_its_sign() -> None:
    sideways = Cartesian2D(normal=Axis(direction=Direction(1, 0, 0)), thickness=quantity(10, "mm"))
    pull = SurfaceForce(force=quantity(Vec3(0, -100, 0), "N"))
    emitted = pull.on_boundary(BoundarySite(name="TIP", measure=2e-2, frame=sideways))
    record = _record("surface_force", pull, 2, {name: float(value) for name, value in emitted.items()})

    assert _texts(condition_lines(record, sideways)) == ["Surface Force 100 N Along (0, -1, 0)", "Traction (-500, 0) kPa"]


def test_the_table_headers_name_the_measure_the_frame_reads() -> None:
    assert boundary_columns(FLEXURE_FRAME) == ["Name", "Length"]
    assert boundary_columns(Solid()) == ["Name", "Area"] and boundary_columns(None) == ["Name", "Area"]
    assert body_columns(FLEXURE_FRAME) == ["Name", "Section Area"] and body_columns(Solid()) == ["Name", "Volume"] and body_columns(None) == ["Name", "Volume"]


def test_a_framed_group_keeps_the_scene_around_it() -> None:
    assert padded((0.0, 1.0, 0.0, 0.0, 2.0, 2.0), 0.5) == (0.0, 1.0, -0.25, 0.25, 1.75, 2.25)


# --- the template ----------------------------------------------------------------


def test_the_tab_builds_its_switches_legend_sections_and_grids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("template", tmp_path, monkeypatch)
    template = cast("Node", app.state[f"trame__template_{template_name(FieldPanel.SETUP)}"])
    switches = find(template, "ff-switch")
    checkboxes = find(template, "ff-checkbox")
    grids = find(template, "ff-entity-grid")
    headers = [cast("Node", trigger["children"][0]) for trigger in find(template, "ff-accordion-trigger")]  # pyright: ignore[reportIndexIssue]  # a trigger node holds its header span

    assert [_bound(node, "checked") for node in switches] == [str(FieldVar.SECTION_OUTLINE), str(FieldVar.AUTHORED_GHOST)], "the 2D switches are the only switches; the legend shows the glyphs"
    assert [texts(find(header, "span")[1])[0] for header in headers] == ["Frame", "Authored Tags", "Bodies", "Boundaries", "Glyphs"]
    assert [_bound(node, "rows") for node in grids] == [str(SetupVar.TAG_ROWS), str(SetupVar.BODY_ROWS), str(SetupVar.BOUNDARY_ROWS)]
    assert all(_bound(node, "selected") == str(FieldVar.SELECTED_ENTITY) for node in grids)
    assert all(prop(node, "onHover") == {"callback": {"js": f'trame.state.set("{FieldVar.HOVERED}", $event)'}} for node in grids), "a hovered row outlines its group in the view"
    assert [prop(node, "htmlFor") for node in find(template, "ff-field-label")] == [prop(node, "id") for node in switches]
    assert [len(find(row, "ff-checkbox")) for row in find(template, "label")] == [1] * len(checkboxes), "a click anywhere on a Glyphs row toggles its checkbox"
    assert find(template, "ff-input") == [], "the tab has no search"
    assert len(checkboxes) == 1 + len(ConditionGlyph) and prop(checkboxes[0], "id") == "ff-setup-kind-all", "an All Glyphs row leads one checkbox per glyph kind"
    assert "'indeterminate'" in str(_bound(checkboxes[0], "checked")), "All Glyphs reads mixed while only some kinds are off"
    assert all(_bound(node, "disabled") == f"!{SetupVar.HAS_SCENE}" for node in checkboxes), "a legend checkbox needs a scene to act on"
    assert _bound(switches[1], "aria-disabled") == f"!{SetupVar.HAS_GHOST}", "a ghost switch without a ghost says it is disabled"
    assert prop(grids[0], "columns") == ["Tag", ""] and [_bound(node, "columns") for node in grids[1:]] == [str(SetupVar.BODY_COLUMNS), str(SetupVar.BOUNDARY_COLUMNS)]
    assert [_bound(node, "fields") for node in find(template, "ff-fields")] == [str(SetupVar.FRAME_FACTS)], "the frame reads as read-only fields"
    assert f'"js": "{SetupVar.WARP_CAPTION}"' in json.dumps(template), "the ghost names the warp the solved body shows"
    assert f"{FieldVar.AUTHORED_GHOST} && {SetupVar.HAS_GHOST} && " in json.dumps(template), "the warp caption shows only while the ghost is drawn"


def test_a_run_without_conditions_lists_every_boundary_and_never_counts_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("more", tmp_path, monkeypatch)
    panel = app.panel(FieldPanel.SETUP, SetupPanel)
    flexure = _model(tmp_path)
    named = [Boundary(name=f"FACE{index:02d}", mesh_id=index + 10, bodies=("FLEXURE",), kind=BoundaryKind.NAMED) for index in range(FILTER_ABOVE)]
    many = replace(flexure, entities=Entities(bodies=flexure.entities.bodies, boundaries=Boundaries([*flexure.entities.boundaries, *named])))

    panel.show_run(many)

    assert app.state[SetupVar.FILTER] == str(FilterChoice.WITH_CONDITIONS) and app.state[SetupVar.HAS_CONDITIONS] is True
    assert app.state[SetupVar.MORE] == f"{FILTER_ABOVE + 1} More Without Conditions · All"

    panel.show_run(replace(many, conditions=None))

    assert app.state[SetupVar.FILTER] == str(FilterChoice.ALL) and app.state[SetupVar.HAS_CONDITIONS] is False
    assert app.state[SetupVar.MORE] == "" and app.state[SetupVar.BOUNDARY_COUNT] == str(FILTER_ABOVE + 3)
    assert boundary_rows(replace(many, conditions=None), RowView(filter=FilterChoice.WITH_CONDITIONS)).unlisted == 0, "unknown conditions filter nothing"


# --- the controller's paths ------------------------------------------------------


def test_opening_a_run_publishes_it_and_a_new_run_resets_the_legend_and_the_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths", tmp_path, monkeypatch)
        controller = _controller(app)
        panel = app.panel(FieldPanel.SETUP, SetupPanel)
        revealed: list[str] = []
        dock = app.dock
        assert dock is not None
        monkeypatch.setattr(dock, "active_panel", revealed.append)
        def restored(_: object) -> None:
            revealed.append("layout")

        monkeypatch.setattr(dock, "restore_layout", restored)

        assert app.state[SetupVar.STATE] == str(SetupState.NONE)

        first = _flexure(tmp_path / "first")
        await controller.load(first)
        await controller.settle()

        assert app.state[SetupVar.STATE] == str(SetupState.GROUPS) and app.state[SetupVar.BOUNDARY_COUNT] == "3"
        assert revealed == [], "a dock not yet ready drops an activation"

        app.on_dock_ready()

        assert revealed == ["layout", str(FieldPanel.SETUP)], "a run without fields opened at launch brings Setup forward over the restored layout"

        revealed.clear()
        await controller.load(first)
        await controller.settle()

        assert revealed == [str(FieldPanel.SETUP)], "a run without fields opened later brings Setup forward at once"
        assert app.state[SetupVar.CAPTION] == "Glyphs need the mesh; re-solve to restore it."
        assert app.state[SetupVar.HAS_SCENE] is False, "the scene switches hide while the view draws nothing"
        assert all(row.get("dimmed") for row in cast("list[dict[str, object]]", app.state[SetupVar.BOUNDARY_ROWS])), "nothing has cells without fields or mesh"

        panel.on_kind("thermal", False)
        app.state[FieldVar.SELECTED_ENTITY] = "boundary:LEFTFACE"

        assert app.state[FieldVar.GLYPH_KINDS_OFF] == ["thermal"]

        second = _flexure(tmp_path / "second")
        await controller.load(second)
        await controller.settle()

        assert app.state[FieldVar.GLYPH_KINDS_OFF] == [] and app.state[FieldVar.SELECTED_ENTITY] == ""

        panel.on_kind("surface_force", False)
        app.state[FieldVar.SELECTED_ENTITY] = "boundary:LEFTFACE"
        await controller.load(second)
        await controller.settle()

        assert app.state[FieldVar.GLYPH_KINDS_OFF] == ["surface_force"] and app.state[FieldVar.SELECTED_ENTITY] == "boundary:LEFTFACE", "the same run reopening keeps the legend and the selection"

        await controller.load(tmp_path / "missing")
        await controller.settle()

        assert app.state[SetupVar.STATE] == str(SetupState.NONE), "a run that fails to open clears the tab"
        assert app.state[SetupVar.EMPTY] == "The run could not be opened."
        assert app.state[SetupVar.EMPTY_DETAIL] == controller.note and str(controller.note).startswith("FileNotFoundError: "), "the tab shows the failure the view shows"

        await controller.load(first)
        await controller.settle()

        assert controller.failure is None and app.state[SetupVar.STATE] == str(SetupState.GROUPS), "a run that opens forgets the failure"

    asyncio.run(run())


def test_the_empty_tab_names_a_missing_run_index_and_the_picker_otherwise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare = _shell("bare", tmp_path / "bare", monkeypatch)

    assert bare.state[SetupVar.EMPTY] == "No run index. Pass telemetry to the workbench to list runs." and bare.state[SetupVar.EMPTY_DETAIL] == ""

    indexed = Workbench("indexed", title="Fields", server=get_server("setup-indexed"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
    indexed.add(workbench(telemetry=tmp_path / "telemetry"))

    assert indexed.state[SetupVar.EMPTY] == "No run open. Pick one in the Run picker in the app bar."


def test_the_ghost_caption_follows_the_warp_scale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("warp", tmp_path, monkeypatch)
    panel = app.panel(FieldPanel.SETUP, SetupPanel)
    app.state[FieldVar.WARP_SCALE] = [22654.0]
    panel.on_warp_scale()

    assert app.state[SetupVar.WARP_CAPTION] == "Undeformed; the solved body is warped ×22,700"


def test_a_reloaded_page_brings_setup_forward_once_its_new_dock_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("reload", tmp_path, monkeypatch)
        controller = _controller(app)
        dock = app.dock
        assert dock is not None
        revealed: list[str] = []
        monkeypatch.setattr(dock, "active_panel", revealed.append)

        def restored(_: object) -> None:
            revealed.append("layout")

        monkeypatch.setattr(dock, "restore_layout", restored)
        app.on_dock_ready()
        app.on_client_exited()
        app.on_client_connected()

        assert app.docked is False, "a page that left takes its dock with it"

        await controller.load(_flexure(tmp_path / "fieldless"))
        await controller.settle()

        assert revealed == ["layout"], "the reloaded page has no dock yet to activate a tab in"

        app.on_dock_ready()

        assert revealed == ["layout", "layout", str(FieldPanel.SETUP)], "the new dock brings Setup forward over its restored layout"

    asyncio.run(run())


def test_a_run_that_failed_before_meshing_lists_its_authored_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("authored", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_flexure(tmp_path / "failed", entities=False, conditions=False))
        await controller.settle()

        assert app.state[SetupVar.STATE] == str(SetupState.AUTHORED) and app.state[SetupVar.TAG_COUNT] == "3"
        assert app.state[SetupVar.NOTICE_LEVEL] == str(NoticeLevel.WARNING) and app.state[SetupVar.CAPTION] == ""

    asyncio.run(run())


def test_the_handlers_select_announce_clear_and_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("handlers", tmp_path, monkeypatch)
        controller = _controller(app)
        panel = app.panel(FieldPanel.SETUP, SetupPanel)
        framed: list[EntityRef] = []
        monkeypatch.setattr(controller, "frame_entity", framed.append)

        await controller.load(_flexure(tmp_path / "flexure"))
        await controller.settle()
        controller.highlightable = None
        monkeypatch.setattr(controller, "groups", object())
        panel.publish()
        assert app.state[SetupVar.HAS_SCENE] is True, "a mesh-only scene shows the scene switches"
        controller.groups = None

        panel.on_select("boundary:RIGHTFACE")
        assert app.state[FieldVar.SELECTED_ENTITY] == "boundary:RIGHTFACE" and app.state[SetupVar.ANNOUNCE] == "Highlighted RightFace"

        panel.on_select("boundary:RIGHTFACE")
        assert app.state[FieldVar.SELECTED_ENTITY] == "boundary:RIGHTFACE", "a second click keeps the row selected, so a double-click never clears it"

        panel.on_select("")
        assert app.state[FieldVar.SELECTED_ENTITY] == "" and app.state[SetupVar.ANNOUNCE] == "Selection cleared"

        panel.on_select("material:Steel1080")
        assert app.state[FieldVar.SELECTED_ENTITY] == "", "a material row selects nothing"

        panel.on_frame("boundary:LEFTFACE")
        panel.on_frame("nonsense")
        assert framed == [EntityRef(EntityKind.BOUNDARY, "LEFTFACE")]

        controller.bodies = ["FLEXURE", "OTHER"]
        app.state[FieldVar.VISIBLE_BODIES] = ["OTHER"]
        panel.on_show("body:FLEXURE")
        panel.on_show("nonsense")
        assert app.state[FieldVar.VISIBLE_BODIES] == ["FLEXURE", "OTHER"]

        panel.on_kind("thermal", False)
        panel.on_kind("thermal", True)
        assert app.state[FieldVar.GLYPH_KINDS_OFF] == []

        panel.on_all_kinds(False)
        assert app.state[FieldVar.GLYPH_KINDS_OFF] == ["surface_force", "fixed_displacement"], "All Glyphs off hides every kind the run draws"
        panel.on_all_kinds(True)
        assert app.state[FieldVar.GLYPH_KINDS_OFF] == []

        controller.highlightable = frozenset()
        panel.publish()
        panel.on_select("boundary:LEFTFACE")
        assert app.state[FieldVar.SELECTED_ENTITY] == "" and app.state[SetupVar.ANNOUNCE] == "LeftFace has no cells to highlight"

        app.state[SetupVar.FILTER] = str(FilterChoice.WITH_CONDITIONS)
        panel.show_all()
        assert app.state[SetupVar.FILTER] == str(FilterChoice.ALL)

        panel.on_visible(True)
        assert app.state[FieldVar.SETUP_SHOWN] is True

    asyncio.run(run())


def test_a_rebuilt_tab_shows_the_open_run_and_disposing_it_hides_the_glyphs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("rebuilt", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_flexure(tmp_path / "flexure"))
        await controller.settle()
        panel = app.panel(FieldPanel.SETUP, SetupPanel)
        panel.on_visible(True)
        panel.dispose()

        assert app.state[FieldVar.SETUP_SHOWN] is False and controller.setup_panel is None

        rebuilt = SetupPanel(app, str(FieldPanel.SETUP), "Setup", controller)

        assert controller.setup_panel is rebuilt and rebuilt.model is controller.setup
        assert app.state[SetupVar.STATE] == str(SetupState.GROUPS)

    asyncio.run(run())


def test_a_scene_whose_layer_failed_dims_every_row_as_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("unreadable", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _flexure(tmp_path / "flexure")

        assert highlightable(None, None, Run(bundle)) == frozenset(), "no scene has no cells"
        assert highlightable(None, cast("GlyphHost", object()), Run(bundle)) is None, "a scene without its layer cannot tell"

        await controller.load(bundle)
        await controller.settle()
        controller.mark(None)
        rows = cast("list[dict[str, object]]", app.state[SetupVar.BOUNDARY_ROWS])

        assert controller.cells_unreadable and rows
        assert all(row.get("dim_tip") == "Highlight unavailable: the scene's groups failed to load." for row in rows)

    asyncio.run(run())
