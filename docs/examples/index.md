# Examples

Each example is a folder in the repository's [`examples/`](https://github.com/femforge-io/femforge/tree/main/examples): a script that builds a small model, solves it and opens the result in the workbench. Clone the repository, start Docker and run one from the repository root; [Install](../overview/install.md) covers uv and Docker.

```console
uv run examples/thermal/main.py
```

The quickstart project is the fourth. `uvx femforge new` writes it anywhere, with no clone.

<div class="grid cards" markdown>

-   [![The quickstart workbench: a disc magnet over a steel plate, revolved from its axisymmetric section into 3D, with field lines drawn through the air](../assets/examples/magnet-plate.webp)](../overview/quickstart.md)

    **[Magnet and plate](../overview/quickstart.md)**

    ---

    A disc magnet pulls on a steel plate across four air gaps, solved on the axisymmetric section and shown revolved into 3D. The project `uvx femforge new` writes.

    Magnetostatics · a sweep over a dimension · build123d geometry

-   [![The bearing-seat workbench: the Runs list at the left, the 4 mm magnet with its shell revolved into 3D with field lines, and the run's details below](../assets/examples/bearing-seat.webp)](bearing-seat.md)

    **[Bearing seat](bearing-seat.md)**

    ---

    A neodymium disc magnet holds a steel ball in a spherical seat. The pull with and without a steel shell around three magnet sizes in 2D, then the 4 mm magnet in 3D, in a workbench that lists each run as it finishes.

    Magnetostatics in 2D and 3D · a run browser · a dock of your own

-   [![The coil-current workbench: the solenoid's section mirrored across its axis, with field lines through the plunger, the frame and the air gap](../assets/examples/coil-current.webp)](coil-current.md)

    **[Coil current](coil-current.md)**

    ---

    A copper coil inside a steel pot frame pulls a steel plunger toward the frame's bottom plate. The coil is driven by a current density, solved at two air gaps and shown mirrored across the axis.

    Magnetostatics · a current drive · force on a body

-   [![The thermal workbench: a copper puck on an FR4 board in 3D, colored by temperature from the ambient blue of the board's corners to the yellow of the puck](../assets/examples/thermal.webp)](thermal.md)

    **[Thermal](thermal.md)**

    ---

    A copper puck dissipates 1 W on a square FR4 board and still air cools every exposed face. The steady temperature in 3D, with the peak and its rise over the air.

    Thermal in 3D · a heat source · convection

</div>
