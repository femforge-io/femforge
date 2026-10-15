"""The gmsh mesher: a STEP's bodies matched to materials and tags, grouped into physical groups, sized, and meshed into a ``.msh``."""

import os
import re
from itertools import groupby
from pathlib import Path

import gmsh
from rich.tree import Tree

from femforge.common.files import MSH_SUFFIX
from femforge.common.log import get_console, log
from femforge.common.tags import TagKey
from femforge.common.utils import COLORS, title
from femforge.meshing.backend import MeshError
from femforge.meshing.boundary import BoundaryGroup, axis_entity_tags, partition_boundary_faces, read_face_adjacency
from femforge.meshing.config import SupportsMeshing
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.groups import PhysicalGroup, format_boundary_groups, format_physical_groups, raise_on_duplicate_names
from femforge.meshing.mesh_entity import MeshEntity
from femforge.meshing.mesh_sizing import apply_mesh_sizing, gmsh_set_global_size, gmsh_set_region_size, resolve_region_sizes
from femforge.meshing.naming import compose_group_name, name_fragment


# A linear-motor domain is centimetre-scale; an extent past this many metres almost certainly means the
# geometry was never converted from millimetres to metres (the pipeline is SI -- see GmshOptions.target_units).
PLAUSIBLE_MAX_EXTENT_M = 2.0

BUILD123D_ORIGINATING_SYSTEM = "build123d"
"""The ``FILE_NAME`` originating system build123d's STEP exporter writes."""

_FILE_NAME_ENTITY = re.compile(r"FILE_NAME\s*\((.*?)\)\s*;", re.DOTALL)
# One FILE_NAME parameter: a list of strings, a string ('' escapes a quote), or $ for unset.
_HEADER_PARAMETER = re.compile(r"\((?:'(?:[^']|'')*'|[^')])*\)|'(?:[^']|'')*'|\$")


def step_originating_system(path: str | Path) -> str | None:
    """The originating system a STEP's ``FILE_NAME`` header records, or None when the header has none."""
    header: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as file:
        for line in file:
            if line.lstrip().startswith("ENDSEC"):
                break
            header.append(line)

    entity: re.Match[str] | None = _FILE_NAME_ENTITY.search("".join(header))
    if entity is None:
        return None

    # FILE_NAME(name, time_stamp, author, organization, preprocessor_version, originating_system, authorization)
    parameters: list[str] = _HEADER_PARAMETER.findall(entity.group(1))
    if len(parameters) < 6 or not parameters[5].startswith("'"):
        return None

    return parameters[5][1:-1].replace("''", "'")


def oversized_extent_warning(extents: tuple[float, float, float], limit_m: float = PLAUSIBLE_MAX_EXTENT_M) -> str | None:
    """Warning string if the largest domain extent exceeds ``limit_m`` (a likely mm->m conversion slip), else ``None``."""
    largest: float = max(extents)
    if largest <= limit_m:
        return None

    return (
        f"mesh bounding box spans {largest:.3g} m (> {limit_m:g} m) -- implausibly large for a metre-scale model; "
        "the geometry may not have been converted to metres (is GmshOptions.target_units='M'?)."
    )


