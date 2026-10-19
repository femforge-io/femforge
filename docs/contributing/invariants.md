# Invariants

These are the narrow rules the code upholds, one per row, each with the code that implements it and the test that guards it. A change that breaks a rule fails the test named beside it; a row marked "no test" relies on review. [Principles](principles.md) covers the broader design rules these serve.

## Geometry and mesh

| Rule | Code | Test |
| --- | --- | --- |
| A multi-body STEP is OCC-fragmented into one conformal decomposition, so touching bodies share interface faces and nodes. | `src/femforge/meshing/mesher.py:162-174` | `tests/meshing/test_mesher_2d_integration.py::test_boundaries_are_curve_groups_named_by_adjacency` |
| Fragmenting drops STEP names, so each child gets back the name of its smallest-mass input, and an air-magnet overlap belongs to the magnet. | `src/femforge/meshing/mesher.py:275-301` | `tests/meshing/test_mesher_2d_integration.py::test_step_faces_keep_their_labels_and_become_body_groups` |
| gmsh writes only entities in a physical group, so the boundary partition covers every face exactly once. | `src/femforge/meshing/mesher.py:307`, `src/femforge/meshing/boundary.py:126-157` | `tests/meshing/test_boundary_groups.py::test_partition_is_total_every_face_covered_exactly_once` |
| A boundary group is named `<A>__<B>` for an interface, with the body names sorted, `<BODY>__EXT` for an external shell and `<BODY>__AXIS` on the symmetry axis of a 2D model. | `src/femforge/meshing/boundary.py:18-25`, `src/femforge/meshing/boundary.py:140-146` | `tests/meshing/test_boundary_groups.py::test_two_box_partition_yields_interface_and_externals` |
| The mesher always emits `Mesh.RecombineAll = 0`, because WhitneyAVSolver needs tetrahedra and a persisted gmsh options file can turn recombination on. | `src/femforge/meshing/gmsh_options.py:92-94` | `tests/meshing/test_gmsh_options.py::test_recombine_all_is_pinned_off` |
| The global element size applies first, then each region takes the finest `EntityTag.mesh_size` among its tags. | `src/femforge/meshing/mesh_sizing.py:30-44`, `src/femforge/meshing/mesh_sizing.py:62-64` | `tests/meshing/test_mesh_sizing.py::test_resolve_finest_requested_size_wins` |
| `MeshConfig.deterministic` pins gmsh to one thread, whatever `num_threads` says, so the same STEP and sizes yield the identical mesh. | `src/femforge/meshing/config.py:28-35` | `tests/meshing/test_gmsh_options.py::test_deterministic_pins_meshing_to_one_thread` |
| ElmerGrid renumbers the groups, so `reconcile_ids` rewrites each group's id from `mesh.names` before the sif targets it. | `src/femforge/elmer/mesh_names.py:64-83`, `src/femforge/run/driver.py:619-625` | `tests/elmer/test_mesh_names.py::test_reconcile_overwrites_gmsh_tags_with_elmer_ids` |
| The axisymmetric frame puts the radial direction on X and the symmetry axis on Y, and a section that reaches X < 0 raises, since Elmer rejects it silently. | `src/femforge/frames/axisymmetric.py:67-74`, `src/femforge/geometry/section.py:166-170` | `tests/geometry/test_section.py::test_a_section_leaking_below_x_zero_raises` |

## Solver input

| Rule | Code | Test |
| --- | --- | --- |
| A body's boundary conditions project onto every surface group the body borders, and an `external_only` condition lands only on an external shell. | `src/femforge/elmer/sim.py:439-453` | `tests/elmer/test_sif_smoke.py::test_fixed_temperature_reaches_only_the_board_surface` |
| A surface group gets one merged Boundary block, because Elmer honors a single BC section per boundary element. | `src/femforge/elmer/sim.py:417-437` | `tests/elmer/test_sif_smoke.py::test_one_boundary_block_per_group_with_conditions_merged` |
| Magnetic physics pins the far field to zero on external shells: `AV {e}` in 3D, the nodal `Potential` in 2D. | `src/femforge/elmer/sim.py:479-487` | `tests/elmer/test_sif_smoke.py::test_axisymmetric_2d_far_field_pins_potential_not_av` |
| Each body's material must emit the union of the `required_material_keys` of the solvers acting on it; a `conductor_only` solver's keys apply only to conductors. | `src/femforge/elmer/sim.py:160-177` | `tests/elmer/test_sif_validation.py::test_missing_required_material_property_raises` |
| A magnet's `Magnetization` components go on its own Material block, which WhitneyAVSolver reads, never on a Body Force. | `src/femforge/elmer/sim.py:497-519` | `tests/elmer/test_sif_smoke.py::test_magnet_body_gets_magnetization_on_material` |
| Magnetostatics solves with BiCGStabl of degree 4 and no preconditioning, since the gauge-non-unique AV system stalls under ILU. | `src/femforge/elmer/config/electromagnetism/magnetodynamics.py:55-57` | `tests/elmer/test_solvers.py::test_magnetodynamics_block_is_byte_compatible` |
| StatCurrent runs on conductor bodies only and solves Direct with umfpack; dielectrics take a second equation without it. | `src/femforge/elmer/config/electromagnetism/stat_current.py:44-60`, `src/femforge/elmer/sim.py:258-271` | `tests/elmer/test_sif_current.py::test_stat_current_solver_defaults_to_direct_umfpack` |
| Partitioning adds halo elements only for the coil stack, because halos skew the force lumping of partition-cut bodies. | `src/femforge/elmer/config/common/physics_config.py:92-94` | `tests/elmer/test_solvers.py::test_needs_halobc_only_for_the_coil_stack` |
| The sif writer reproduces pyelmer's output text and always writes LF line endings. | `src/femforge/elmer/sif.py:1`, `src/femforge/elmer/sif.py:394-397` | `tests/elmer/test_sif_writer.py::test_case_sif_matches_reference` |

