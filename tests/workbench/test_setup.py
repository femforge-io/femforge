"""The Setup tab: its row builders on a flexure-shaped bundle, its template bindings and the controller paths that feed it."""

import asyncio
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
from femforge.physical.conditions import CalculateMagneticForce, ConditionGlyph, Convection, CurrentDensity, FixedDisplacement, FixedVoltage, SurfaceForce
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import quantity
from femforge.run.bundle import write_manifest
from femforge.run.conditions import ConditionRecord, ConditionsFile, GroupConditions, SifKeyword, SolveComponent
from femforge.run.entities import Bodies, Body, BodyRecord, Boundaries, Boundary, BoundaryKind, BoundaryRecord, Entities, EntitiesFile
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.result import RunStatus
from femforge.viz.glyphs import EntityKind, EntityRef, GlyphHost
from femforge.viz.symmetry import Expansion
from tests.workbench.tree import Node, find, prop, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.dock import template_name  # noqa: E402
from femforge_workbench.panels.field_viewer import FieldPanel, FieldVar, FieldViewerController, FieldViewerPanel, highlightable, workbench  # noqa: E402
from femforge_workbench.panels.setup import (  # noqa: E402
    FILTER_ABOVE,
    FilterChoice,
    NoticeLevel,
    RowView,
    SetupModel,
    SetupPanel,
    SetupState,
    SetupVar,
    body_rows,
    boundary_rows,
    caption_of,
    check_line,
    condition_lines,
    count_text,
    dropped_tip,
    frame_facts,
    icon_of,
    legend_label,
    legend_rows,
    matches,
    measure_cell,
    miss_text,
    notice_of,
    order,
    read_setup,
    tag_rows,
)
from femforge_workbench.persist import LocalJsonStore  # noqa: E402
from femforge_workbench.widgets.entity_grid import ConditionLine  # noqa: E402
from femforge_workbench.widgets.icon import IconName  # noqa: E402


FLEXURE_FRAME = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=quantity(10, "mm"))
STEEL = Steel_1080()


