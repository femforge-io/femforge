"""Scene glyphs: labels, sizes and placements as pure functions, plus pyvista-gated layers drawn over synthetic scenes."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from femforge.common.vector import Direction, Vec3
from femforge.elmer.sections import SifSection
from femforge.frames import Axis, Axisymmetric, Cartesian2D, Solid
from femforge.meshing.groups import Bounds
from femforge.physical.conditions import (
    CalculateMagneticForce,
    CoilDrive,
    CoilTerminal,
    ConditionGlyph,
    ConditionUnion,
    Convection,
    CurrentDensity,
    FixedDisplacement,
    FixedTemperature,
    FixedVoltage,
    HeatFlux,
    HeatSource,
    JouleHeat,
    Magnetization,
    SurfaceForce,
    shipped_conditions,
)
from femforge.physical.units import U, quantity
from femforge.run.conditions import ConditionRecord, ConditionsFile, GroupConditions, SifKeyword, SolveComponent
from femforge.run.entities import Body, Boundary, BoundaryKind
from femforge.viz.field_display import DisplaySpec, BodyPaint, BodyStyle
from femforge.viz.glyphs import (
    ARROW_FLOOR,
    ARROW_SHARE,
    CIRCLE_SIDES,
    GLYPH_ACTORS,
    PIN_COUNT,
    PLAIN_BODY,
    EntityKind,
    EntityRef,
    Glyph,
    GlyphActor,
    GlyphLayer,
    GlyphPlacement,
    GlyphView,
    arrow_span,
    circles,
    clamp_ticks,
    dashed,
    glyph_label,
    glyphs_of,
    mirrored,
    pin_sample,
    plain_spec,
    revolve_angles,
    shown_diagonal,
)
from femforge.viz.symmetry import Basis, Expansion


if TYPE_CHECKING:
    import pyvista as pv
    from vtkmodules.vtkRenderingCore import vtkActor, vtkTextProperty
    from vtkmodules.vtkRenderingLabel import vtkPointSetToLabelHierarchy

    from femforge.viz.field_display import FieldScene

FLEXURE_DIAGONAL = math.hypot(0.1, 0.02)
"""`D` `[m]`: The diagonal of the 100 × 20 mm flexure section."""

FORBIDDEN = re.compile(r"[\[\]|⊙⊗]")
"""Glyphs VTK's text renderers drop, misdraw or crash on."""


def _planar() -> Cartesian2D:
    return Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=10 * U.mm)


def _flexure_bodies() -> list[Body]:
    return [Body(name="FLEXURE", mesh_id=1, centroid=Vec3(0.05, 0.01, 0.0), bbox=Bounds(low=Vec3(0, 0, 0), high=Vec3(0.1, 0.02, 0.0)))]


def _flexure_boundaries() -> list[Boundary]:
    return [
        Boundary(name="LEFTFACE", mesh_id=3, bodies=("FLEXURE",), kind=BoundaryKind.NAMED, sif_index=1, centroid=Vec3(0.0, 0.01, 0.0), normal=Direction(-1, 0, 0)),
        Boundary(name="RIGHTFACE", mesh_id=4, bodies=("FLEXURE",), kind=BoundaryKind.NAMED, sif_index=2, centroid=Vec3(0.1, 0.01, 0.0), normal=Direction(1, 0, 0)),
        Boundary(name="FLEXURE__EXT", mesh_id=2, bodies=("FLEXURE",), kind=BoundaryKind.EXTERNAL, centroid=Vec3(0.05, 0.01, 0.0)),
    ]


def _record(condition: ConditionUnion | None, block: int, components: list[tuple[int, float]], keywords: dict[str, float | str] | None = None, dropped: list[str] | None = None) -> ConditionRecord:
    kind = condition.kind if condition is not None else "far_field"
    section = condition.section if condition is not None else SifSection.BOUNDARY_CONDITION

    return ConditionRecord(
        kind=kind,
        condition=condition,
        section=section,
        block=block,
        block_name=f"BLOCK_{block}",
        keywords=[SifKeyword(name=name, value=value) for name, value in (keywords or {}).items()],
        solve_components=[SolveComponent(index=index, value=value) for index, value in components],
        dropped=dropped or [],
    )


def _flexure_conditions() -> ConditionsFile:
    held = _record(FixedDisplacement(), 1, [(1, 0.0), (2, 0.0)], {"Displacement 1": 0.0, "Displacement 2": 0.0}, ["z"])
    pull = _record(SurfaceForce(force=quantity(Vec3(100, 0, 0), "N")), 2, [(1, 10000.0), (2, 0.0)], {"Force 1": 500000.0, "Force 2": 0.0})

    return ConditionsFile(
        bodies=[GroupConditions(group="FLEXURE")],
        boundaries=[GroupConditions(group="LEFTFACE", conditions=[held]), GroupConditions(group="RIGHTFACE", conditions=[pull]), GroupConditions(group="FLEXURE__EXT")],
    )


# --- pure: refs, labels, sizes ----------------------------------------------------


