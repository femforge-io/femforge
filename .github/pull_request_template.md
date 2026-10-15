## Summary

<!-- The pull request title and this description become the squash commit on main. Write them as a commit message: say what changes and why. -->

## Changelog

<!-- Paste the line you added under Unreleased in CHANGELOG.md, or write "none" for an internal change. -->

## Gates

- [ ] `uv run basedpyright --warnings` reports 0 errors and 0 warnings.
- [ ] `uv run ruff check src packages scripts examples` and `uv run ruff format --check src packages scripts examples` pass.
- [ ] `uv run pytest` passes at 100% coverage.
- [ ] `npm run check` and `npm run format:check` pass in `packages/femforge-workbench/js`, with the client rebuilt (when `js/` or a workbench Python file changed).
