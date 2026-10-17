"""Lift the ball off its seat step by step in the shelled 2D seat and write a ParaView animation of the field."""

from pathlib import Path

import numpy as np

import femforge as ff
from main import config_for
from paths import RUNS_DIR, TELEMETRY_DIR


def z_sweep() -> ff.Driver:
    """Solve the shelled 2D seat as the ball lifts from 0.25 to 8 mm, then write a ParaView animation of the steps."""
    driver = ff.Driver(runs_dir=RUNS_DIR, telemetry=TELEMETRY_DIR, docker=True)
    vtu_files: list[Path] = []

    for airgap in np.arange(0.25, 8.0, 0.125):
        config = config_for(
            as_2d=True,
            airgap=float(airgap),
            mesh_size=1.0,
            domain_padding=50.0,
            shell_thickness=2.0,
        )

        result = driver.evaluate(config)

        vtu = ff.Run.from_result(result).vtu_path if result.run_dir is not None else None

        if vtu is None:
            ff.log.bind(run_id=result.run_id, airgap=float(airgap)).warning("Run has no field output; left out of the animation.")
            continue

        vtu_files.append(vtu)

    datasets = [f'    <DataSet file="{vtu}" timestep="{step}" />' for step, vtu in enumerate(vtu_files)]
    collection = [
        '<?xml version="1.0"?>',
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
        "  <Collection>",
        *datasets,
        "  </Collection>",
        "</VTKFile>",
    ]

    animation = RUNS_DIR / "z-sweep" / "animation.pvd"
    animation.parent.mkdir(parents=True, exist_ok=True)

    _ = animation.write_text("\n".join(collection) + "\n", encoding="utf-8")

    ff.log.bind(path=str(animation), steps=len(vtu_files)).info("Animation written.")

    return driver


if __name__ == "__main__":
    _ = z_sweep()