## Units and config

| Rule | Code | Test |
| --- | --- | --- |
| A material property stays a pint quantity until `to_elmer` converts it to its Elmer unit and strips it to a float. | `src/femforge/materials/properties.py:59-63`, `src/femforge/elmer/sim.py:179-184` | `tests/elmer/test_sif_validation.py::test_leaked_pint_quantity_raises` |
| A physical field's docstring opens with `` `glyph` `[unit]`: meaning ``, and pint parses the unit. | `src/femforge/meshing/config.py:22-23` | `tests/test_field_docstrings.py::test_physical_field_docstring_format` |
| `Vec3` compares and hashes its exact components; `Direction` normalizes and rounds to 12 digits, so scaled copies compare equal. | `src/femforge/common/vector.py:122-142`, `src/femforge/common/vector.py:207-228` | `tests/vector/test_vec3.py::test_direction_is_normalized_and_scaled_copies_compare_and_hash_equal` |

## Runs and artifacts

| Rule | Code | Test |
| --- | --- | --- |
| Execution settings never enter the run identity: the backend, the mesher, the rank count, retention and telemetry. | `src/femforge/run/driver.py:129` | `tests/run/test_driver.py::test_driver_mpi_processes_is_not_part_of_the_run_identity` |
| `cache_key` hashes the config without `meta`, the STEP path and `physics.overrides`, plus the STEP key, the resolved solver stack and `RUN_PRODUCER_VERSION`, so overrides equal to the preset leave it unchanged. | `src/femforge/run/kinds.py:190-207` | `tests/run/test_manifest.py::test_cache_key_ignores_overrides_identical_to_the_preset` |
| Every artifact key hashes the kind name, its producer version and the spec without its `HashExclude` fields at any depth. | `src/femforge/artifacts/keys.py:91-105` | `tests/artifacts/test_keys.py::test_keys_are_stable_and_ignore_hash_excluded_fields_at_any_depth` |
| The mesh key hashes the `MeshSpec`, so materials' properties, conditions, physics and metadata never move it. | `src/femforge/run/kinds.py:145-187` | `tests/run/test_kinds.py::test_mesh_key_ignores_everything_that_only_changes_the_solve` |
| An artifact exists exactly when its `artifact.json` sidecar does, so the store writes the sidecar last, inside the staging directory, before the rename. | `src/femforge/artifacts/sidecar.py:19-20`, `src/femforge/artifacts/local.py:242-245` | `tests/artifacts/test_local_store.py::test_put_from_staging_prunes_unlisted_files_and_commits_atomically` |
| A record's `name` sits beside its key and is always derived from the key, never read from input. | `src/femforge/artifacts/sidecar.py:80-81`, `src/femforge/artifacts/sidecar.py:107-110` | `tests/artifacts/test_sidecar.py::test_record_name_is_derived_and_survives_a_round_trip` |
| Publishing is first-wins: the losing publisher's rename fails and its staging copy is discarded. | `src/femforge/artifacts/local.py:36-38`, `src/femforge/artifacts/local.py:244-249` | `tests/artifacts/test_local_store.py::test_two_publishers_racing_on_one_key` |
| A producer holds a heartbeat claim; a claim without a heartbeat for 120 s goes stale and another claimant breaks it. | `src/femforge/artifacts/lock.py:26-29`, `src/femforge/artifacts/lock.py:84-91` | `tests/artifacts/test_lock.py::test_a_claim_without_heartbeats_goes_stale_and_is_broken` |
| The default retention removes only store-sourced inputs, and no policy removes the manifest or the result. | `src/femforge/run/retention.py:100`, `src/femforge/run/retention.py:338-339` | `tests/run/test_retention.py::test_default_policy_removes_nothing_unless_cached` |
| Telemetry is append-only: each append writes a new shard per partition, and each process journal flushes every record before parquet sees it. | `src/femforge/telemetry/hive.py:149-162`, `src/femforge/telemetry/journal.py:146-155` | `tests/telemetry/test_hive.py::test_each_append_writes_a_distinct_shard` |

