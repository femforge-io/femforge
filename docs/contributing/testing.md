# Testing and gates

A gate is a check that every change passes before it merges: the type check, the linter, the test suite at full coverage, the docs build and the client checks. Each gate proves one property of the codebase, and CI runs them all on every pull request. [Setup](index.md#run-the-gates) lists the commands; this page says what each gate proves and where CI runs it.

## Type check

```console
uv run basedpyright --warnings
```

basedpyright checks every path in the `include` list of `[tool.basedpyright]`: `src`, `tests`, the workbench source, its `hatch_build.py` and `scripts`. The gate requires 0 errors and 0 warnings. The `reportUnknown*` family, `reportAny` and `reportExplicitAny` report as warnings, and `--warnings` turns every warning into a failure. A suppression names one rule on one line, such as `# pyright: ignore[reportAny]`, and `reportUnnecessaryTypeIgnoreComment` flags one that no longer suppresses anything. [Tooling settings](tooling.md) gives the reason for each setting.

## Lint and format

```console
uv run ruff check src packages scripts examples
uv run ruff format --check src packages scripts examples
```

ruff runs the `E`, `F` and `I` rule sets at a line length of 200 and sorts imports with two blank lines after them. It skips `tests`.

## Tests and coverage

```console
uv run pytest
```

Every pytest run measures line coverage of `src`, `packages/femforge-workbench/src` and `tools/docs/src`, and fails below 100%. `exclude_also` in `pyproject.toml` leaves out `if TYPE_CHECKING:` blocks, `raise NotImplementedError`, `def __repr__`, `def print_tree`, `if __name__ == "__main__":` and abstract methods. A line that runs only with gmsh or Elmer carries `# pragma: no cover` with a reason, and an integration test covers it on a machine that has both.

The integration tests skip themselves when gmsh, build123d or a native Elmer is missing, so the suite passes on a bare machine.

In a test that compares meshes, set `deterministic=True` on [MeshConfig][femforge.meshing.MeshConfig]. It pins gmsh to one thread, so the same STEP and sizes always yield the identical mesh.

### Run one file or one test

```console
uv run pytest tests/test_release.py --no-cov
uv run pytest tests/meshing/test_firewall.py::test_block_gmsh_refuses_once_gmsh_is_loaded --no-cov
```

Unset `FORCE_COLOR` before you run pytest. With it set, Rich writes ANSI escapes into the captured output, and the log and CLI tests fail.

## The guard tests

These tests run inside `pytest` and guard properties that no type checker sees.

| Test | What it proves |
| --- | --- |
| `tests/test_public_api.py` | every lazy package has an `__init__.pyi` stub, each stub name resolves to its source, `dir()` of each lazy package equals its stub, and every name in a public `__all__` resolves |
| `tests/test_public_api.py` | every public name has a docstring of its own, every field of a curated record is documented, and every public callable with three or more parameters has an `Args:` block naming exactly its parameters |
| `tests/test_public_api.py` | `ff.workbench` without the workbench package names the install command `pip install "femforge[workbench]"` |
| `tests/test_field_docstrings.py` | each physical field in the listed modules has a `` `glyph` `[unit]`: meaning `` first line, pint parses the unit, and a property function's unit matches its declared dimensionality |
| `tests/test_import_time.py` | `import femforge` stays under 60 ms and loads no subpackage and none of pydantic, pint, numpy, loguru and rich; the first `ff.SimulationConfig` stays under 500 ms and loads no CAD, viz or run stack |
| `tests/meshing/test_firewall.py` | after [block_gmsh][femforge.meshing.block_gmsh], no import in the process loads gmsh, and the call refuses once gmsh has loaded |
| `tests/run/test_gmsh_firewall.py` | a driver that meshes through a [WorkerMesher][femforge.meshing.WorkerMesher] never loads gmsh, whether it meshes, reuses a cached mesh or rebinds one |
| `tests/test_schemas.py` | the schema export covers every registered model and writes valid JSON |
| `tests/test_docs_reference.py` | every public package, root module and workbench module has a `:::` directive on a reference page, and every directive resolves |
| `tests/test_docs_snippets.py` | every fence whose info string is exactly `python` type-checks as a standalone module |
| `tests/test_release.py` | the two packages, `__init__.py`, the citation and the changelog carry one version, and LICENSE and NOTICE match across the packages; [Release process](release.md#versions) lists each check |

The import budgets measure the fastest of five fresh interpreters. On a slow machine, set `FEMFORGE_IMPORT_BUDGET_SCALE` to multiply every budget; CI sets it to `2`.

A docs page that needs a fragment that cannot stand alone marks its fence `{ .python .no-check }`. Use that sparingly.

## Docker end to end

```console
FEMFORGE_DOCKER_TESTS=1 uv run pytest tests/elmer/test_docker_integration.py --no-cov
```

`tests/elmer/test_docker_integration.py` converts, partitions and solves a partitioned case in containers, with no native Elmer. It skips unless `FEMFORGE_DOCKER_TESTS` is `1`, since it needs Docker and the Elmer image.

## Docs build

```console
uv run python -m femforge.schemas --out docs/schema
uv run zensical build --strict
```

The strict build fails on any warning, such as a broken link, an autoref that resolves to nothing, or a snippet include of a missing file. Export the schemas first: `docs/schema` is build output, and the site serves it beside the [JSON schemas](../reference/schemas.md) page.

## Client checks

Run these in `packages/femforge-workbench/js` when you change `js/` or a workbench Python file:

| Command | What it proves |
| --- | --- |
| `npm run check` | `tsc --noEmit` passes and the Vitest tests pass |
| `npm run format:check` | Prettier finds nothing to reformat |
| `npm run build` | the client builds |
| `npm run check-dist` | the built bundle matches its sources, including the workbench Python files the source hash covers |

## CI workflows

| Workflow | Job | What it runs | When |
| --- | --- | --- | --- |
| Quality | `client` | `npm ci`, the four client checks, then uploads the bundle | every push to `main` and every pull request |
| Quality | `checks` | basedpyright, both ruff gates, and pytest under `xvfb-run`, with the bundle from `client` | after `client`; a pull request runs Python 3.12 only, a push to `main` and the Monday run cover 3.12, 3.13 and 3.14 |
| Quality | `docker-e2e` | fails if the runner has native Elmer, then the Docker end-to-end test | every push to `main` and the Monday run |
| Dependabot auto-merge | `automerge` | queues a squash merge of a Dependabot pull request once the required checks pass, unless the update is a major version | Dependabot pull requests only |
| Quality | `package` | `uv build --all-packages --no-sources`, `twine check --strict`, the LICENSE and NOTICE copies, the bundle files in the workbench wheel, and no build output or extra directories in the sdists | after `client` |
| Fingerprint | `fingerprint` | rejects agent configuration files, assistant attributions and internal ticket ids in the tracked files, the pushed commits, and the pull request title and description | every push to `main`; a pull request when it opens, changes, or reopens |
| Docs | `docs` | the schema export and `zensical build --strict`, then uploads the site | every push to `main`, every pull request, and on demand |
| Docs | `mkdocs-material fallback` | builds the site with mkdocs-material instead of Zensical | on demand only |
| Docs | `deploy` | publishes the site, as [Release process](release.md#docs-deploy) describes | pushes to `main` in `femforge-io/femforge` |

Fingerprint lives in its own workflow, so editing a pull request title or description reruns only that check.

## Linux

The `checks` and `docker-e2e` jobs and the Elmer image job install the libraries gmsh loads on Ubuntu 24.04:

```console
sudo apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1
```

`checks` adds `xvfb` and `xauth` and runs pytest under `xvfb-run -a`, which gives the rendering tests an X display. To match it locally on a headless Linux machine, install the same packages and run:

```console
xvfb-run -a uv run pytest
```
