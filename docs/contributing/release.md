# Release process

femforge and femforge-workbench release together under one version. A release is a version bump and a changelog section merged to `main`, then a `v` tag that a workflow turns into PyPI uploads and a GitHub release. The Elmer image and the docs site publish on their own schedules from `main`. This page covers all three.

## Versions

Every version string is a static literal, and `tests/test_release.py` keeps them in lockstep:

| File | Line | Check |
| --- | --- | --- |
| `pyproject.toml` | `version = "X.Y.Z"` | `test_package_versions_match` |
| `packages/femforge-workbench/pyproject.toml` | `version = "X.Y.Z"` | `test_package_versions_match` |
| `src/femforge/__init__.py` | `__version__ = "X.Y.Z"` | `test_package_versions_match` |
| `pyproject.toml` | the `workbench` extra pins `femforge-workbench==X.Y.Z` | `test_workbench_extra_pins_the_workbench_version` |
| `packages/femforge-workbench/pyproject.toml` | the dependency `femforge[viz]==X.Y.Z` | `test_workbench_depends_on_the_same_femforge_version` |
| `CITATION.cff` | `version: X.Y.Z` | `test_citation_version_matches` |
| `CHANGELOG.md` | a `## [X.Y.Z]` section | `test_changelog_has_release_section` |
| `src/femforge/templates/new/main.py` | the script header pins `femforge[workbench]~=X.Y.0` | `test_the_project_template_pins_this_minor_release_and_python` |