def test_an_entity_ref_round_trips_its_id_and_rejects_anything_else() -> None:
    ref = EntityRef(EntityKind.BOUNDARY, "RIGHTFACE")

    assert ref.id == "boundary:RIGHTFACE"
    assert EntityRef.parse(ref.id) == ref
    assert EntityRef.parse("body:FLEXURE") == EntityRef(EntityKind.BODY, "FLEXURE")
    assert [EntityRef.parse(value) for value in ("RIGHTFACE", "face:RIGHTFACE", "body:", "")] == [None] * 4


SAMPLES: list[ConditionUnion] = [
    Magnetization(direction=Direction(0, 0, 1)),
    CurrentDensity(value=-2.5e6 * U.A / U.m**2),
    FixedTemperature(value=1500 * U.K),
    HeatFlux(value=500 * U.W / U.m**2),
    Convection(coefficient=10 * U.W / (U.m**2 * U.K), ext_temperature=293 * U.K),
    HeatSource(power_density=1.2e6 * U.W / U.m**3),
    FixedVoltage(value=0.5 * U.V),
    JouleHeat(),
    CoilDrive(current=12 * U.A),
    CoilTerminal(role="start"),
    CalculateMagneticForce(),
    FixedDisplacement(),
    SurfaceForce(force=quantity(Vec3(100, 0, 0), "N")),
]
"""One authored instance of every shipped condition, in ``ConditionUnion`` order."""


def test_every_shipped_condition_has_a_sample_and_a_label_free_of_glyphs_vtk_cannot_draw() -> None:
    assert [type(sample) for sample in SAMPLES] == list(shipped_conditions())

    labels = [glyph_label(sample, revolved=False) for sample in SAMPLES]

    assert labels == [None, "2.5 A/mm²", "1500 K", "q 500 W/m²", "h 10 · T 293 K", "q 1.2 MW/m³", "0.5 V", "Joule Heat", "Coil 12 A", "Start", None, None, "100 N"]
    assert all(FORBIDDEN.search(label) is None for label in labels if label is not None)


def test_a_revolved_load_reads_as_a_total_and_a_held_face_names_only_its_solved_axes() -> None:
    assert glyph_label(SurfaceForce(force=quantity(Vec3(0, 0, 100), "N")), revolved=True) == "100 N Total"
    assert glyph_label(FixedDisplacement(x=None), revolved=False, dropped=["z"]) == "Y Fixed"
    assert glyph_label(FixedDisplacement(y=None), revolved=False) == "X, Z Fixed"
    assert glyph_label(FixedDisplacement(x=None, y=None, z=None), revolved=False) is None
    assert glyph_label(FixedDisplacement(x=None, y=None), revolved=False, dropped=["z"]) is None
    assert glyph_label(FixedDisplacement(x=quantity(1e-4, "m"), z=None), revolved=False) == "0.1 mm"


def test_an_arrow_is_a_quarter_of_the_diagonal_for_the_largest_load_and_stays_outside_the_body() -> None:
    face = Vec3(0.05, 0.0, 0.0)
    outward = Vec3(1.0, 0.0, 0.0)

    tail, head = arrow_span(face, Vec3(1.0, 0.0, 0.0), outward, 1.0, FLEXURE_DIAGONAL)

    assert tail == face and (head - tail).magnitude() == pytest.approx(0.025495, abs=1e-6), "a pull keeps its tail on the face"

    tail, head = arrow_span(face, Vec3(-2.0, 0.0, 0.0), outward, 1.0, FLEXURE_DIAGONAL)

    assert head == face and tail.x == pytest.approx(0.05 + 0.025495, abs=1e-6), "a push keeps its head on the face"

    tail, head = arrow_span(face, Vec3(1.0, 0.0, 0.0), None, 0.1, FLEXURE_DIAGONAL)

    assert tail == face and (head - tail).magnitude() == pytest.approx(ARROW_FLOOR * FLEXURE_DIAGONAL) == pytest.approx(0.0061188, abs=1e-7)
    assert ARROW_SHARE * FLEXURE_DIAGONAL == pytest.approx(0.025495, abs=1e-6)


def test_magnet_arrows_repeat_every_45_degrees_of_the_sweep_up_to_eight() -> None:
    assert len(revolve_angles(360.0)) == 8
    assert revolve_angles(90.0) == (0.0, 45.0, 90.0)
    assert revolve_angles(15.0) == (0.0,)


def test_the_shown_diagonal_follows_the_expansion_of_the_section() -> None:
    bounds = (0.0, 2.0, 0.0, 1.0, 0.0, 0.0)

    assert shown_diagonal(bounds, Expansion.SECTION) == pytest.approx(math.hypot(2.0, 1.0))
    assert shown_diagonal(bounds, Expansion.MIRROR) == pytest.approx(math.hypot(4.0, 1.0))
    assert shown_diagonal(bounds, Expansion.REVOLVE) == pytest.approx(math.hypot(4.0, 4.0, 1.0))


def test_a_mirrored_glyph_negates_its_radial_components() -> None:
    glyph = Glyph(group="A", target=EntityKind.BOUNDARY, kind=ConditionGlyph.SURFACE_FORCE, anchor=Vec3(1, 2, 0), normal=Vec3(1, 0, 0), vector=Vec3(0.6, 0.8, 0))
    flipped = mirrored(glyph)

    assert flipped.anchor == Vec3(-1, 2, 0) and flipped.normal == Vec3(-1, 0, 0) and flipped.vector == Vec3(-0.6, 0.8, 0)
    assert mirrored(glyph.model_copy(update={"normal": None, "vector": None})).normal is None
    assert flipped.ref == EntityRef(EntityKind.BOUNDARY, "A")