## Results

| Rule | Code | Test |
| --- | --- | --- |
| An MPI solve's scalars are read from rank 0 and never summed, since every rank writes the globally reduced row. | `src/femforge/run/parse.py:94-118` | `tests/run/test_parse.py::test_parse_mpi_rank_dats_read_rank_zero` |
| An axisymmetric result scales Elmer's per-radian forces by 2π, reports only the axial component and keeps `raw` verbatim. | `src/femforge/frames/axisymmetric.py:117-119`, `src/femforge/run/parse.py:224-227` | `tests/run/test_parse.py::test_parse_axisymmetric_revolves_the_typed_vectors` |
| The field display's automatic range is the union of every body's own robust range, hidden or styled bodies included. | `src/femforge/viz/field_display.py:535-577`, `src/femforge/viz/field_display.py:935-939` | `tests/viz/test_field_display.py::test_the_color_range_covers_every_body_however_it_is_shown` |
| A field line ends at its first sharp reversal, where the cosine between consecutive steps drops below -0.5. | `src/femforge/viz/field_lines.py:222-259` | `tests/viz/test_field_lines.py::test_a_line_ends_at_its_first_sharp_reversal_and_a_straight_one_stays_whole` |

## Workbench

| Rule | Code | Test |
| --- | --- | --- |
| Field lines follow the shown vector field, else the magnetic flux density, else the first vector field. | `src/femforge/viz/field_display.py:308-325`, `src/femforge/viz/field_display.py:1185-1190` | `tests/viz/test_field_display.py::test_line_vectors_prefer_the_shown_vector_then_the_flux_density_then_the_first_vector`, `tests/viz/test_field_display.py::test_a_scene_without_a_flux_density_draws_its_lines_along_the_first_vector` |
| A page has one VTK.wasm mirror: a panel that switches to the mirror moves every older mirroring panel to streaming. | `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:523-528`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:677-682` | `tests/workbench/test_scene.py::test_the_newest_panel_takes_the_mirror_and_the_older_ones_stream` |
| Mirrored polydata carries 32-bit cell arrays, because the wasm32 runtime misreads 64-bit ids. | `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:221-230` | `tests/workbench/test_scene.py::test_the_mirror_gets_cell_arrays_its_32_bit_runtime_reads` |

## Packaging and imports

| Rule | Code | Test |
| --- | --- | --- |
| `import femforge` stays under 60 ms and loads no femforge subpackage, pydantic, pint, numpy, loguru or rich. | `src/femforge/__init__.py:3-8` | `tests/test_import_time.py::test_import_femforge_loads_no_heavy_dependency` |
| The first `SimulationConfig` loads no gmsh, build123d, polars, VTK, workbench or run stack, so `femforge.meshing` exports neither `Mesher` nor `MeshEntity`, which import gmsh. | `src/femforge/meshing/__init__.py:5-13`, `src/femforge/meshing/mesher.py:8`, `src/femforge/meshing/mesh_entity.py:3` | `tests/test_import_time.py::test_the_first_simulation_config_loads_no_cad_viz_or_run_stack` |
| A driver that meshes through `WorkerMesher` never loads gmsh in its own process, whether it meshes, produces a cached mesh or rebinds one. | `src/femforge/meshing/backend.py:209-210`, `src/femforge/meshing/firewall.py:28-34` | `tests/run/test_gmsh_firewall.py::test_a_worker_driver_never_loads_gmsh` |
| A lazy package's `__init__.pyi` is its only export list: `dir()` and `__all__` equal the stub's names. | `src/femforge/__init__.py:8` | `tests/test_public_api.py::test_dir_of_a_lazy_package_equals_its_stub` |
| A stub holds only one-dot relative imports and ASCII text, because `lazy_loader.attach_stub` rejects any other import level and reads the file in the locale encoding. | `src/femforge/__init__.pyi:1` | no test |
| A public callable with three or more parameters documents each one in a Google `Args:` block. | `src/femforge/meshing/naming.py:22-26` | `tests/test_public_api.py::test_a_public_callable_documents_its_arguments` |
| femforge and femforge-workbench carry the same version, and each pins the other at it. | `src/femforge/__init__.py:6`, `packages/femforge-workbench/pyproject.toml:3` | `tests/test_release.py::test_package_versions_match` |
