# Contributing to femforge

Follow the [Code of Conduct](CODE_OF_CONDUCT.md) when you take part in femforge.

## Ways to help

- **Bug reports.** Open an [issue](https://github.com/femforge-io/femforge/issues) with the smallest configuration that reproduces the bug. Include the femforge version, your platform, and the full error output. For a security vulnerability, follow [SECURITY.md](SECURITY.md) instead.
- **Documentation fixes.** Correct anything that is wrong, unclear, or out of date, including docstrings.
- **Examples.** Share a short, runnable script that solves a real problem with femforge.
- **Materials, conditions, and solver models.** Add material definitions, boundary and body conditions, or Elmer solver models.
- **Testing on other operating systems.** Run the test suite on your operating system and report what fails.

## Setup

### Before you begin

- Install [Git](https://git-scm.com/).
- Install [uv](https://docs.astral.sh/uv/). femforge supports Python 3.12, 3.13, and 3.14, and uv installs one of them for you.
- Install [Node.js](https://nodejs.org/) 24 or later, with npm. You need it to build the workbench client, the browser front end of the `femforge-workbench` package.
- To run solves, install [Elmer](https://github.com/ElmerCSC/elmerfem) with `ElmerSolver` and `ElmerGrid` on your `PATH`, or install [Docker](https://docs.docker.com/get-started/get-docker/). With Docker, femforge pulls its released Elmer image on first use, and builds it locally only when the pull fails or the released image comes from a different recipe. The test suite needs neither Elmer nor Docker.
- On Linux, install the OpenGL and X libraries that gmsh and OpenCascade load. On Ubuntu, these packages are `libgl1`, `libglu1-mesa`, `libxrender1`, `libxcursor1`, `libxft2`, and `libxinerama1`.

### Install

1. Clone the repository and install the workspace:

   ```sh
   git clone https://github.com/femforge-io/femforge.git
   cd femforge
   uv sync
   ```

   `uv sync` installs femforge, the workbench package, and the development tools into `.venv`. It installs gmsh from PyPI as a dependency of femforge.

2. Build the workbench client once per clone:

   ```sh
   cd packages/femforge-workbench/js
   npm ci
   npm run build
   ```

   The build writes the client bundle to `packages/femforge-workbench/src/femforge_workbench/widgets/serve`. Git ignores the build output. Do not edit the files there by hand.

3. Enable the Git hooks:

   ```sh
   git config core.hooksPath .githooks
   ```

   The `core.hooksPath` setting enables two hooks. The pre-commit hook runs basedpyright and ruff before each commit. The commit-msg hook checks each commit message.

## Gates

Every change must pass the following gates. CI runs them on every pull request.

Run the Python gates from the repository root:

```sh
uv run basedpyright --warnings
uv run ruff check src packages scripts examples
uv run ruff format --check src packages scripts examples
uv run pytest
```

- The basedpyright gate checks every path in the `include` list of `[tool.basedpyright]` in `pyproject.toml`, the workbench package included. It requires 0 errors and 0 warnings. The `--warnings` flag turns any warning into a failure.
- [docs/contributing/tooling.md](docs/contributing/tooling.md) records the reason behind each tool setting.
- The pytest gate measures line coverage of `src` and fails below 100%. To run a subset without the coverage gate, add `--no-cov`, for example `uv run pytest tests/meshing --no-cov`.
- `uv run ruff format src packages scripts examples` fixes formatting in place.

Run the client gates whenever you change a file under `packages/femforge-workbench/js` or any Python file in the workbench package:

```sh
cd packages/femforge-workbench/js
npm run check
npm run format:check
```

- `npm run check` type-checks the client with `tsc` and runs the Vitest tests.
- `npm run format` fixes client formatting in place.
- Run `npm run watch` while you edit, or `npm run build` before the Python gates, because the Python tests read the build output. A watch build does not regenerate `props.json`, so run `npm run build` after you change the props of a component.
- `npm run check-dist` confirms that the build output matches the sources. The build records a hash of the client sources and the workbench Python files, so a Python-only change also needs a rebuild.
- Never commit the build output.

## Code style

- Write typed code that keeps basedpyright at 0 errors and 0 warnings.
- Prefer a real type to an ignore comment. If an ignore is unavoidable, limit it to one rule on one line, such as `# pyright: ignore[reportAny]`. The type checker reports any ignore that no longer suppresses a diagnostic.
- Keep each docstring to one sentence, or two at most.
- Give every public function or constructor with three or more parameters a Google-style `Args:` block, with one line per parameter.
- Leave a blank line after every guard clause, early return, and raise.
- Write a comment only for a constraint that the code cannot show.
- Name physical quantities in words, without unit suffixes. Start the docstring of each quantity with its symbol and unit, in the form `` `symbol` `[unit]`: meaning ``:

  ```python
  ambient_temperature: float
  """`T_amb` `[K]`: Temperature of the surrounding air."""
  ```

## Documentation style

Write documentation, docstrings, and comments in the [Google developer documentation style](https://developers.google.com/style). Use active voice, present tense, short sentences, and second person for instructions.

Write femforge in lowercase everywhere, including at the start of a sentence, never FemForge, FEMForge or FEM Forge.

## Pull requests

1. Create a branch from `main`.
2. Commit as often as you like. A squash merge replaces your branch commits with one commit on `main`.
3. For a user-visible change, add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md).
4. Run the [gates](#gates).
5. Fork the repository on GitHub, push the branch to your fork, and open a pull request against `main`.
6. Write the pull request title and description as the commit message. The squash merge uses the title as the commit subject and the description as the commit body. Keep the title short and in the imperative mood, for example `Add a radiation boundary condition`, and the description to a few lines.

Do not put trailers or footers that credit a coding assistant, or internal ticket ids, in commit messages, pull request titles, or descriptions. The commit-msg hook and the Fingerprint CI check reject them. Refer to GitHub issues by number instead, for example `#42`.

## License

femforge uses the [Apache License 2.0](LICENSE). Section 5 of that license applies the same terms to every contribution you submit. You do not sign a separate contributor agreement.