def test_clamp_ticks_run_every_two_percent_of_the_diagonal_on_the_outside_at_45_degrees() -> None:
    face = np.array([[[0.0, 0.0, 0.0], [0.0, 0.01, 0.0]], [[0.0, 0.01, 0.0], [0.0, 0.01, 0.0]], [[0.0, 0.01, 0.0], [0.0, 0.02, 0.0]]])

    ticks = clamp_ticks(face, Vec3(-1.0, 0.0, 0.0), FLEXURE_DIAGONAL)
    spacing = 0.02 * FLEXURE_DIAGONAL

    assert len(ticks) == math.floor(0.02 / spacing) + 1, "a zero-length segment adds none"
    assert np.allclose(ticks[:, 0, 1], spacing * np.arange(len(ticks)))
    slant = ticks[:, 1] - ticks[:, 0]
    assert np.all(slant[:, 0] < 0.0) and np.allclose(-slant[:, 0], slant[:, 1]), "every tick leans out of the body at 45°"


def test_pins_sample_at_most_pin_count_points_and_dashes_break_a_segment() -> None:
    assert len(pin_sample(np.zeros((100, 3)))) == PIN_COUNT
    assert len(pin_sample(np.zeros((5, 3)))) == 5

    pieces = dashed(np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]]), 0.1)

    assert len(pieces) == 5 and np.allclose(pieces[:, 1, 0] - pieces[:, 0, 0], 0.1)


def test_circles_are_closed_polygons_about_each_center() -> None:
    sides = circles(np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]]), 2.0)

    assert sides.shape == (2 * CIRCLE_SIDES, 2, 3)
    assert np.allclose(np.linalg.norm(sides[:CIRCLE_SIDES, 0], axis=1), 2.0)
    assert np.allclose(sides[CIRCLE_SIDES - 1 : CIRCLE_SIDES, 1], sides[0:1, 0])


# --- pure: the glyphs of a run's records ---------------------------------------------


def test_the_flexure_draws_a_pull_arrow_and_a_held_face() -> None:
    glyphs = glyphs_of(_flexure_conditions(), _flexure_bodies(), _flexure_boundaries(), _planar())

    assert [(glyph.group, glyph.kind) for glyph in glyphs] == [("LEFTFACE", ConditionGlyph.FIXED_DISPLACEMENT), ("RIGHTFACE", ConditionGlyph.SURFACE_FORCE)]
    held, pull = glyphs
    assert held.held == (True, True) and held.vector is None and held.label is None and held.normal == Vec3(-1, 0, 0)
    assert pull.vector == Vec3(1.0, 0.0, 0.0) and pull.label == "100 N" and pull.share == 1.0 and pull.anchor == Vec3(0.1, 0.01, 0.0)


def test_loads_share_the_largest_and_bodies_get_magnet_arrows_and_drive_markers() -> None:
    bodies = [
        Body(name="MAGNET", mesh_id=1, centroid=Vec3(0.01, 0.0, 0.0), bbox=Bounds(low=Vec3(0.008, -0.005, 0), high=Vec3(0.015, 0.005, 1e-18))),
        Body(name="COIL", mesh_id=2, centroid=Vec3(0.02, 0.0, 0.0)),
        Body(name="LOST", mesh_id=3),
    ]
    boundaries = [
        Boundary(name="TOP", mesh_id=4, bodies=("MAGNET",), kind=BoundaryKind.NAMED, centroid=Vec3(0, 1, 0)),
        Boundary(name="SIDE", mesh_id=5, bodies=("MAGNET",), kind=BoundaryKind.NAMED, centroid=Vec3(1, 0, 0), normal=Direction(1, 0, 0)),
        Boundary(name="NOWHERE", mesh_id=6, bodies=("MAGNET",), kind=BoundaryKind.NAMED),
    ]
    big = SurfaceForce(force=quantity(Vec3(0, 400, 0), "N"))
    small = SurfaceForce(force=quantity(Vec3(0, 100, 0), "N"))
    conditions = ConditionsFile(
        bodies=[
            GroupConditions(group="MAGNET", conditions=[_record(Magnetization(direction=Direction(0, 0, 1)), 1, [(1, 0.0), (2, 1.0)]), _record(CalculateMagneticForce(), 1, [])]),
            GroupConditions(group="COIL", conditions=[_record(CurrentDensity(value=-3e6 * U.A / U.m**2), 1, [], {"Current Density": -3e6}), _record(CurrentDensity(value=1e6 * U.A / U.m**2), 2, [], {"Note": "Logical True"})]),
            GroupConditions(group="LOST", conditions=[_record(JouleHeat(), 3, [])]),
            GroupConditions(group="GONE", conditions=[_record(JouleHeat(), 3, [])]),
        ],
        boundaries=[
            GroupConditions(group="TOP", conditions=[_record(big, 1, [(1, 0.0), (2, 400.0)]), _record(None, 2, [])]),
            GroupConditions(group="SIDE", conditions=[_record(small, 3, [(1, 0.0), (2, 100.0)]), _record(FixedDisplacement(x=quantity(1e-4, "m"), z=None), 4, [(1, 1e-4), (2, 0.0)])]),
            GroupConditions(group="NOWHERE", conditions=[_record(big, 5, [(1, 0.0), (2, 400.0)])]),
        ],
    )

    glyphs = glyphs_of(conditions, bodies, boundaries, Axisymmetric())

    magnet, coil, unsigned, top, side, moved = glyphs
    assert magnet.vector == Vec3(0, 1, 0) and magnet.extent == pytest.approx(0.0035) and magnet.reach == pytest.approx(0.005), "a flat section ignores its vanishing Z extent"
    assert coil.vector == Vec3(0, 0, -1) and coil.label == "3 A/mm²", "the out-of-plane marker takes the sign of the emitted drive"
    assert unsigned.vector == Vec3(0, 0, 1), "a drive without a numeric keyword reads as positive"
    assert top.share == 1.0 and side.share == pytest.approx(0.25) and top.label == "400 N Total"
    assert moved.kind is ConditionGlyph.FIXED_DISPLACEMENT and moved.vector == Vec3(1, 0, 0) and moved.held == (True, True) and moved.label == "0.1 mm"