`femforge new` rewrites the template pin at scaffold time to the version that runs it, as [release candidates](#install-a-candidate) describe. Set `date-released` in `CITATION.cff` to the release date as well. Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Changelog

`CHANGELOG.md` follows [Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/). Pull requests add lines under `## [Unreleased]`. At release time, move those lines into a new dated section and update the compare links at the bottom of the file:

```markdown
## [Unreleased]

## [0.2.0] - 2026-11-20

### Added

- A radiation boundary condition.

[Unreleased]: https://github.com/femforge-io/femforge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/femforge-io/femforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/femforge-io/femforge/releases/tag/v0.1.0
```

The release workflow copies the section into the GitHub release notes, so write it for users.

## Cut a release

1. Open a pull request that sets the new version in every file of the [versions table](#versions), moves the Unreleased lines into the new section, and updates the compare links and `date-released`.
2. Run the [gates](index.md#run-the-gates), then merge.
3. Tag the merge commit on `main` and push the tag:

    ```console
    git tag -a v0.2.0 -m "femforge 0.2.0"
    git push origin v0.2.0
    ```

## The release workflow

!!! note
    `.github/workflows/release.yml` runs only in the public repository `femforge-io/femforge`, because its first job checks `github.repository`.

A tag that matches `v[0-9]+.[0-9]+.[0-9]+*` starts the workflow. Its jobs run in order, and a failure stops the release:

| Job | What it does |
| --- | --- |
| `client` | builds the workbench client with Node 24 and uploads the bundle |
| `build` | checks that the tag is on `main`, that both packages carry the tag version (`uv version --short --frozen --package`), that `CHANGELOG.md` has the section and that `CITATION.cff` matches; then builds both packages with `uv build --all-packages --no-sources`, runs `twine check --strict`, and installs both wheels into a clean virtual environment to assert `femforge.__version__` |
| `pypi` | uploads the wheels and sdists through PyPI trusted publishing, in the `pypi` environment, with no stored token |
| `github-release` | creates the GitHub release for the tag with the changelog section as its notes and the wheels and sdists attached |

A version that holds a letter, such as `0.2.0rc1`, becomes a GitHub prerelease.

## Release candidates

A release candidate, such as `0.1.0rc1`, goes through the same pull request, tag and workflow as a release. PyPI holds it beside the final releases, and testers install it by name.

### Cut a candidate

1. Bump both packages. `uv version` rewrites each `version =` line and `uv.lock`:

    ```console
    uv version --package femforge 0.1.0rc1
    uv version --package femforge-workbench 0.1.0rc1
    ```

2. Set the rest of the [versions table](#versions) by hand: `__version__` in `src/femforge/__init__.py`, the extra `workbench = ["femforge-workbench==0.1.0rc1"]` in `pyproject.toml`, the dependency `femforge[viz]==0.1.0rc1` in `packages/femforge-workbench/pyproject.toml`, and `version: 0.1.0rc1` with `date-released` in `CITATION.cff`. `uv version` touches none of these. The template script keeps `~=0.1.0`.
3. Add a `## [0.1.0rc1]` section to `CHANGELOG.md` with its compare link. The workflow requires it and copies it into the release notes.
4. Run the [gates](index.md#run-the-gates), merge, then tag the merge commit on `main`:

    ```console
    git tag -a v0.1.0rc1 -m "femforge 0.1.0rc1"
    git push origin v0.1.0rc1
    ```

The release workflow publishes both packages to PyPI and marks the GitHub release as a prerelease.

### Install a candidate

A plain install never picks a candidate: `uvx femforge`, `uv add femforge` and `pip install femforge` resolve the newest final release. A candidate installs only when named or allowed:

| Tool | Command |
| --- | --- |
| A new project | `uvx femforge@0.1.0rc1 new magnet-plate`, then `uv run magnet-plate/main.py` |
| A uv project | `uv add "femforge[workbench]" --prerelease allow` |
| pip | `pip install --pre "femforge[workbench]"` |

`femforge new` pins the script to the version that ran it. A final release writes `~=`, and a candidate writes `==` for both `femforge[workbench]` and `femforge-workbench`, because uv takes a prerelease only for a direct requirement that names one. The `--prerelease allow` flag holds for one command. Setting `prerelease = "allow"` under `[tool.uv]` in the project `pyproject.toml` keeps it for later locks.

### Preview docs

The `next` branch deploys the docs to `next.docs.femforge.io`, a separate Worker named `femforge-docs-next`. Point it at the candidate commit and push it:

```console
git push --force origin v0.1.0rc1^{commit}:refs/heads/next
```

### Smoke test

`.github/workflows/smoke.yml` runs on demand against a version on PyPI. It checks out nothing, so it proves the install path from the internet alone:

```console
gh workflow run smoke.yml --repo femforge-io/femforge -f version=0.1.0rc1
```

| Job | What it does |
| --- | --- |
| `solve` | on Ubuntu, runs `uvx femforge@<version> new magnet-plate` and `uv run magnet-plate/main.py --solve-only`, which pulls the Elmer image from GHCR and solves four gaps; then checks the log and the run folders for all four |
| `install` | on macOS and Windows, which have no Linux Docker, runs `femforge doctor` and checks that Python, gmsh and the CAD kernel work, runs `femforge new`, then imports `femforge` and `femforge_workbench` from the environment `uv sync --script` builds for the scaffolded script |

The `solve` job needs the Elmer image the candidate pins to be public on GHCR.

## Elmer image releases

The image `ghcr.io/femforge-io/femforge-elmer` has its own version, `IMAGE_VERSION` in `src/femforge/elmer/runtime/docker_image.py`, next to `IMAGE_DIGEST`, the sha256 of the released Dockerfile. When you change the recipe, raise the version in the same pull request:

```console
uv run femforge image bump
```

The command rewrites both lines when the recipe differs from the released one and does nothing otherwise. `--part` picks `major`, `minor` or `patch`, the default. It refuses to run from an installed package, since it edits the source.

The Elmer image workflow (`.github/workflows/image.yml`) runs on a push to `main` that touches `docker_image.py`, `src/femforge/execution/docker/` or the workflow itself, and on demand. It runs only in `femforge-io/femforge`:

1. `femforge image tag` resolves the tag. A recipe change without a bump yields a `-dev.` tag, and the job fails.
2. When GHCR already holds the tag, the job skips the remaining steps. Version tags are immutable.
3. It builds the image with `femforge image build --no-pull` and runs the Docker end-to-end test through it.
4. It pushes the image.
5. It downloads the source of the installed `elmerfem-csc` package and attaches it to a `femforge-elmer-v<version>` GitHub release, which never becomes the latest release.

The base-image workflow (`.github/workflows/base-image.yml`) runs on the first of each month and on demand. When the digest of `ubuntu:24.04` has moved from the one `BASE_IMAGE` pins, it pins the new digest, runs `femforge image bump --part patch`, and opens a pull request as the repository's bot GitHub App (secrets `BOT_APP_ID` and `BOT_PRIVATE_KEY`), so the required checks run on it. Merging it publishes the new image.

## Docs deploy

The `deploy` job of `.github/workflows/docs.yml` publishes the site after each push to `main` in `femforge-io/femforge`. It takes the site the `docs` job built and runs `npx wrangler@4 deploy` with the Cloudflare credentials of the `docs` environment. `wrangler.jsonc` names the Worker `femforge-docs`, serves `./site` with its 404 page, and routes `docs.femforge.io` to it. A push to the `next` branch deploys the same way through the `docs-next` environment with `wrangler deploy --env next`, to the Worker `femforge-docs-next` at `next.docs.femforge.io`. The `next` environment in `wrangler.jsonc` sets only its name and route and inherits the assets and the compatibility date. The `next` deploy also writes `site/_headers` with `X-Robots-Tag: noindex, nofollow`, so search engines leave the preview out of their index, and `workers_dev` and `preview_urls` are off, so each Worker answers only on its custom domain.

## The placeholder releases

PyPI holds placeholders `femforge 0.0.1` and `femforge-workbench 0.0.1` with no code in them. Once `0.1.0` is on PyPI, both placeholders are yanked, so an unpinned install never resolves to them.
