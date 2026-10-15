# Setup

A development checkout holds both packages, femforge and femforge-workbench, in one uv workspace. One `uv sync` installs them in editable mode with the development and docs tools, so every gate CI runs also runs on your machine. This page takes you from a clone to a green gate run and an open pull request.

## Requirements

| Tool | Version | Needed for |
| --- | --- | --- |
| [Git](https://git-scm.com/) | any | the clone and the hooks |
| [uv](https://docs.astral.sh/uv/) | current | the environment; it installs a Python for you |
| Python | 3.12 or newer | femforge itself; CI tests 3.12, 3.13 and 3.14 |
| [Node.js](https://nodejs.org/) | 24 or newer, with npm 11 | the workbench client build |
| Elmer or [Docker](https://docs.docker.com/get-started/get-docker/) | see [Install](../overview/install.md#choose-how-elmer-runs) | solves only; the test suite needs neither |

On Linux, install the OpenGL and X11 libraries that gmsh loads, as [Linux system libraries](../overview/install.md#linux-system-libraries) lists.

## Clone and install

```console
git clone https://github.com/femforge-io/femforge.git
cd femforge
uv sync
```

`uv sync` installs the workspace into `.venv` with the `dev` and `docs` dependency groups, which `[tool.uv] default-groups` in `pyproject.toml` selects. The `dev` group carries basedpyright, ruff, pytest and the workbench with its `ocp` extra; the `docs` group carries Zensical and the mkdocstrings plugins.

## Build the workbench client

The workbench serves a compiled TypeScript client that git ignores. Build it once per clone, before you run the full test suite or the workbench from source:

```console
cd packages/femforge-workbench/js
npm ci
npm run build
```

The build writes the bundle into `packages/femforge-workbench/src/femforge_workbench/widgets/serve`. Several Python tests read it, and the workbench raises `FileNotFoundError` with the build command when it is missing.

| Command | When to run it |
| --- | --- |
| `npm run watch` | while you edit `js/` or any workbench Python file; it rebuilds on save |
| `npm run build` | after you change the props of a component, since a watch build skips `props.json` |
| `npm run check-dist` | to confirm the bundle matches its sources; the source hash covers the workbench Python files too |

Never commit the build output.

## Enable the Git hooks

```console
git config core.hooksPath .githooks
```

| Hook | What it does |
| --- | --- |
| `pre-commit` | runs `.githooks/check_truncation.py`, then basedpyright and the two ruff gates; pytest stays in CI so commits stay fast |
| `commit-msg` | rejects a message that credits a coding assistant or carries an internal ticket id |

The truncation check fails on a staged text file that holds a NUL byte or lost more than 40% of its bytes against `HEAD`. To keep an intentional large deletion, uncomment its path in the `.truncation-allow` file the check writes, then commit again.

`git commit --no-verify` skips both hooks, so the [CI workflows](testing.md#ci-workflows) run the same checks again.

## Elmer for solves

The unit tests run without Elmer, and the integration tests skip when gmsh, build123d or Elmer is missing. To run solves, install native Elmer or Docker as [Install](../overview/install.md#choose-how-elmer-runs) describes, then check the machine:

```console
uv run femforge doctor
```

[Elmer: native and Docker](../user-guide/elmer.md) covers both backends.

## Run the gates

Run every gate from the repository root before you open a pull request:

```console
uv run basedpyright --warnings
uv run ruff check src packages scripts examples
uv run ruff format --check src packages scripts examples
uv run pytest
uv run python -m femforge.schemas --out docs/schema
uv run zensical build --strict
cd packages/femforge-workbench/js
npm run check
npm run format:check
npm run build
npm run check-dist
```

The npm gates matter when you change `js/` or a workbench Python file. `uv run ruff format src packages scripts examples` and `npm run format` fix formatting in place. [Testing and gates](testing.md) explains what each gate proves.

## Run a subset of the tests

`pytest` enforces 100% coverage on every run, so a subset fails the coverage gate. Pass `--no-cov` to skip it:

```console
uv run pytest tests/meshing --no-cov
```

## Serve the docs

`zensical serve` builds the site and serves it at `localhost:8000`, rebuilding on save. Export the schemas first, because `docs/schema` is build output that git ignores:

```console
uv run python -m femforge.schemas --out docs/schema
uv run zensical serve
```

Pass `--open` to open the preview in a browser and `--dev-addr` to change the address. `zensical serve` does not support strict mode yet, so run `zensical build --strict` before you push.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Commit as often as you like. The squash merge replaces your commits with one commit on `main`.
3. For a user-visible change, add a line under `## [Unreleased]` in `CHANGELOG.md`.
4. Run the [gates](#run-the-gates).
5. Open a pull request against `main` and fill in the template.

The squash merge uses the pull request title as the commit subject and the description as the commit body, so write both as a commit message. Keep the title short and imperative, such as `Add a radiation boundary condition`. Leave out trailers that credit a coding assistant and internal ticket ids; the commit-msg hook and the Fingerprint workflow reject both. Refer to GitHub issues by number, such as `#42`.

## License

femforge is licensed under the Apache License 2.0. Section 5 of the license applies the same terms to every contribution you submit, so you sign no separate contributor agreement.