def test_a_record_without_solve_components_draws_no_direction() -> None:
    bodies = [Body(name="MAGNET", mesh_id=1, centroid=Vec3(0, 0, 0), bbox=Bounds(low=Vec3(0, 0, 0), high=Vec3(1, 1, 1)))]
    boundaries = [Boundary(name="TIP", mesh_id=2, bodies=("MAGNET",), kind=BoundaryKind.NAMED, centroid=Vec3(1, 0, 0), normal=Direction(1, 0, 0))]
    unloaded = SurfaceForce(force=quantity(Vec3(0, 0, 0), "N"))
    conditions = ConditionsFile(
        bodies=[GroupConditions(group="MAGNET", conditions=[_record(Magnetization(direction=Direction(0, 0, 1)), 1, [(1, 0.0), (2, 0.0), (3, 0.0)])])],
        boundaries=[GroupConditions(group="TIP", conditions=[_record(unloaded, 1, [])])],
    )

    magnet, load = glyphs_of(conditions, bodies, boundaries, Solid())

    assert magnet.vector is None and magnet.reach == 0.0, "a zero direction has no arrow"
    assert load.vector is None and load.share == 1.0 and load.label == "0 N"


def test_a_run_without_loads_keeps_its_shares_and_a_3d_drive_draws_no_marker() -> None:
    bodies = [Body(name="COIL", mesh_id=1, centroid=Vec3(0, 0, 0))]
    conditions = ConditionsFile(bodies=[GroupConditions(group="COIL", conditions=[_record(CoilDrive(current=12 * U.A), 1, [])])])

    glyphs = glyphs_of(conditions, bodies, [], Solid())

    assert len(glyphs) == 1 and glyphs[0].vector is None and glyphs[0].share == 1.0 and glyphs[0].label == "Coil 12 A"


# --- pure: views and placements ----------------------------------------------------


def test_the_default_view_draws_every_kind_with_labels_and_the_outline() -> None:
    view = GlyphView()

    assert view.kinds == frozenset(ConditionGlyph) and view.outline and not view.ghost and view.selected is None and view.scale == 1.0


def test_a_placement_mirrors_or_revolves_solve_frame_points() -> None:
    points = np.array([[1.0, 2.0, 0.0]])
    flat = GlyphPlacement()
    mirror = GlyphPlacement(expansion=Expansion.MIRROR, basis=Basis.DEFAULT)
    revolve = GlyphPlacement(expansion=Expansion.REVOLVE, sweep=90.0, basis=Basis.DEFAULT)
    unrevolvable = GlyphPlacement(expansion=Expansion.REVOLVE)

    assert not flat.revolved and not unrevolvable.revolved and revolve.revolved
    assert len(flat.copies(points)) == 1 and np.allclose(flat.placed(points), points)
    assert np.allclose(mirror.copies(points)[1], [[-1.0, 2.0, 0.0]])
    assert np.allclose(revolve.copies(points)[0], [[1.0, 0.0, 2.0]]), "the section's Y is the default frame's Z axis"
    assert np.allclose(revolve.placed(points, math.pi / 2), [[0.0, 1.0, 2.0]]), "a quarter turn carries the radial to +Y"
    assert flat.angles() == (0.0,) and revolve.angles() == pytest.approx((0.0, math.pi / 4, math.pi / 2))


def test_plain_bodies_paint_every_body_grey_and_keep_its_opacity() -> None:
    spec = DisplaySpec(body_styles=(BodyStyle(body="A", paint=BodyPaint.COLORMAP, opacity=0.5),))

    plain = plain_spec(spec, ["A", "B"])

    assert [(style.body, style.paint, style.color, style.opacity) for style in plain.body_styles] == [("A", BodyPaint.SOLID, PLAIN_BODY, 0.5), ("B", BodyPaint.SOLID, PLAIN_BODY, 1.0)]
    assert spec.body_styles[0].paint is BodyPaint.COLORMAP, "the spec the controls own is left alone"


