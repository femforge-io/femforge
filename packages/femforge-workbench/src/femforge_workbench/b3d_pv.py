# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false
# pyright: reportMissingParameterType=false
# pyright: reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false

"""Instanced VTK actors for a build123d Compound, with silhouettes and feature edges drawn over them."""

import build123d as b3d
import build123d.vtk_tools as b3d_vtk
import numpy as np
import pyvista as pv
import vtk


class Build123Instancer:
    """Converts a build123d Compound hierarchy into instanced VTK actors: each unique shape meshed once, every occurrence an actor with its own world matrix and color."""

    def __init__(
        self,
        tolerance: float = 0.1,
        angular_tolerance: float = 0.5,
        default_color: tuple[float, float, float] = (0.9137, 0.6941, 0.1529),
    ):
        """Sets the meshing tolerances and the color of a shape that carries none.

        Args:
            tolerance: The linear deflection the shapes are meshed to.
            angular_tolerance: The angular deflection the shapes are meshed to, in radians.
            default_color: The RGB color of a shape that carries none.
        """
        self.tolerance = tolerance
        self.angular_tolerance = angular_tolerance
        self.default_color = tuple(default_color)

        # TShape -> shared vtkPolyDataMapper (None if the shape produced no geometry)
        self._mapper_cache: dict = {}
        # canonical location-free PolyData per unique mapper, kept alive for export/picking
        self._geom: dict = {}
        # canonical-mesh id -> shared feature-edge mapper (see add_feature_edges)
        self._edge_mapper_cache: dict = {}
        # one actor per leaf occurrence
        self.actors: list[vtk.vtkActor] = []

    # ------------------------------------------------------------------ public

    def parse(self, obj: "b3d.Compound") -> "Build123Instancer":
        """Replaces the actors with one per leaf of `obj`'s hierarchy."""
        self.actors.clear()
        self._walk(obj, np.eye(4))
        return self

    def add_to_plotter(self, plotter: pv.Plotter) -> pv.Plotter:
        """Adds every actor to `plotter`."""
        for actor in self.actors:
            plotter.add_actor(actor)
        return plotter

    def show(self, **kwargs) -> pv.Plotter:
        """Shows the actors in a new plotter window, passing `kwargs` to its show."""
        pl = pv.Plotter()
        self.add_to_plotter(pl)
        pl.show(**kwargs)
        return pl

    # -------------------------------------------------------------- silhouettes

    def add_silhouettes(
        self,
        plotter: pv.Plotter,
        color: str | tuple[float, float, float] = "black",
        line_width: float = 1.5,
        opacity: float = 1.0,
        feature_angle: "float | None" = 70.0,  # None -> pure view rim, no crease edges
        border_edges: bool = True,
    ) -> list[vtk.vtkActor]:
        """Adds a view-dependent silhouette per instance, recomputed on every camera move, so `add_feature_edges` suits large assemblies better.

        Args:
            plotter: The plotter to add the silhouettes to.
            color: The line color.
            line_width: The line width, in pixels.
            opacity: The line opacity.
            feature_angle: The angle above which a crease draws too, in degrees; None draws the rim alone.
            border_edges: Draws the mesh's border edges too.
        """
        cam = plotter.renderer.GetActiveCamera()
        col = pv.Color(color).float_rgb
        sil_actors: list[vtk.vtkActor] = []

        for actor in self.actors:
            mesh = actor.GetMapper().GetInput()  # shared canonical PolyData

            sil = vtk.vtkPolyDataSilhouette()
            sil.SetInputData(mesh)
            sil.SetCamera(cam)
            sil.SetProp3D(actor)
            sil.SetBorderEdges(border_edges)
            if feature_angle is None:
                sil.SetEnableFeatureAngle(0)
            else:
                sil.SetEnableFeatureAngle(1)
                sil.SetFeatureAngle(feature_angle)

            smap = vtk.vtkPolyDataMapper()
            smap.SetInputConnection(sil.GetOutputPort())
            smap.ScalarVisibilityOff()

            sa = vtk.vtkActor()
            sa.SetMapper(smap)
            sa.SetUserMatrix(actor.GetUserMatrix())  # silhouette output is in local frame
            prop = sa.GetProperty()
            prop.SetColor(*col)
            prop.SetLineWidth(line_width)
            prop.SetOpacity(opacity)
            prop.LightingOff()
            sa.SetPickable(False)

            plotter.add_actor(sa)
            sil_actors.append(sa)

        return sil_actors

    def add_feature_edges(
        self,
        plotter: pv.Plotter,
        color: str | tuple[float, float, float] = "black",
        line_width: float = 1.5,
        opacity: float = 1.0,
        feature_angle: float = 30.0,
        boundary_edges: bool = True,
    ) -> list[vtk.vtkActor]:
        """Adds each instance's crease and boundary edges, computed once per unique shape; smooth bodies show little.

        Args:
            plotter: The plotter to add the edges to.
            color: The line color.
            line_width: The line width, in pixels.
            opacity: The line opacity.
            feature_angle: The angle above which a crease draws, in degrees.
            boundary_edges: Draws the mesh's boundary edges too.
        """
        col = pv.Color(color).float_rgb
        edge_actors: list[vtk.vtkActor] = []

        for actor in self.actors:
            mesh = actor.GetMapper().GetInput()
            key = id(mesh)  # one shared canonical mesh per unique TShape

            edge_mapper = self._edge_mapper_cache.get(key)
            if edge_mapper is None:
                fe = vtk.vtkFeatureEdges()
                fe.SetInputData(mesh)
                fe.SetBoundaryEdges(boundary_edges)
                fe.FeatureEdgesOn()
                fe.SetFeatureAngle(feature_angle)
                fe.NonManifoldEdgesOff()
                fe.ManifoldEdgesOff()
                fe.ColoringOff()
                fe.Update()
                edge_mapper = vtk.vtkPolyDataMapper()
                edge_mapper.SetInputData(fe.GetOutput())  # bake once, not a live pipeline
                edge_mapper.ScalarVisibilityOff()
                self._edge_mapper_cache[key] = edge_mapper

            ea = vtk.vtkActor()
            ea.SetMapper(edge_mapper)
            ea.SetUserMatrix(actor.GetUserMatrix())
            prop = ea.GetProperty()
            prop.SetColor(*col)
            prop.SetLineWidth(line_width)
            prop.SetOpacity(opacity)
            prop.LightingOff()
            ea.SetPickable(False)

            plotter.add_actor(ea)
            edge_actors.append(ea)

        return edge_actors

    # --------------------------------------------------------------- traversal

    def _walk(self, obj: "b3d.Compound", world_matrix: np.ndarray) -> None:
        """Instances every leaf under `obj`, carrying the accumulated world matrix down."""
        children = getattr(obj, "children", None)

        if children:
            for child in children:  # pyright: ignore[reportAny]
                child_world = world_matrix @ self._local_matrix(child)  # pyright: ignore[ reportAny]
                self._walk(child, child_world)  # pyright: ignore[reportAny]
            return

        # Leaf: instance it. `world_matrix` already includes this leaf's own
        # location (composed by the parent above), so it is the full world
        # placement of the canonical (location-free) geometry.
        self._add_instance(obj, world_matrix)

    def _add_instance(self, obj: "b3d.Compound", world_matrix: np.ndarray) -> None:
        mapper = self._get_mapper(obj)
        if mapper is None:
            return

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetUserMatrix(self._np_to_vtk_matrix(world_matrix))

        color = getattr(obj, "color", None)
        if color is None:
            color = self.default_color
        color = list(color)

        actor.GetProperty().SetColor(*color[:3])
        actor.GetProperty().SetOpacity(color[3] if len(color) > 3 else 1.0)

        # carry the build123d label for picking / lookup
        actor.SetObjectName(str(getattr(obj, "label", "") or "Unnamed"))

        self.actors.append(actor)

    # --------------------------------------------------------------- geometry

    def _get_mapper(self, obj: "b3d.Compound") -> "vtk.vtkPolyDataMapper | None":
        """Return the shared mapper for this object's TShape, meshing once."""
        if obj._wrapped is None:
            return None

        tshape = obj.wrapped.TShape()

        if tshape in self._mapper_cache:
            return self._mapper_cache[tshape]

        vtk_poly = b3d_vtk.to_vtk_poly_data(
            obj,
            tolerance=self.tolerance,
            angular_tolerance=self.angular_tolerance,
            normals=True,  # normals are computed by VTK at render time
        )

        if vtk_poly is None or vtk_poly.GetNumberOfPoints() == 0:
            self._mapper_cache[tshape] = None
            return None

        mesh = pv.wrap(vtk_poly)

        # to_vtk_poly_data returns geometry placed by this object's *local*
        # location. Strip it so the canonical mesh sits at the TShape origin;
        # each instance's world matrix then places it. Done once per unique shape.
        local_inv = np.linalg.inv(self._local_matrix(obj))
        mesh = mesh.transform(local_inv, inplace=False)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(mesh)
        # color comes from each actor's property, not from any mesh scalars
        mapper.ScalarVisibilityOff()

        self._geom[tshape] = mesh  # keep a Python ref alive
        self._mapper_cache[tshape] = mapper
        return mapper

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _local_matrix(obj: "b3d.Compound") -> np.ndarray:
        """4x4 placement of `obj` within its parent frame (obj.location)."""
        trsf = obj.location.wrapped.Transformation()
        mat = np.eye(4)
        for row in range(3):
            for col in range(4):
                mat[row, col] = trsf.Value(row + 1, col + 1)
        return mat

    @staticmethod
    def _np_to_vtk_matrix(m: np.ndarray) -> vtk.vtkMatrix4x4:
        vm = vtk.vtkMatrix4x4()
        vm.DeepCopy(np.ascontiguousarray(m, dtype=float).ravel())  # pyright: ignore[reportCallIssue]
        return vm
