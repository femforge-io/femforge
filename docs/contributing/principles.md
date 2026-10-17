# Principles

These are the design rules a change to femforge respects. Each one keeps a property that users or other parts of the code rely on: one typed source of truth, a run identity that only results move, and a codebase that reads the same everywhere. Each rule below names the reason and one place in the code that follows it. [Invariants](invariants.md) lists the narrower rules that tests guard.

## Configuration

**Config is typed pydantic areas, and attribute docstrings are the single source of their documentation.** [ConfigArea][femforge.common.ConfigArea] turns on `use_attribute_docstrings` (`src/femforge/common/config.py:10`), so the docstring under each field becomes the editor hover, the field description in the [JSON schemas](../reference/schemas.md) and the text of the API reference. [SimulationConfig][femforge.config.SimulationConfig] composes one area per concern: an optional `geometry`, then `frame`, `mesh`, `domain`, `physics` and `meta` (`src/femforge/config.py:37-61`).

```python
from femforge.common import ConfigArea


class CoolingConfig(ConfigArea):
    """How the housing sheds heat."""

    ambient_temperature: float = 293.15
    """`T_amb` `[K]`: Temperature of the surrounding air."""

    film_coefficient: float = 10.0
    """`h` `[W/(m²·K)]`: Convective heat transfer coefficient at the housing surface."""


print(CoolingConfig.model_fields["ambient_temperature"].description)
```

**Backends consume a Protocol slice or plain data, never `SimulationConfig`.** A backend that reads only what it needs can be swapped, tested with a stand-in, or run in another process. The mesher reads [SupportsMeshing][femforge.meshing.SupportsMeshing], which declares only `frame`, `mesh` and `domain` (`src/femforge/meshing/config.py:38-48`); a [MeshBackend][femforge.meshing.MeshBackend] takes a `MeshJob` (`src/femforge/meshing/backend.py:99-102`); an [ExecutionBackend][femforge.elmer.ExecutionBackend] takes directories (`src/femforge/elmer/runtime/backend.py:14-35`). No module under `meshing/`, `elmer/runtime/` or `execution/` imports `femforge.config`.

## Runs

**Execution settings never enter the run identity.** The same config names the same run on every machine, so a bundle solved in Docker on four ranks is reused by a native single-rank driver. The [Driver][femforge.run.Driver] docstring states it (`src/femforge/run/driver.py:128`), and [cache_key][femforge.run.cache_key] hashes only the config, the geometry key, the resolved solver stack and `RUN_PRODUCER_VERSION`, with `meta` excluded (`src/femforge/run/kinds.py:140-152`).

**A run meshes first, then solves on that mesh.** The mesh identity holds only what decides the mesh files and their group names, so configs that differ in materials, conditions or physics share one cached mesh. [mesh_key][femforge.run.mesh_key] is invariant to those inputs (`src/femforge/run/kinds.py:135-137`), and `MeshSpec` says so in its docstring (`src/femforge/meshing/spec.py:25-26`).

## Units and values

**Physical values stay pint quantities until `to_elmer` strips them once.** Conversions happen in one place, against one registry, so a unit mismatch fails loudly instead of scaling a result. `U` is the shared registry (`src/femforge/physical/units.py:257`), and each property is converted to its Elmer unit and stripped to a float at the emit site (`src/femforge/materials/properties.py:59-63`).

**Keys live on fields as typed metadata, never in string-keyed lookup tables.** A table beside a model drifts from it; metadata on the field moves with the field. [Map][femforge.common.Map] ties a field to its sif keyword, and [mapped_items][femforge.common.mapped_items] reflects the pairs out (`src/femforge/common/pydantic.py:9`, `:34`), as in `max_output_level: Annotated[int, Map("Max Output Level")]` (`src/femforge/elmer/config/common/simulation.py:65`).

**A string that an emit site and a require site share lives in one StrEnum.** Both sides then reference the same symbol, so a typo is a type error. `ElmerMaterialKeyword` (`src/femforge/materials/properties.py:118-128`) names the keyword that `ElmerProperty` emits (`src/femforge/materials/properties.py:217`) and that each solver requires, such as `HeatSolver.required_material_keys` (`src/femforge/elmer/config/thermal/heat.py:38`).

**Use the value type that matches the intent.** [Vec3][femforge.common.Vec3] is frozen, compares its exact components and hashes them, so it works as a dict key (`src/femforge/common/vector.py:122-142`). [Direction][femforge.common.Direction] normalizes and rounds to 12 digits on construction, so equal orientations compare equal without a tolerance (`src/femforge/common/vector.py:207-228`).

```python
import femforge as ff

offset = ff.Vec3(0.0, 0.0, 0.002)
assert len({offset, ff.Vec3(0.0, 0.0, 0.002)}) == 1

axis = ff.Direction(0.0, 0.0, 5.0)
assert axis == ff.Direction(0.0, 0.0, 1.0)
```

## Docstrings and comments

**A physical field documents its glyph and unit, and its name carries no unit.** The docstring's first line reads `` `glyph` `[unit]`: meaning ``, with `[-]` for a dimensionless value. `tests/test_field_docstrings.py:42` enforces the format on the modules it lists and checks that pint parses the unit, as in `` `h` `[mm]`: Domain-wide target element size `` (`src/femforge/meshing/config.py:23`).

**A docstring is one sentence, two at most.** Design rationale belongs in these pages. A public function or constructor with three or more parameters adds a Google `Args:` block with one line per parameter, which `tests/test_public_api.py:497` checks against the signature, as in `to_elmer` (`src/femforge/materials/properties.py:176-183`).

**A comment states a constraint the code cannot show.** It never restates the code or narrates a change, as in the PATH note above `gmsh.initialize` (`src/femforge/meshing/mesher.py:149-150`).

## Code layout

| Rule | Why | Example |
| --- | --- | --- |
| A blank line after every guard, `return` or `raise` | each exit stands out | `src/femforge/run/kinds.py:39-42` |
| A trailing comma on a call with more than three arguments | ruff then keeps one argument per line | `src/femforge/run/driver.py:425-430` |
| A named intermediate instead of a nested comprehension | the name says what the step yields | `tests/test_release.py:111-112` |
| A single-use helper nested in its caller | its scope shows where it is used | `src/femforge/meshing/mesher.py:137-141` |

## Logging

**A log call has a short static message, and its data goes in `log.bind()`.** A static message groups and searches well, and the telemetry log store keeps the bound fields as queryable data (`src/femforge/telemetry/schemas.py:179`). Bind keys are snake_case field names without unit suffixes. `src/femforge/common/log.py` is the only logging configuration site.

```python
import femforge as ff

ff.log.bind(run_id="ecru-elk-9c9984fd", files=12).info("Bundle published.")
```

`src/femforge/artifacts/cache.py:117` follows the rule.

## Compatibility and history

**Before 1.0, a merited breaking change ships without a compatibility shim.** femforge is alpha (`pyproject.toml:12`), and security fixes land only on the latest 0.x minor (`SECURITY.md:7`). When the pipeline changes what a config yields, bump the producer version, which invalidates reuse instead of migrating old bundles (`src/femforge/run/kinds.py:23-30`).

**History carries no AI attribution and no internal ticket ids.** The commit-msg hook (`.githooks/commit-msg:6-7`) and the Fingerprint workflow (`.github/workflows/fingerprint.yml:20-38`) reject both in messages, pull request titles and descriptions, and tracked files.