def test_a_layer_without_a_frame_takes_the_solid_one() -> None:
    assert GlyphLayer([]).frame.model_dim == 3


# --- pyvista-gated: layers over synthetic scenes --------------------------------------


def _flexure_grid() -> pv.UnstructuredGrid:  # pragma: no cover - helper for the pyvista-gated tests below
    import pyvista as pv

    columns, rows = 4, 2
    points = np.array([(0.1 * column / columns, 0.02 * row / rows, 0.0) for row in range(rows + 1) for column in range(columns + 1)], dtype=np.float64)
    quads = [[row * (columns + 1) + column + offset for offset in (0, 1, columns + 2, columns + 1)] for row in range(rows) for column in range(columns)]
    left = [[0, 5], [5, 10]]
    right = [[4, 9], [9, 14]]
    cells = np.concatenate([np.array([[4, *quad] for quad in quads]).ravel(), np.array([[2, *line] for line in left + right]).ravel()])
    types = np.array([pv.CellType.QUAD] * len(quads) + [pv.CellType.LINE] * 4)
    grid = pv.UnstructuredGrid(cells, types, points)
    grid.cell_data["GeometryIds"] = np.array([1] * len(quads) + [101, 101, 102, 102], dtype=np.int32)
    grid.point_data["displacement"] = np.column_stack([0.01 * points[:, 0], np.zeros(len(points)), np.zeros(len(points))])

    return grid


def _flexure_scene(frame: Cartesian2D | Axisymmetric | None = None) -> FieldScene:  # pragma: no cover - helper for the pyvista-gated tests below
    from femforge.run.entities import Bodies, Boundaries
    from femforge.viz.field_display import FieldScene, list_fields, model_dim_of, split_by_body

    grid = _flexure_grid()
    bodies = Bodies(_flexure_bodies())
    scene = FieldScene(
        grid=grid,
        blocks=split_by_body(grid, bodies),
        fields=list_fields(grid),
        model_dim=model_dim_of(grid),
        run_id="flexure",
        bodies=bodies,
        frame=frame or _planar(),
        boundaries=Boundaries(_flexure_boundaries()),
    )
    scene.glyphs = GlyphLayer(glyphs_of(_flexure_conditions(), _flexure_bodies(), _flexure_boundaries(), scene.frame), bodies=_flexure_bodies(), boundaries=_flexure_boundaries(), frame=scene.frame)
    scene.spec = DisplaySpec(field="displacement", mode=scene.spec.mode, warp=None, streamlines=None)

    return scene


def _drawn(scene: FieldScene, view: GlyphView | None, spec: DisplaySpec | None = None) -> pv.Plotter:  # pragma: no cover - helper for the pyvista-gated tests below
    import pyvista as pv

    plotter = pv.Plotter(off_screen=True)
    scene.glyph_view = view
    if spec is not None:
        scene.spec = spec

    scene.render(plotter)

    return plotter


def _glyph_names(plotter: pv.Plotter) -> list[str]:  # pragma: no cover - helper for the pyvista-gated tests below
    return sorted(str(name) for name in plotter.renderer.actors if str(name).startswith("glyph-"))


def _actor(plotter: pv.Plotter, name: str) -> vtkActor:  # pragma: no cover - helper for the pyvista-gated tests below
    from vtkmodules.vtkRenderingCore import vtkActor

    actor = plotter.renderer.actors[name]
    assert isinstance(actor, vtkActor)

    return actor


def _bounds(plotter: pv.Plotter, name: GlyphActor) -> tuple[float, ...]:  # pragma: no cover - helper for the pyvista-gated tests below
    return tuple(_actor(plotter, str(name)).GetBounds())


def _hierarchy(plotter: pv.Plotter) -> vtkPointSetToLabelHierarchy:  # pragma: no cover - helper for the pyvista-gated tests below
    from vtkmodules.vtkRenderingCore import vtkActor2D
    from vtkmodules.vtkRenderingLabel import vtkLabelPlacementMapper, vtkPointSetToLabelHierarchy

    actor = plotter.renderer.actors["glyph-labels-labels"]
    assert isinstance(actor, vtkActor2D)
    mapper = actor.GetMapper()
    assert isinstance(mapper, vtkLabelPlacementMapper)
    hierarchy = mapper.GetInputAlgorithm()
    assert isinstance(hierarchy, vtkPointSetToLabelHierarchy)

    return hierarchy


