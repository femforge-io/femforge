# Tooling settings

The `pyproject.toml` files carry no comments, so this page records the reason behind each setting that needs one.

- The basedpyright editor extension and the `basedpyright` CLI read the same `[tool.basedpyright]` table, so their results never diverge.
- `extraPaths` makes basedpyright resolve `femforge` from the source tree instead of the editable install, so its wildcard re-exports count as first-party.
- `stubPath` points basedpyright at `typings/`, which holds hand-written stubs for only the gmsh, lazy_loader, magpylib, trame, and winpty members that the code calls. When you call a new member of one of these libraries, add its signature to `typings/`, or the type check fails.
- `reportUnnecessaryTypeIgnoreComment` flags any `# pyright: ignore` comment that no longer suppresses a diagnostic, so every remaining ignore suppresses a real one.
- The `reportUnknown*` family and `reportAny` stay at `warning`, because the scientific dependencies ship no stubs and the editor would otherwise mark their untyped values as errors. CI runs `basedpyright --warnings`, which turns every warning into a failure.
- `reportNoOverloadImplementation = "none"` allows overloads without an implementation, because the scientific dependencies use multiple dispatch.
- The `panels` execution environment turns off `reportMissingModuleSource`, because the terminal panel imports `winpty` from pywinpty, the optional Windows-only `pty` extra of the workbench. On a host without the extra, such as CI, basedpyright finds only the stub. An inline ignore can't replace this setting, because the ignore becomes unnecessary on a host that has the extra.
- `addopts` makes every `pytest` run enforce 100% line coverage over `src`. To run a subset without the coverage gate, pass `--no-cov`.
- The coverage `source` leaves out the workbench package, because behavior tests and a scripted browser run verify its panels.
- `exclude_also` leaves boilerplate, such as `if TYPE_CHECKING:` blocks and abstract methods, out of the coverage count. Lines that can't run without gmsh or Elmer carry `# pragma: no cover`, and the gated integration tests verify them on a machine that has both. As a result, the 100% gate measures every line of environment-independent logic.
- The workbench pins `pyvista`, `vtk`, and `trame-vtklocal` to one minor version each, because a new minor can change what its VTK.wasm mirror draws. The VTK serializer and the wasm runtime that `trame-vtklocal` fetches to match it decide what the browser renders, and `pyvista` decides what a clip passes to them.
- The workbench depends on `pyvista[colormaps]`, because most of the field viewer's colormap catalog resolves through cmocean, colorcet, and cmcrameri, which that extra installs.
- The pre-commit hook runs the type and lint gates but not pytest, so commits stay fast. The Quality workflow runs the full suite with the coverage gate.
- The Quality workflow backstops the hooks, because `git commit --no-verify` skips them.