class Mesher:
    """
    Walk a STEP file, match each region to a material + tags, and create one gmsh physical group per (material, tags) combo.

    Two-phase: the constructor is cheap and gmsh-free; [`generate`][femforge.meshing.mesher.Mesher.generate] does all gmsh work.
    """

    def __init__(self, config: SupportsMeshing, output: str | Path | None = None) -> None:
        """Store the config and the ``.msh`` path, ``output`` or else the STEP's own path with the ``.msh`` suffix; nothing here touches gmsh."""
        self.config: SupportsMeshing = config
        self.input_filename: str = config.mesh.step

        filename_without_extension: str = self.input_filename.rsplit(".", 1)[0]
        self.name: str = filename_without_extension.rsplit("/", 1)[-1]
        self.output_filename: str = str(output) if output is not None else f"{filename_without_extension}{MSH_SUFFIX}"

        # Populated by generate(); the physical_groups / boundary_groups properties raise until then.
        self.Entities: list[MeshEntity] = []
        self._physical_groups: list[PhysicalGroup] | None = None
        self._boundary_groups: list[BoundaryGroup] | None = None
        self._element_count: int | None = None

    @property
    def body_dim(self) -> int:
        """Topological dimension of the bodies being meshed: 3 for solids, 2 for a planar cross-section."""
        return self.config.frame.model_dim

    @property
    def boundary_dim(self) -> int:
        """Topological dimension of the interfaces between bodies -- one below [`body_dim`][femforge.meshing.mesher.Mesher.body_dim] (surfaces in 3D, curves in 2D)."""
        return self.body_dim - 1

    @property
    def physical_groups(self) -> list[PhysicalGroup]:
        """The body groups built by [`generate`][femforge.meshing.mesher.Mesher.generate]; raises if accessed before meshing."""
        if self._physical_groups is None:
            raise RuntimeError("Mesher.generate() has not been called yet; physical_groups are only available after meshing.")

        return self._physical_groups

    @property
    def boundary_groups(self) -> list[BoundaryGroup]:
        """The boundary groups built by [`generate`][femforge.meshing.mesher.Mesher.generate]; raises if accessed before meshing."""
        if self._boundary_groups is None:
            raise RuntimeError("Mesher.generate() has not been called yet; boundary_groups are only available after meshing.")

        return self._boundary_groups

    @property
    def element_count(self) -> int | None:
        """Elements of the body dimension in the generated mesh (tetrahedra in 3D, triangles in 2D); None before [`generate`][femforge.meshing.mesher.Mesher.generate]."""
        return self._element_count

    @property
    def element_count_3D(self) -> int | None:
        """[`element_count`][femforge.meshing.mesher.Mesher.element_count] of a 3D model; None in 2D."""
        return self._element_count if self.body_dim == 3 else None

    def generate(self) -> None:  # pragma: no cover - walks a STEP through a live gmsh model and meshes it
        """
        Run the gmsh pipeline: import the STEP, build body + boundary groups, apply options + mesh sizing, then mesh and write the ``.msh``.

        After this returns, [`physical_groups`][femforge.meshing.mesher.Mesher.physical_groups] and [`boundary_groups`][femforge.meshing.mesher.Mesher.boundary_groups] are available.
        """
        config: SupportsMeshing = self.config
        body_dim: int = self.body_dim

        def is_unnamed(entity: MeshEntity) -> bool:
            return entity.name_raw is None or entity.name_raw.strip() == ""

        def group_key(entity: MeshEntity) -> tuple[str, ...]:
            return (name_fragment(entity.tags.get(TagKey.NAME, "")), *(tag.name for tag in entity.entity_tags))

        options: GmshOptions = config.mesh.resolved_gmsh_options()

        # Interruptible does not work in a background thread (e.g. triggered by watchdog); set False to avoid warnings.
        if not gmsh.isInitialized():
            gmsh.initialize(interruptible=False)

        # gmsh.initialize rewrites the PATH child processes inherit (on Windows, to ~300 characters) while os.environ keeps
        # the full one; re-setting it restores it for Docker, Elmer and every later subprocess.
        os.environ["PATH"] = os.environ.get("PATH", "")

        # Applied before clear() too, which prints to C-level stdout unless General.Terminal is already off; applied
        # again after it, and before importShapes so OCCImportLabels takes effect.
        options.apply()
        gmsh.clear()
        options.apply()

        gmsh.model.occ.importShapes(self.input_filename)
        gmsh.model.occ.synchronize()

        # Conformal mesh is REQUIRED: build123d STEPs carry independent, possibly overlapping solids, which gmsh meshes
        # with duplicate coincident faces and no shared nodes — flux cannot couple across interfaces and the solve is garbage.
        before_conforming = [MeshEntity(*entity, config.domain) for entity in gmsh.model.getEntities(dim=body_dim)]
        missing_material = [entity for entity in before_conforming if entity.material is None]
        if missing_material:
            unnamed = [entity.tag for entity in missing_material if is_unnamed(entity)]
            unmatched = {entity.tag: entity.name_raw for entity in missing_material if not is_unnamed(entity)}
            log.bind(unnamed=unnamed, unmatched=unmatched).warning(f"{len(missing_material)} Entities without an associated material before conforming.")
            if unnamed and step_originating_system(self.input_filename) == BUILD123D_ORIGINATING_SYSTEM:
                log.info("It is a KNOWN OCCT bug that if you do not assign a color to a solid in build123d, the STEP export may not carry a name for that solid, check that first.")
            raise MeshError("All entities must have an associated material before conforming. Please check the error messages above.")

        self._make_conformal()

        entity_count: int = len(gmsh.model.getEntities(dim=body_dim))
        self.Entities = [MeshEntity(*entity, config.domain) for entity in gmsh.model.getEntities(dim=body_dim)]

        # Group entities by material
        self.Entities.sort(key=lambda e: e.material.name if e.material else "")
        entities_by_material = {material: list(group) for material, group in groupby(self.Entities, key=lambda e: e.material)}

        if None in entities_by_material:
            orphaned = {entity.tag: entity.name_raw for entity in entities_by_material[None]}
            log.bind(entities=orphaned).warning("Entities without an associated material.")

            raise MeshError("All entities must have an associated material. Please check the error messages above.")

        # Shared mesh<->sim contract: record every physical group so the Elmer sif generator can build a matching body
        # targeting the same gmsh id and reusing the same compound name.
        physical_groups: list[PhysicalGroup] = []

        for material, entities in entities_by_material.items():
            if material is None:
                # Missing-material entities are reported and raised above; skip the empty None bucket.
                continue

            # Further group entities by their authored name and matched EntityTag selectors within each material
            entities.sort(key=group_key)

            entities_by_tags = {key: list(group) for key, group in groupby(entities, key=group_key)}
            for tagged_entities in entities_by_tags.values():
                authored: str | None = tagged_entities[0].tags.get(TagKey.NAME)
                group_name = compose_group_name(
                    material,
                    tagged_entities[0].entity_tags,
                    warn=log.warning,
                    authored=authored,
                )

                entity_volume_tags = [entity.tag for entity in tagged_entities]
                mesh_material: int = gmsh.model.addPhysicalGroup(
                    body_dim,
                    entity_volume_tags,
                    name=group_name,
                )
                gmsh.model.setPhysicalName(body_dim, mesh_material, group_name)

                # Resolve the EntityTag objects (not just their string keys) so the Elmer generator can read per-region
                # overrides. All entities in a group share the same tag set by construction.
                tag_objs = tagged_entities[0].entity_tags if tagged_entities else []
                physical_groups.append(
                    PhysicalGroup(
                        dim=body_dim,
                        gmsh_id=mesh_material,
                        name=group_name,
                        material=material,
                        tags=tag_objs,
                        entity_tags=entity_volume_tags,
                        authored=authored,
                    )
                )

        raise_on_duplicate_names(physical_groups)
        self._physical_groups = physical_groups
        log.info(format_physical_groups(physical_groups, total_entities=entity_count))

        # Derive the boundary physical groups from body adjacency, once all body groups exist.
        # Mirrors physical_groups so the sif writer can attach boundary conditions to real boundary ids.
        self._boundary_groups = self._build_boundary_groups()

        # Set global + per-region element sizes, then mesh and write the .msh.
        apply_mesh_sizing(
            config.mesh.global_mesh_size,
            resolve_region_sizes(physical_groups),
            set_global=gmsh_set_global_size,
            set_region=lambda tags, size: gmsh_set_region_size(tags, size, body_dim),
        )
        gmsh.model.mesh.generate(body_dim)

        # Sanity-check the meshed extent: far larger than a metre-scale motor flags a likely mm->m slip.
        # Warn rather than fail -- a large air box can be legitimate.
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        warning: str | None = oversized_extent_warning((xmax - xmin, ymax - ymin, zmax - zmin))
        if warning is not None:
            log.warning(warning)

        gmsh.write(self.output_filename)

        _, element_tags, _ = gmsh.model.mesh.getElements(dim=body_dim)
        self._element_count = sum(len(tags) for tags in element_tags)

    def _make_conformal(self) -> None:  # pragma: no cover - mutates a live gmsh OCC model
        """
        OCC-``fragment`` every body against every other so touching/overlapping bodies become one conformal decomposition with shared interfaces (no-op for a single body).

        ``fragment`` discards STEP entity names, so each input's name + mass is captured first and re-applied via the
        input->children map; a child claimed by several inputs goes to the smallest-mass claimant.
        """
        body_dim: int = self.body_dim
        volumes: list[tuple[int, int]] = gmsh.model.getEntities(dim=body_dim)
        if len(volumes) < 2:
            return

        # Capture (name, mass) before fragmenting: fragment renumbers/replaces volumes and drops their STEP names.
        info: dict[int, tuple[str, float]] = {}
        for _, tag in volumes:
            name = gmsh.model.getEntityName(body_dim, tag)
            mass = gmsh.model.occ.getMass(body_dim, tag)
            info[tag] = (name, mass)

        # Fragment all volumes against each other (empty tool list = mutually fragment the object set).
        # out_map[i] is the list of child (dim, tag) entities the i-th input volume became.
        _out, out_map = gmsh.model.occ.fragment(volumes, [])
        gmsh.model.occ.synchronize()

        # Resolve each child volume to its smallest-mass claimant, so an air<->magnet overlap region is named for the magnet.
        claims: dict[int, tuple[float, str]] = {}
        for (_, in_tag), children in zip(volumes, out_map):
            name, mass = info[in_tag]
            for child_dim, child_tag in children:
                if child_dim != body_dim:
                    continue

                current: tuple[float, str] | None = claims.get(child_tag)
                if current is None or mass < current[0]:
                    claims[child_tag] = (mass, name)

        for child_tag, (_, name) in claims.items():
            if name:
                gmsh.model.setEntityName(body_dim, child_tag, name)

    def _build_boundary_groups(self) -> list[BoundaryGroup]:  # pragma: no cover - reads adjacency from a live gmsh model
        """
        Emit one boundary-dimension physical group per adjacency-derived boundary set (surfaces in 3D, curves in 2D).

        The partition is *total*: under gmsh's all-or-nothing rule, only faces in some boundary group survive into the mesh, so every boundary Elmer might target must be covered.
        """
        boundary_dim: int = self.boundary_dim

        body_groups = ((group.name, group.entity_tags) for group in self.physical_groups)
        adjacency: dict[int, set[str]] = read_face_adjacency(body_groups, body_dim=self.body_dim)
        # The symmetry axis exists only in the 2D axisymmetric frame (X = radial >= 0); 3D output must stay byte-identical.
        axis_faces: frozenset[int] = axis_entity_tags(adjacency.keys(), dim=boundary_dim) if self.body_dim == 2 else frozenset()

        boundary_groups: list[BoundaryGroup] = []
        # lines = ["", f"{len(adjacency)} boundary faces → {len(adjacency)} physical groups:"]
        for partition in partition_boundary_faces(adjacency, axis_faces=axis_faces):
            gid: int = gmsh.model.addPhysicalGroup(
                boundary_dim,
                list(partition.face_tags),
                name=partition.name,
            )
            gmsh.model.setPhysicalName(boundary_dim, gid, partition.name)
            # lines.append(f"  - {partition.name} ({len(partition.face_tags)} face(s)) -> id {gid}")
            # log.debug(f"Boundary: {partition.name} ({len(partition.face_tags)} face(s)) -> id {gid}")

            group = BoundaryGroup(
                gmsh_id=gid,
                name=partition.name,
                bodies=partition.bodies,
                face_tags=list(partition.face_tags),
                dim=boundary_dim,
                on_axis=partition.on_axis,
            )
            boundary_groups.append(group)

        log.info(format_boundary_groups(boundary_groups))
        return boundary_groups

    def print_tree(self):
        """Print the meshed entities as a tree on the shared console."""
        tree = Tree(COLORS.H1(title(self.name)))

        for entity in self.Entities:
            entity.print_tree(tree)

        get_console().print(tree)