def test_a_flexure_draws_named_glyph_actors_the_mirror_can_serialize() -> None:
    pytest.importorskip("pyvista")
    from vtkmodules.vtkRenderingCore import vtkActor, vtkActor2D
    from vtkmodules.vtkRenderingLabel import vtkLabelPlacementMapper

    plotter = _drawn(_flexure_scene(), GlyphView())
    names = _glyph_names(plotter)
    condition_actors = [name for name in names if name.removesuffix("-labels") in {str(actor) for actor in GLYPH_ACTORS}]
    props = plotter.renderer.GetViewProps()
    drawn = [props.GetItemAsObject(index) for index in range(props.GetNumberOfItems())]

    assert names == sorted(["glyph-arrows", "glyph-halos", "glyph-lines", "glyph-marks", "glyph-outline", "glyph-triad", "glyph-labels-labels"])
    assert len(condition_actors) <= len(GLYPH_ACTORS) == 7
    for prop in drawn:
        labelled = isinstance(prop, vtkActor2D) and isinstance(prop.GetMapper(), vtkLabelPlacementMapper)
        assert isinstance(prop, vtkActor) or labelled, f"{type(prop).__name__} would blank the mirror"


def test_kinds_off_leave_no_condition_glyph_and_none_removes_every_glyph_actor() -> None:
    pytest.importorskip("pyvista")

    scene = _flexure_scene()
    plotter = _drawn(scene, GlyphView(kinds=frozenset(), outline=False))

    assert _glyph_names(plotter) == []

    scene.glyph_view = GlyphView()
    scene.update(plotter, scene.spec)
    assert "glyph-arrows" in _glyph_names(plotter)

    scene.glyph_view = None
    scene.update(plotter, scene.spec)
    assert _glyph_names(plotter) == []


def test_a_hidden_row_drops_its_glyph_and_an_unchanged_view_draws_nothing_again() -> None:
    pytest.importorskip("pyvista")

    scene = _flexure_scene()
    plotter = _drawn(scene, GlyphView(hidden=frozenset({"boundary:RIGHTFACE"}), outline=False))
    layer = scene.glyphs
    assert layer is not None
    arrows_before = "glyph-arrows" in _glyph_names(plotter)
    drawn = layer.names

    scene.update(plotter, scene.spec)

    assert not arrows_before and "glyph-lines" in _glyph_names(plotter)
    assert layer.names == drawn


def test_the_arrow_tail_follows_the_mean_displacement_of_its_face() -> None:
    pytest.importorskip("pyvista")

    still = _bounds(_drawn(_flexure_scene(), GlyphView()), GlyphActor.ARROWS)
    scene = _flexure_scene()
    moved = _bounds(_drawn(scene, GlyphView(), scene.spec.model_copy(update={"warp": "displacement", "warp_scale": 2.0})), GlyphActor.ARROWS)

    assert still[0] == pytest.approx(0.1, abs=1e-7)
    assert moved[0] == pytest.approx(0.1 + 2.0 * 0.01 * 0.1, abs=1e-7), "the right face moves by 1 mm, doubled"


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_the_label_font_scales_with_the_window_pixels_per_css_pixel(scale: float) -> None:
    pytest.importorskip("pyvista")

    plotter = _drawn(_flexure_scene(), GlyphView(scale=scale))

    text = cast("vtkTextProperty", _hierarchy(plotter).GetTextProperty())  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped

    assert text.GetFontSize() == round(14 * scale)
    assert _actor(plotter, "glyph-lines").GetProperty().GetLineWidth() == pytest.approx(4.0 * scale)


def test_a_selected_body_gets_an_accent_silhouette_and_fill_and_a_boundary_an_accent_line() -> None:
    pytest.importorskip("pyvista")

    scene = _flexure_scene()
    plotter = _drawn(scene, GlyphView(selected="body:FLEXURE", accent="#ff0000"))

    assert {"glyph-selection", "glyph-selection-fill"} <= set(_glyph_names(plotter))
    assert _actor(plotter, "glyph-selection").GetProperty().GetColor() == pytest.approx((1.0, 0.0, 0.0))

    hidden = scene.spec.model_copy(update={"hidden_bodies": ("FLEXURE",)})
    plotter = _drawn(scene, GlyphView(selected="body:FLEXURE"), hidden)
    assert "glyph-selection" in _glyph_names(plotter) and "glyph-selection-fill" not in _glyph_names(plotter), "a hidden body shows its silhouette only"

    plotter = _drawn(_flexure_scene(), GlyphView(selected="boundary:RIGHTFACE"))
    line = _bounds(plotter, GlyphActor.SELECTION)
    assert line[0] == pytest.approx(0.1) and line[1] == pytest.approx(0.1)


def test_the_outline_and_triad_label_the_solve_axes_by_their_authored_directions() -> None:
    pytest.importorskip("pyvista")
    from vtkmodules.vtkCommonCore import vtkStringArray
    from vtkmodules.vtkCommonDataModel import vtkDataSet

    plotter = _drawn(_flexure_scene(), GlyphView(kinds=frozenset()))
    points = cast("object", _hierarchy(plotter).GetInput())  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped
    assert isinstance(points, vtkDataSet)
    labels = cast("object", points.GetPointData().GetAbstractArray("labels"))  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped
    assert isinstance(labels, vtkStringArray)

    assert [labels.GetValue(index) for index in range(labels.GetNumberOfValues())] == ["X = Authored (1, 0, 0)", "Y = Authored (0, 1, 0)"]
    assert _bounds(plotter, GlyphActor.OUTLINE)[1] == pytest.approx(0.1)


