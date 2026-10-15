"""Local stub for ``magpylib.magnet`` -- the ``Cuboid`` permanent-magnet source (see ``../__init__.pyi``).

Only the SI keyword arguments this project sets (``position`` / ``dimension`` / ``polarization``) are spelled out;
the rest of magpylib's constructor is accepted loosely as ``object`` so the seal stays narrow.
"""

class Cuboid:
    def __init__(
        self,
        position: tuple[float, float, float] = ...,
        orientation: object = ...,
        dimension: tuple[float, float, float] | None = ...,
        polarization: tuple[float, float, float] | None = ...,
        magnetization: object = ...,
        meshing: object = ...,
        style: object = ...,
    ) -> None: ...
