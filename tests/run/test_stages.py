"""Stage vocabulary: enum order matches the driver pipeline, and StageEvent defaults to unfinished."""

from datetime import datetime, timezone

from femforge.run.stages import Stage, StageEvent


def test_stage_order_matches_the_pipeline() -> None:
    # Enum definition order IS the pipeline order (consumers iterate list(Stage)).
    assert [stage.value for stage in Stage] == [
        "prepare_geometry",
        "pre_mesh",
        "mesh",
        "post_mesh",
        "build_sim",
        "configure_sim",
        "write_sif",
        "run_solver",
        "mesh_stats",
        "publish",
        "cleanup",
    ]


def test_stage_event_starts_unfinished_and_round_trips() -> None:
    started = datetime(2026, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
    event = StageEvent(stage=Stage.MESH, started_at=started)
    assert event.finished_at is None
    restored = StageEvent.model_validate_json(event.model_dump_json())
    assert restored.stage is Stage.MESH
    assert restored.started_at == started
    assert restored.finished_at is None