def _record(kind: str, condition: object, block: int, keywords: dict[str, float], *, dropped: tuple[str, ...] = (), shadowed: bool = False) -> ConditionRecord:
    return ConditionRecord(
        kind=kind,
        condition=cast("FixedDisplacement", condition),
        tag=block - 1,
        selector={TagKey.NAME: "LeftFace" if block == 1 else "RightFace"},
        section=SifSection.BOUNDARY_CONDITION,
        block=block,
        block_name=f"{kind}_{block}",
        keywords=[SifKeyword(name=name, value=value, shadowed=shadowed) for name, value in keywords.items()],
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

    assert model.state is SetupState.GROUPS and model.frame == FLEXURE_FRAME and not model.has_fields and not model.legacy_names
    assert [row.get("name") for row in bodies.rows] == ["Flexure"] and bodies.rows[0].get("detail") == STEEL.tag
    assert bodies.rows[0].get("detail_tip") == STEEL.name and _texts(bodies.rows[0].get("lines", [])) == ["No Conditions"]
    assert bodies.rows[0].get("tip") == ["FLEXURE", "Body", "Section Area 1450 mm²"]
    assert [row.get("id") for row in boundaries.rows] == ["boundary:LEFTFACE", "boundary:RIGHTFACE", "boundary:FLEXURE__EXT"]
    assert [row.get("name") for row in boundaries.rows] == ["LeftFace", "RightFace", "Flexure · External"], "names stay verbatim"


def test_the_rows_read_what_the_solver_received_with_the_dropped_axis_muted(tmp_path: Path) -> None:
    left, right, external = boundary_rows(_model(tmp_path), RowView()).rows
    parts = left.get("lines", [])[0]["parts"]

    assert _texts(right.get("lines", [])) == ["Surface Force 100 N Along (1, 0, 0)", "Traction 500 kPa"]
    assert parts == [
        {"text": "Fixed Displacement X 0 · Y 0 · "},
        {"text": "Z –", "muted": True, "tip": "Authored Z runs along no solve axis: solve X and Y run along +X and +Y."},
    ]
    assert (left.get("detail"), left.get("detail_tip")) == ("20 mm", "Face Area 200 mm² at 10 mm Thickness")
    assert left.get("tip") == ["LEFTFACE", "Named Face", "Borders Flexure", "Boundary Condition 1"]
    assert (left.get("icon"), right.get("icon"), "icon" in external) == ("anchor", "arrow-big-right-dash", False), "a row without a drawable condition shows no toggle"
    assert right.get("label") == "RightFace, Surface Force 100 N Along (1, 0, 0), Traction 500 kPa, Glyph Shown"
    assert "Z – (Authored Z runs along no solve axis: solve X and Y run along +X and +Y.)" in str(left.get("label")), "the label reads the reason the tooltip shows"


def test_a_row_toggled_off_hidden_or_without_cells_says_so(tmp_path: Path) -> None:
    model = _model(tmp_path)
    view = RowView(visible=frozenset(), rows_off=frozenset({"boundary:RIGHTFACE"}), highlightable=frozenset({"boundary:RIGHTFACE"}))
    body = body_rows(model, view).rows[0]
    left, right, _ = boundary_rows(model, view).rows

    assert right.get("glyph_shown") is False and str(right.get("label")).endswith("Glyph Hidden")
    assert body.get("hidden") is True, "a body the Field Viewer hides offers to show it"
    assert str(body.get("label")).endswith("Hidden in Field Viewer; Press S to Show")
    assert left.get("dimmed") is True and left.get("dim_tip") == "Not in the Mesh Files: Re-solve to Restore the Mesh"
    assert str(left.get("label")).endswith(", Not in the Mesh Files: Re-solve to Restore the Mesh")
    assert "dimmed" not in right


def test_without_a_scene_a_row_shows_its_icon_without_a_toggle_and_unreadable_cells_say_so(tmp_path: Path) -> None:
    left, right, _ = boundary_rows(_model(tmp_path), RowView(highlightable=frozenset(), unreadable=True, drawn=False)).rows

    assert right.get("icon") == "arrow-big-right-dash" and "glyph_shown" not in right and "Glyph" not in str(right.get("label"))
    assert left.get("dimmed") is True and right.get("dim_tip") == "Highlight Unavailable: The Scene's Groups Failed to Load"


def test_many_boundary_groups_start_on_those_with_conditions_and_count_the_rest(tmp_path: Path) -> None:
    flexure = _model(tmp_path)
    named = [Boundary(name=f"FACE{index:02d}", mesh_id=index, bodies=("FLEXURE",), kind=BoundaryKind.NAMED) for index in range(FILTER_ABOVE)]
    many = SetupModel(run_id="many", frame=Solid(), entities=Entities(bodies=flexure.entities.bodies, boundaries=Boundaries([*named, *flexure.entities.boundaries])), conditions=CONDITIONS)
    listed = boundary_rows(many, RowView(filter=FilterChoice.WITH_CONDITIONS))

    assert len(many.entities.boundaries) == FILTER_ABOVE + 3
    assert [row.get("name") for row in listed.rows] == ["LeftFace", "RightFace"] and listed.unlisted == FILTER_ABOVE + 1 and listed.total == FILTER_ABOVE + 3
    assert count_text(len(listed.rows), listed.total) == "2 of 15" and count_text(3, 3) == "3"
    assert listed.rows[0].get("detail") == "20000 mm²", "a solid reads the measured area"


def test_a_search_matches_names_materials_and_kinds_and_says_when_nothing_does(tmp_path: Path) -> None:
    flexure = _model(tmp_path)
    bodies = Bodies([Body(name=f"MAGNET{index}", mesh_id=index, material="N42") for index in range(17)] + [Body(name="AIR", mesh_id=17, material="Air")])
    motor = SetupModel(run_id="motor", frame=Solid(), entities=Entities(bodies=bodies, boundaries=Boundaries(())), conditions=None)
    found = body_rows(motor, RowView(query="air"))

    assert [row.get("name") for row in found.rows] == ["AIR"] and found.total == 18 and count_text(len(found.rows), found.total) == "1 of 18"
    assert [row.get("name") for row in body_rows(motor, RowView()).rows][:2] == ["N42", "MAGNET0"], "a material several bodies share heads them"
    assert [row.get("name") for row in boundary_rows(flexure, RowView(query="surface")).rows] == ["RightFace"]
    assert matches(["RightFace"], "  ") and not matches(["RightFace"], "tip")
    assert miss_text("tip", 0) == "No Bodies or Boundaries Match 'tip'." and miss_text("tip", 1) == "" and miss_text("", 0) == ""


def test_the_frame_section_reads_the_frame_the_check_and_how_a_revolve_is_shown(tmp_path: Path) -> None:
    model = _model(tmp_path)
    toroid = GeometryCheck(frame="cartesian", tolerance=0.01, measured=(BodyMismatch(body="Name=Flexure, Material=Steel1080", check=FrameCheck.PRISM, fraction=0.032), BodyMismatch(body="Name=Air", check=FrameCheck.DEPTH, fraction=0.02)))
    revolved = SetupModel(run_id="coil", frame=Axisymmetric(), entities=model.entities, conditions=None)

    assert [row["label"] for row in frame_facts(model, Expansion.SECTION, 360.0)] == ["Plane Origin", "Solve X, Y", "Thickness", "Plane State"]
    assert frame_facts(revolved, Expansion.REVOLVE, 90.0)[-1] == {"label": "Shown As", "value": "Revolved 90°"}
    assert frame_facts(revolved, Expansion.MIRROR, 90.0)[-1] == {"label": "Shown As", "value": "Mirrored"}
    assert check_line(toroid, FLEXURE_FRAME) == ("Flexure Fails Prism Test (Mismatch 3.2 %) and 1 More", True)
    assert check_line(toroid.model_copy(update={"measured": ()}), FLEXURE_FRAME) == ("Passed", False)
    assert check_line(None, FLEXURE_FRAME) == ("Not Recorded", False) and check_line(None, Solid()) == ("", False)
    assert frame_facts(SetupModel(run_id="x", frame=None, entities=model.entities, conditions=None), Expansion.SECTION, 360.0) == []


def test_the_measure_reads_the_area_the_length_or_a_dash() -> None:
    face = Boundary(name="RIGHTFACE", mesh_id=4, bodies=("FLEXURE",), kind=BoundaryKind.NAMED, length=0.02, centroid=Vec3(0.01, 0, 0), measure=2e-4)

    assert measure_cell(face, Solid()) == ("200 mm²", "")
    assert measure_cell(face, Axisymmetric()) == ("20 mm", "Revolved Area 2π·r̄·L = 1260 mm²")
    assert measure_cell(Boundary(name="A__B", mesh_id=5, bodies=("A", "B"), kind=BoundaryKind.INTERFACE), FLEXURE_FRAME) == ("—", "")
    assert measure_cell(Boundary(name="A__B", mesh_id=5, bodies=("A", "B"), kind=BoundaryKind.INTERFACE), None) == ("—", "")


def test_the_order_ranks_named_faces_then_conditioned_groups_then_interfaces_externals_and_axes() -> None:
    def boundary(kind: BoundaryKind) -> Boundary:
        return Boundary(name="X", mesh_id=1, bodies=("A",), kind=kind)

    assert order(boundary(BoundaryKind.NAMED), conditioned=False) == 0
    assert order(boundary(BoundaryKind.EXTERNAL), conditioned=True) == 1
    assert [order(boundary(kind), conditioned=False) for kind in (BoundaryKind.INTERFACE, BoundaryKind.EXTERNAL, BoundaryKind.AXIS)] == [2, 3, 4]


def test_a_dropped_axis_names_the_solve_axes_in_authored_terms() -> None:
    sideways = Cartesian2D(normal=Axis(direction=Direction(1, 0, 0)), thickness=quantity(10, "mm"))

    assert dropped_tip("x", sideways) == "Authored X runs along no solve axis: solve X and Y run along +Y and +Z."
    assert dropped_tip("y", Axisymmetric()) == "Authored Y runs along no solve axis: radial and axial run along +X and +Z."
    assert dropped_tip("z", None) == "Authored Z runs along no solve axis." and dropped_tip("z", Solid()) == "Authored Z runs along no solve axis."


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

    assert caption_of(replace(model, has_mesh=True)) == "No Fields · Glyphs Drawn on the Mesh"
    assert caption_of(bare) == "No Fields · Showing the Mesh" and caption_of(replace(bare, solving=True)) == "Solving · Showing the Mesh"
    assert caption_of(model) == "Glyphs Need the Mesh; Re-solve to Restore It." and caption_of(replace(bare, has_mesh=False)) == ""


def test_a_femforge_block_or_an_overridden_one_reads_muted() -> None:
    automatic = ConditionRecord(kind="far_field", section=SifSection.BOUNDARY_CONDITION, block=3, block_name="AIR__EXT_far_field")
    shadowed = _record("fixed_displacement", FixedDisplacement(), 1, {"Displacement 1": 0.0}, shadowed=True)

    assert condition_lines(automatic, None) == [{"parts": [{"text": "Far Field "}, {"text": "(Set by femforge)", "muted": True}]}]
    assert condition_lines(shadowed, FLEXURE_FRAME)[0]["parts"][-1].get("text") == "(Overridden)"


def test_the_notices_name_a_run_without_conditions_one_that_sets_none_and_one_that_failed_before_meshing(tmp_path: Path) -> None:
    model = _model(tmp_path)
    empty = SetupModel(run_id="bare", frame=Solid(), entities=model.entities, conditions=ConditionsFile(bodies=[GroupConditions(group="FLEXURE")]))
    authored = SetupModel(run_id="failed", frame=FLEXURE_FRAME, entities=Entities(bodies=Bodies(()), boundaries=Boundaries(())), conditions=None, tags=TAGS)

    assert notice_of(model) == ("", NoticeLevel.INFO)
    assert notice_of(replace(model, conditions=None))[0].startswith("This run recorded no conditions.json")
    assert notice_of(empty) == ("This run sets no conditions; every boundary takes the solver's natural condition.", NoticeLevel.INFO)
    assert notice_of(authored)[1] is NoticeLevel.WARNING and authored.state is SetupState.AUTHORED
    assert [row.get("name") for row in tag_rows(authored, "").rows] == ["Name=LeftFace", "Name=RightFace", "Name=Flexure"]
    assert _texts(tag_rows(authored, "").rows[0].get("lines", [])) == ["Fixed Displacement X 0 · Y 0 · Z 0"]
    assert [row.get("name") for row in tag_rows(authored, "right").rows] == ["Name=RightFace"]


def test_the_legend_counts_each_glyph_kind_the_run_draws(tmp_path: Path) -> None:
    model = _model(tmp_path)

    assert legend_rows(model) == [{"kind": "surface_force", "label": "Surface Force", "count": 1}, {"kind": "fixed_displacement", "label": "Fixed Displacement", "count": 1}]
    assert legend_label(ConditionGlyph.VOLTAGE) == "Voltage Potential"


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

    assert coil.get("icon") == "circle-x" and "icon" not in plunger
    assert _texts(plunger.get("lines", [])) == ["Calculate Magnetic Force (Output, No Glyph)"]


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


# --- the template ----------------------------------------------------------------


def test_the_tab_builds_its_switches_sections_and_grids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("template", tmp_path, monkeypatch)
    template = cast("Node", app.state[f"trame__template_{template_name(FieldPanel.SETUP)}"])
    switches = find(template, "ff-switch")
    headers = [cast("Node", trigger["children"][0]) for trigger in find(template, "ff-accordion-trigger")]  # pyright: ignore[reportIndexIssue]  # a trigger node holds its header span

    assert [_bound(node, "checked") for node in switches[:4]] == [str(var) for var in (FieldVar.GLYPHS, FieldVar.PLAIN_BODIES, FieldVar.SECTION_OUTLINE, FieldVar.AUTHORED_GHOST)]
    assert [texts(find(header, "span")[1])[0] for header in headers] == ["Frame", "Authored Tags", "Bodies", "Boundaries", "Glyphs"]
    assert [_bound(node, "rows") for node in find(template, "ff-entity-grid")] == [str(SetupVar.TAG_ROWS), str(SetupVar.BODY_ROWS), str(SetupVar.BOUNDARY_ROWS)]
    assert all(_bound(node, "selected") == str(FieldVar.SELECTED_ENTITY) for node in find(template, "ff-entity-grid"))
    assert [prop(node, "htmlFor") for node in find(template, "ff-field-label") if prop(node, "htmlFor") and prop(node, "htmlFor") != "ff-setup-search"] == [prop(node, "id") for node in switches]
    assert _bound(find(template, "ff-input")[0], "key") == str(SetupVar.SEARCH_KEY)
    assert len(switches) == 4 + len(ConditionGlyph), "one legend switch per glyph kind"
    assert all(_bound(node, "disabled") == f"!{SetupVar.HAS_SCENE}" for node in switches[4:]), "a legend switch needs a scene to act on"


# --- the controller's paths ------------------------------------------------------


def test_opening_a_run_publishes_it_and_a_new_run_resets_the_toggles_but_keeps_the_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths", tmp_path, monkeypatch)
        controller = _controller(app)
        panel = app.panel(FieldPanel.SETUP, SetupPanel)
        revealed: list[str] = []
        dock = app.dock
        assert dock is not None
        monkeypatch.setattr(dock, "active_panel", revealed.append)

        assert app.state[SetupVar.STATE] == str(SetupState.NONE)

        await controller.load(_flexure(tmp_path / "first"))
        await controller.settle()

        assert app.state[SetupVar.STATE] == str(SetupState.GROUPS) and app.state[SetupVar.BOUNDARY_COUNT] == "3"
        assert revealed == [str(FieldPanel.SETUP)], "a run without fields brings Setup forward"
        assert app.state[SetupVar.CAPTION] == "Glyphs Need the Mesh; Re-solve to Restore It."
        assert app.state[SetupVar.HAS_SCENE] is False, "the scene switches hide while the view draws nothing"
        assert all(row.get("dimmed") for row in cast("list[dict[str, object]]", app.state[SetupVar.BOUNDARY_ROWS])), "nothing has cells without fields or mesh"

        panel.on_toggle_glyph("boundary:RIGHTFACE")
        panel.on_kind("thermal", False)
        app.state[FieldVar.SELECTED_ENTITY] = "boundary:LEFTFACE"
        app.state[SetupVar.QUERY] = "face"
        panel.on_rows()

        assert app.state[FieldVar.GLYPH_ROWS_OFF] == ["boundary:RIGHTFACE"] and app.state[FieldVar.GLYPH_KINDS_OFF] == ["thermal"]
        assert app.state[SetupVar.BOUNDARY_COUNT] == "2 of 3"

        second = _flexure(tmp_path / "second")
        await controller.load(second)
        await controller.settle()

        assert app.state[FieldVar.GLYPH_ROWS_OFF] == [] and app.state[FieldVar.GLYPH_KINDS_OFF] == [] and app.state[FieldVar.SELECTED_ENTITY] == ""
        assert app.state[SetupVar.QUERY] == "face" and app.state[SetupVar.BOUNDARY_COUNT] == "2 of 3"

        panel.on_toggle_glyph("boundary:RIGHTFACE")
        app.state[FieldVar.SELECTED_ENTITY] = "boundary:LEFTFACE"
        await controller.load(second)
        await controller.settle()

        assert app.state[FieldVar.GLYPH_ROWS_OFF] == ["boundary:RIGHTFACE"] and app.state[FieldVar.SELECTED_ENTITY] == "boundary:LEFTFACE", "the same run reopening keeps the toggles"

        await controller.load(tmp_path / "missing")
        await controller.settle()

        assert app.state[SetupVar.STATE] == str(SetupState.NONE), "a run that fails to open clears the tab"

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
        assert app.state[FieldVar.SELECTED_ENTITY] == "" and app.state[SetupVar.ANNOUNCE] == "Selection cleared"

        app.state[SetupVar.QUERY] = "left"
        panel.on_select("")
        assert app.state[SetupVar.QUERY] == "" and app.state[SetupVar.SEARCH_KEY] == 1, "Escape clears the search first"

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

        panel.on_toggle_glyph("boundary:LEFTFACE")
        panel.on_toggle_glyph("boundary:LEFTFACE")
        assert app.state[FieldVar.GLYPH_ROWS_OFF] == []

        controller.highlightable = frozenset()
        panel.publish()
        panel.on_select("boundary:LEFTFACE")
        assert app.state[FieldVar.SELECTED_ENTITY] == "" and app.state[SetupVar.ANNOUNCE] == "LeftFace has no cells to highlight"

        app.state[SetupVar.FILTER] = str(FilterChoice.WITH_CONDITIONS)
        panel.show_all()
        assert app.state[SetupVar.FILTER] == str(FilterChoice.ALL)

        panel.on_visible(True)
        assert app.state[FieldVar.SETUP_SHOWN] is True

        app.state[FieldVar.EXPANSION] = str(Expansion.REVOLVE)
        panel.on_expansion()
        assert [row["label"] for row in cast("list[dict[str, str]]", app.state[SetupVar.FRAME_FACTS])][-1] == "Plane State", "only a revolving frame reads Shown As"

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
        assert all(row.get("dim_tip") == "Highlight Unavailable: The Scene's Groups Failed to Load" for row in rows)

    asyncio.run(run())