def test_the_ghost_draws_the_authored_bodies_and_the_dashed_prism_they_are_checked_against() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _flexure_scene()
    assert scene.glyphs is not None
    scene.glyphs.authored = pv.Box(bounds=(0.0, 0.1, 0.0, 0.02, -0.004, 0.006))  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped
    plotter = _drawn(scene, GlyphView(ghost=True))

    assert {"glyph-ghost", "glyph-hull"} <= set(_glyph_names(plotter))
    hull = _bounds(plotter, GlyphActor.HULL)
    assert hull[4] == pytest.approx(-0.005) and hull[5] == pytest.approx(0.006), "the prism runs ±5 mm through the 10 mm depth; the ghost pokes out past it"


def test_a_revolved_section_repeats_the_magnet_arrow_every_45_degrees_and_sweeps_the_tints() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _flexure_scene(Axisymmetric())
    assert scene.glyphs is not None
    magnet = Glyph(group="FLEXURE", target=EntityKind.BODY, kind=ConditionGlyph.MAGNETIZATION, anchor=Vec3(0.05, 0.01, 0.0), vector=Vec3(0, 1, 0), extent=0.01, reach=0.01)
    scene.glyphs.glyphs = [*scene.glyphs.glyphs, magnet]
    scene.glyphs.authored = pv.Box(bounds=(-0.1, 0.1, 0.0, 0.02, -0.1, 0.1))  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped
    revolved = scene.spec.model_copy(update={"expansion": Expansion.REVOLVE, "sweep": 360.0})
    plotter = _drawn(scene, GlyphView(selected="boundary:LEFTFACE", ghost=True), revolved)
    single: int = pv.Arrow().n_points  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped
    arrows = cast("object", _actor(plotter, "glyph-arrows").GetMapper().GetInputDataObject(0, 0))  # pyright: ignore[reportUnknownMemberType]  # the VTK and pyvista stubs leave this partially untyped
    assert isinstance(arrows, pv.PolyData)

    assert arrows.n_points == single * (8 + 1), "eight magnet arrows and the load once"
    assert {"glyph-faces", "glyph-plane", "glyph-selection-fill", "glyph-ghost", "glyph-hull"} <= set(_glyph_names(plotter))
    assert "glyph-lines" not in plotter.renderer.actors, "a revolved tint is a surface"

    mirrored_view = scene.spec.model_copy(update={"expansion": Expansion.MIRROR})
    plotter = _drawn(scene, GlyphView(), mirrored_view)
    assert _bounds(plotter, GlyphActor.ARROWS)[0] < -0.1, "a mirrored load arrow points the other way"


def test_a_3d_held_face_tint_is_lifted_off_the_face_and_pinned() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv
    from femforge.run.entities import Bodies, Boundaries
    from femforge.viz.field_display import FieldScene, list_fields, split_by_body

    cube = [(0.0, 0, 0), (1.0, 0, 0), (1.0, 1, 0), (0.0, 1, 0), (0.0, 0, 1), (1.0, 0, 1), (1.0, 1, 1), (0.0, 1, 1)]
    points = np.array(cube + [(x + 1.0, y, z) for x, y, z in cube], dtype=np.float64)
    cells = np.array([8, *range(0, 8), 8, *range(8, 16), 4, 9, 10, 14, 13])
    grid = pv.UnstructuredGrid(cells, np.array([pv.CellType.HEXAHEDRON, pv.CellType.HEXAHEDRON, pv.CellType.QUAD]), points)
    grid.cell_data["GeometryIds"] = np.array([1, 1, 101], dtype=np.int32)
    grid.point_data["temperature"] = np.asarray(grid.points)[:, 0]
    bodies = [Body(name="BAR", mesh_id=1, centroid=Vec3(1, 0.5, 0.5))]
    tip = Boundary(name="TIP", mesh_id=2, bodies=("BAR",), kind=BoundaryKind.NAMED, sif_index=1, centroid=Vec3(2, 0.5, 0.5), normal=Direction(1, 0, 0))
    roller = FixedDisplacement(y=None)
    conditions = ConditionsFile(bodies=[GroupConditions(group="BAR")], boundaries=[GroupConditions(group="TIP", conditions=[_record(roller, 1, [(1, 0.0), (3, 0.0)])])])
    scene = FieldScene(grid=grid, blocks=split_by_body(grid, Bodies(bodies)), fields=list_fields(grid), model_dim=3, run_id="bar", bodies=Bodies(bodies), frame=Solid(), boundaries=Boundaries([tip]))
    scene.glyphs = GlyphLayer(glyphs_of(conditions, bodies, [tip], Solid()), bodies=bodies, boundaries=[tip], frame=Solid())
    scene.spec = DisplaySpec(field="temperature", streamlines=None)

    plotter = _drawn(scene, GlyphView(selected="boundary:TIP"))
    diagonal = math.sqrt(6.0)

    assert _bounds(plotter, GlyphActor.FACES)[0] == pytest.approx(2.0 + 1e-3 * diagonal)
    assert {"glyph-marks", "glyph-arrows", "glyph-selection-fill"} <= set(_glyph_names(plotter)), "pins, with roller balls for the free axis"
    assert "glyph-outline" not in plotter.renderer.actors, "a solid run has no section to outline"
    assert scene.glyphs.bounds_of(EntityRef(EntityKind.BOUNDARY, "TIP")) == pytest.approx((2.0, 2.0, 0.0, 1.0, 0.0, 1.0))
    assert scene.glyphs.bounds_of(EntityRef(EntityKind.BODY, "BAR")) == pytest.approx((0.0, 2.0, 0.0, 1.0, 0.0, 1.0))


def test_plain_bodies_paint_every_body_in_its_own_grey_actor() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_display import SceneActor

    scene = _flexure_scene()
    plotter = _drawn(scene, GlyphView(plain_bodies=True), plain_spec(scene.spec, ["FLEXURE"]))
    actor = _actor(plotter, SceneActor.STYLED.of("FLEXURE"))

    assert actor.GetProperty().GetColor() == pytest.approx(pv.Color(PLAIN_BODY).float_rgb)


class _Groups:  # pragma: no cover - helper for the pyvista-gated tests below
    """Mesh groups whose every group is the flexure grid's cells of one GeometryIds value."""

    def __init__(self) -> None:
        self.grid = _flexure_grid()

    def _of(self, mesh_id: int) -> pv.UnstructuredGrid:
        ids: np.typing.NDArray[np.int64] = np.asarray(self.grid.cell_data["GeometryIds"], dtype=np.int64)

        picked = cast("np.typing.NDArray[np.bool_]", ids == mesh_id)

        return cast("pv.UnstructuredGrid", self.grid.extract_cells(np.flatnonzero(picked)))  # pyright: ignore[reportUnknownMemberType]  # pyvista's extract_cells index parameter is partially untyped

    def body(self, body: Body) -> pv.UnstructuredGrid:
        return self._of(body.mesh_id)

    def boundary(self, boundary: Boundary) -> pv.UnstructuredGrid:
        return self._of({"LEFTFACE": 101, "RIGHTFACE": 102}.get(boundary.name, 0))


def test_groups_the_fields_lack_come_from_the_mesh_and_a_mesh_only_scene_draws_them_grey() -> None:
    pytest.importorskip("pyvista")
    from femforge.run.entities import Bodies, Boundaries
    from femforge.run.mesh_groups import MeshGroups
    from femforge.viz.field_display import SceneActor
    from femforge.viz.glyphs import GroupScene

    groups = cast("MeshGroups", _Groups())
    layer = GlyphLayer(glyphs_of(_flexure_conditions(), _flexure_bodies(), _flexure_boundaries(), _planar()), bodies=_flexure_bodies(), boundaries=_flexure_boundaries(), frame=_planar(), mesh_groups=groups)
    scene = GroupScene(groups, bodies=Bodies(_flexure_bodies()), boundaries=Boundaries(_flexure_boundaries()), frame=_planar(), run_id="flexure")
    scene.glyphs = layer
    scene.glyph_view = GlyphView(selected="boundary:LEFTFACE")
    import pyvista as pv

    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)

    assert SceneActor.STYLED.of("FLEXURE") in plotter.renderer.actors
    assert {"glyph-arrows", "glyph-lines", "glyph-selection"} <= set(_glyph_names(plotter))
    assert layer.has(EntityRef(EntityKind.BODY, "FLEXURE")) and layer.has(EntityRef(EntityKind.BOUNDARY, "RIGHTFACE"))
    assert not layer.has(EntityRef(EntityKind.BOUNDARY, "FLEXURE__EXT")) and not layer.has(EntityRef(EntityKind.BODY, "GONE"))
    assert layer.bounds_of(EntityRef(EntityKind.BOUNDARY, "FLEXURE__EXT")) is None, "no cells and no measured box"
    assert scene.group_cells(EntityRef(EntityKind.BODY, "GONE")) is None


def test_a_bundle_opens_its_glyph_layer_from_its_sidecars(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    from femforge.common.files import BundleFile
    from femforge.run.entities import BodyRecord, BoundaryRecord, EntitiesFile
    from femforge.run.inspect import Run
    from femforge.viz.field_display import FieldScene
    from femforge.viz.glyphs import GroupScene

    _flexure_grid().save(str(tmp_path / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped
    records = {body.mesh_id: BodyRecord(name=body.name, centroid=body.centroid, bbox=body.bbox) for body in _flexure_bodies()}
    edges = {boundary.mesh_id: BoundaryRecord(name=boundary.name, bodies=boundary.bodies, named=True, centroid=boundary.centroid, normal=boundary.normal) for boundary in _flexure_boundaries()}
    _ = (tmp_path / BundleFile.ENTITIES).write_text(EntitiesFile(bodies=records, boundaries=edges).model_dump_json(), encoding="utf-8")
    _ = _flexure_conditions().write(tmp_path)
    run = Run(tmp_path)

    layer = GlyphLayer.from_run(run, FieldScene.from_run(run))
    bare = GlyphLayer.from_run(run)

    assert [glyph.group for glyph in layer.glyphs] == ["LEFTFACE", "RIGHTFACE"] and layer.mesh_groups is None and layer.authored is None
    assert bare.frame.model_dim == 3, "without a manifest the frame is the solid one"
    assert GroupScene.from_run(run) is None, "a bundle without its Elmer mesh has no mesh-only scene"
