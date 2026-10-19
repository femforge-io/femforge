# Workbench client

The workbench is a trame 4 server with a React client. Python builds every panel as a server template, and one Vite-built library of React components, named `Ff`, renders those templates in the browser. This page explains how a panel reaches the browser, how the client is built and checked, where the colours come from, how a save reloads the page, how the 3D view works and which upstream versions the code depends on. [Workbench](../../user-guide/workbench.md) covers using the workbench, and the [workbench API reference](../../reference/workbench/serve.md) lists the signatures.

## From a Python panel to the browser

| Module | Role |
| --- | --- |
| `packages/femforge-workbench/src/femforge_workbench/serve.py` | [show][femforge_workbench.serve.show] serves a Split, a factory, a catalogue card or the launcher; `build` turns a card into a shell (`packages/femforge-workbench/src/femforge_workbench/serve.py:86-117`) |
| `packages/femforge-workbench/src/femforge_workbench/registry.py` | the catalogue: built-in cards plus the `femforge.workbenches` entry points, each target imported only when its card opens (`packages/femforge-workbench/src/femforge_workbench/registry.py:308-336`) |
| `packages/femforge-workbench/src/femforge_workbench/app.py` | [Workbench][femforge_workbench.app.Workbench], the shell: the header, the dock, the panels and the persisted state |
| `packages/femforge-workbench/src/femforge_workbench/dock.py` | the dock tree of [Split][femforge_workbench.dock.Split], `Tabs` and [Panel][femforge_workbench.dock.Panel], its dockview layout JSON and the restore rule |
| `packages/femforge-workbench/src/femforge_workbench/panels/` | [PanelComponent][femforge_workbench.panels.PanelComponent] and the built-in panels |
| `packages/femforge-workbench/src/femforge_workbench/widgets/` | typed Python wrappers, one per React component of the client library |
| `packages/femforge-workbench/src/femforge_workbench/widgets/module.py` | the trame module that serves the built client under `/__ff/` and installs the `Ff` global |

The shell runs trame with `client_type="react"` (`packages/femforge-workbench/src/femforge_workbench/app.py:339`). A panel reaches the browser in five steps:

1. The constructor enables every widget library a panel may use: the `Ff` widgets, trame-rca and trame-vtklocal (`packages/femforge-workbench/src/femforge_workbench/app.py:87-88`, `packages/femforge-workbench/src/femforge_workbench/app.py:401-402`). A client loads only the modules enabled before it connected, and a card can open such panels later.
2. `add` builds each [Panel][femforge_workbench.dock.Panel] leaf through its builder and registers it before the next one, so a later panel sees the panels already standing (`packages/femforge-workbench/src/femforge_workbench/app.py:912-927`).
3. Each [PanelComponent][femforge_workbench.panels.PanelComponent] builds its widgets inside its own `DivLayout`, whose template name is `panel-<id>` (`packages/femforge-workbench/src/femforge_workbench/panels/__init__.py:209-214`, `packages/femforge-workbench/src/femforge_workbench/dock.py:89-91`).
4. The header holds one `ff-dock` element whose render prop is a `react.Slot` with a `client.ServerTemplate` named by the panel's `templateName` param (`packages/femforge-workbench/src/femforge_workbench/app.py:454-467`). The `DockPanel` component renders that slot for every dockview panel (`packages/femforge-workbench/js/src/components/Dock.tsx:99-118`).
5. When the dock reports ready, the shell restores the saved layout or the authored one, then calls `add_panel` for every placement, which the dock ignores for an id it already holds (`packages/femforge-workbench/src/femforge_workbench/app.py:1180-1193`, `packages/femforge-workbench/js/src/components/Dock.tsx:216-220`).

A panel namespaces its state through `key(name)`, which prefixes the panel id (`packages/femforge-workbench/src/femforge_workbench/panels/__init__.py:220-222`). `callback` and `listen` record what the panel registers, so `dispose` can unregister its triggers and state listeners when the tab closes (`packages/femforge-workbench/src/femforge_workbench/panels/__init__.py:232-262`). A panel rebuilt under the same id receives the disposed one through `succeed`, which the scene panel uses to keep its camera and transport (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:974-995`).

```python
from femforge_workbench.dock import Panel, Split
from femforge_workbench.panels import Cls, PanelComponent
from femforge_workbench.widgets.button import Button
from trame.widgets import html, react


class CounterPanel(PanelComponent):
    """A button that counts its clicks in a state variable of this panel."""

    count: int = 0

    def build(self) -> None:
        """Builds the button and the count beside it."""
        self.state[self.key("count")] = self.count
        with html.Div(classes=str(Cls.TOOLBAR)):
            Button("Add", size="xs", on_click=self.callback(self.add))
            html.Span([react.Bind(self.key("count"))], classes=str(Cls.CAPTION))

    def add(self) -> None:
        """Counts one click."""
        self.count += 1
        self.state[self.key("count")] = self.count


def counter() -> Split:
    """One counter panel."""
    return Split([Panel("Counter", CounterPanel)])
```

### Worker threads

The trame server runs on one asyncio loop. A reloader or a monitor that works on another thread hands its result to the loop through `marshal`, which wraps `call_soon_threadsafe` (`packages/femforge-workbench/src/femforge_workbench/app.py:1155-1160`). A state change made from such a callback runs inside `with self.state:`, because only a trame handler flushes state on its own (`packages/femforge-workbench/src/femforge_workbench/app.py:990-996`).

### Background tasks

`show(..., tasks=)` hands a script's solves to a [TaskBoard][femforge_workbench.tasks.TaskBoard], which runs each task in a child process of its own, one at a time (`packages/femforge-workbench/src/femforge_workbench/tasks.py`). Threads would not do:

- OCC holds the GIL. A 3.2 s boolean on a worker thread blocked the server loop for over 2 s, and geometry building, sectioning, the frame check and the result parse all run OCC; only meshing has a process of its own (`WorkerMesher`).
- A save purges every reloadable module. A solve still running on a thread would import a new generation of a class halfway, and `isinstance` checks across the two generations fail.
- A child imports fresh code on each start, a crash in OCC or gmsh never reaches the server, and its job object takes ElmerSolver down with it. The price is about 3.4 s of start-up per task.

The child, `python -m femforge_workbench.task_worker JOB OUTCOME`, imports the factory by its `module:attr` target from the factory's own import root, the running script's directory or the one above its top package, with the server's `sys.path` behind it (`registry.import_root`), lists the plan again and runs the task with the job's label, so nothing is pickled (`packages/femforge-workbench/src/femforge_workbench/task_worker.py`). It logs JSON lines and writes a done or failed outcome file, the `WorkerMesher` pattern.

Its records reach the board on stdout. The board reads the bound `LogKey` fields, the run id, the stage and whether its record ends it, the run directory, the progress, the solve's linear solvers and those that factorize, the kind of artifact the cache served, a claim wait's holder and a stall's reason, to move the row, and relays each record to its terminal under `RELAYED_ROUTE`, a telemetry route no sink stores. The child stores its own records, so they land in the log store once. The terminal leaves out the keys a record binds for such readers, so an aligned convergence line stays on one row (`src/femforge/common/log.py`).

A stop is graceful first. The child leads its own console group (`NEW_GROUP`), and `ProcessTree.interrupt` sends it the break, which the worker turns into `KeyboardInterrupt`, so the Driver's cleanup releases its claim (`packages/femforge-workbench/src/femforge_workbench/process_tree.py`). The board sends the break again every 0.5 s: a grandchild started just after a break never saw it, and a child blocked waiting on that grandchild handles its own break only once the wait returns. The worker ignores every break after the first, so a repeat cannot cut its cleanup short. A child still alive after 10 s has its tree killed. After either kind of stop, `reap_runs` counts the child dead and breaks any claim it left; a killed child can stay in the process table while the server still holds its handle, so the plain pid check would skip it. The next run then need not wait out the 120 s stale window. The shell stops the board on exit, and `show` stops it again when the server raises.

## The widget library

Each wrapper subclasses `Element` and passes `locals()` to it. Every declared parameter becomes a React prop: a trailing underscore is stripped, `aria_label` is renamed, the rest is camelCased, and a parameter named `on_*` becomes an event (`packages/femforge-workbench/src/femforge_workbench/widgets/element.py:73-81`, `packages/femforge-workbench/src/femforge_workbench/widgets/element.py:102-121`). `model` stays on the Python side and binds a state variable that the control writes in the browser (`packages/femforge-workbench/src/femforge_workbench/widgets/element.py:18-19`). A wrapper that needs a handle takes a `ref` from `next_ref` and calls component methods through it, as [Dock][femforge_workbench.widgets.dock.Dock] does for `addPanel` and `restoreLayout` (`packages/femforge-workbench/src/femforge_workbench/widgets/dock.py:51-72`).

The TypeScript props are the contract. `tests/workbench/test_widgets.py` checks every wrapper parameter against the tag's props in the built `props.json`, and every `Literal` alias against the TypeScript union it mirrors (`tests/workbench/test_widgets.py:31`).

On the client, `install` registers every capitalised export of `js/src/components/*.tsx` and then of `js/src/components/ui/*.tsx` as `ff-<kebab-case>`; the first tag wins, so `ff-table` is the TanStack table and the shadcn namesake is skipped (`packages/femforge-workbench/js/src/index.tsx:36-45`).

## The client build

`packages/femforge-workbench/js/` is a Vite library build. It writes one UMD bundle that exposes the `Ff` global, keeps React and ReactDOM external, and emits into the widget package's `serve/` directory with the base URL `/__ff/` (`packages/femforge-workbench/js/vite.config.ts:14`, `packages/femforge-workbench/js/vite.config.ts:34-54`). The trame module serves that directory under the same route and lists the bundle and the stylesheet with the source hash as a cache buster (`packages/femforge-workbench/src/femforge_workbench/widgets/module.py:12-15`, `packages/femforge-workbench/src/femforge_workbench/widgets/module.py:44-64`). Git ignores the built directory (`packages/femforge-workbench/.gitignore:3`), and the Quality workflow builds it on every run (`.github/workflows/quality.yml:32-36`).

| Built file | Written by | Read by |
| --- | --- | --- |
| `ff.umd.js`, `ff.css` | Vite; the bundle opens with a `/* ff <hash> */` banner | the page, through the trame module |
| `version.json` | the dist plugin: the source hash, the bundle and the stylesheet names | `packages/femforge-workbench/src/femforge_workbench/widgets/module.py:29-36`, the dev reload probe and both freshness checks |
| `theme.json` | the dist plugin, from `styles.css` | [Palette][femforge_workbench.theme.Palette] |
| `icons.json` | the dist plugin, from `js/src/icons.ts` | the widget tests |
| `props.json` | `packages/femforge-workbench/js/scripts/emit-props.mts`, after the Vite build | the widget tests |
| `THIRD_PARTY_LICENSES.txt` | the dist plugin | the wheel |
| `popout.html`, `fonts/` | copied from `js/public/` | dockview popout windows, the stylesheet |

The dist plugin writes `version.json`, `theme.json`, `icons.json` and the license file only after a bundle was written, so a failed build leaves the previous dist and its stamp in place (`packages/femforge-workbench/js/scripts/vite-plugin-dist.mts:151-182`). The license file joins the licenses Vite collects from the bundled modules with those of tailwindcss and dockview, whose CSS `styles.css` imports outside that scan, plus each package's NOTICE file (`packages/femforge-workbench/js/scripts/vite-plugin-dist.mts:10-11`, `packages/femforge-workbench/js/scripts/vite-plugin-dist.mts:120-149`).

### The source hash

The hash is sha256, cut to 16 hex characters, over the sorted paths and LF-normalised bytes of `js/src`, `js/public`, `js/scripts`, the five config and lock files, and every `.py` file of the workbench package outside `serve/` (`packages/femforge-workbench/js/scripts/source-hash.mts:5-8`, `packages/femforge-workbench/js/scripts/source-hash.mts:44-61`). The Python files count because Tailwind scans them for utility classes (`packages/femforge-workbench/js/src/styles.css:4-5`), so a Python-only change can change the stylesheet. That is also why the shell's class strings sit literally in `Cls` enums (`packages/femforge-workbench/src/femforge_workbench/app.py:105-167`).

Three places compare the stamp with the sources:

| Check | Where | On a mismatch |
| --- | --- | --- |
| `npm run check-dist` | `packages/femforge-workbench/js/scripts/check-dist.mts:1-19` | exits 1 |
| the hatch build hook | `packages/femforge-workbench/hatch_build.py:48-62` | refuses the sdist or wheel; an editable install skips the check |
| the dev reload probe | `packages/femforge-workbench/src/femforge_workbench/dev.py:36-63` | reloads the page with fresh assets |

`hatch_build.py` repeats the hash in Python (`packages/femforge-workbench/hatch_build.py:19-40`), so the two implementations must change together. It also refuses a dist that lacks any of the eight required files (`packages/femforge-workbench/hatch_build.py:16`, `packages/femforge-workbench/hatch_build.py:54-56`).

### Watch builds

`npm run watch` runs `vite build --watch` (`packages/femforge-workbench/js/package.json:11`). A watch build keeps the output directory between rebuilds (`packages/femforge-workbench/js/vite.config.ts:43-44`) and skips `emit-props.mts`, which only `npm run build` runs (`packages/femforge-workbench/js/package.json:10`). Run `npm run build` after you change the props of a component.

## Theme and tokens

`packages/femforge-workbench/js/src/styles.css` is the single source of colour: the `:root` and `.dark` token blocks, the `--ff-*` metrics in pixels and the font stacks (`packages/femforge-workbench/js/src/styles.css:41-153`). At build time `themeFrom` converts each colour through culori to sRGB hex, reads each `--ff-*` pixel value as a number, and writes both schemes to `theme.json` (`packages/femforge-workbench/js/scripts/vite-plugin-dist.mts:64-87`).

On the Python side, `load_tokens` reads `theme.json` and [Palette][femforge_workbench.theme.Palette] picks one scheme (`packages/femforge-workbench/src/femforge_workbench/theme.py:170-177`, `packages/femforge-workbench/src/femforge_workbench/theme.py:206-243`). The palette paints what the browser stylesheet cannot reach:

| Consumer | Method |
| --- | --- |
| plotly | `plotly_template`, registered as the default template by `install` (`packages/femforge-workbench/src/femforge_workbench/theme.py:273-294`) |
| pyvista | the scene panel's background and font colours (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:479-485`) |
| xterm | `xterm_theme` (`packages/femforge-workbench/src/femforge_workbench/theme.py:296-298`) |
| Monaco | `monaco_theme` (`packages/femforge-workbench/src/femforge_workbench/theme.py:255-258`) |

The `ff-theme` element toggles `.dark` and `color-scheme` on `<html>`, where Radix portals outside the layout root see them (`packages/femforge-workbench/js/src/components/Theme.tsx:7-16`). The shell mounts it in the root and in every panel, so a standalone `?ui=` page and a popout window paint too (`packages/femforge-workbench/src/femforge_workbench/app.py:418`, `packages/femforge-workbench/src/femforge_workbench/app.py:753-755`, `packages/femforge-workbench/src/femforge_workbench/panels/__init__.py:212-213`). `popout.html` copies the opener's scheme before its first paint (`packages/femforge-workbench/js/public/popout.html:7-13`).

## The dev reload loop

| Save | What happens |
| --- | --- |
| a `.py` file under a card's roots | its [Reloader][femforge_workbench.reload.Reloader] purges the modules under the roots, re-imports the target on a worker thread and hands the result to the server loop |
| a shell core module | the `--dev` supervisor restarts the server process; the page follows it back |
| a file under `js/` with `npm run watch` running | `version.json` changes and the page re-fetches the assets and reloads |

A card's roots are the femforge source root, the workbench source root and the target's own source root, which is its `src` directory in a src layout so sibling packages reload together (`packages/femforge-workbench/src/femforge_workbench/reload.py:95-124`). `SaveWatcher` debounces a burst of saves by 0.3 s and counts only a changed mtime, since watchdog also reports the access-time updates NTFS writes on reads (`packages/femforge-workbench/src/femforge_workbench/reload.py:25-26`, `packages/femforge-workbench/src/femforge_workbench/reload.py:146-169`). A reloader runs one rebuild at a time with at most one more queued (`packages/femforge-workbench/src/femforge_workbench/reload.py:251-320`). All reloaders share one purge per save under `REBUILD_LOCK`, so every open card ends on the same class generation (`packages/femforge-workbench/src/femforge_workbench/reload.py:34-35`, `packages/femforge-workbench/src/femforge_workbench/reload.py:365-396`).

The shell rebuilds a Split in place: it disposes the old panels, builds the new ones under the same ids, and their templates replace the running ones while the dock layout stays (`packages/femforge-workbench/src/femforge_workbench/app.py:980-1069`). Changed panel ids close and reopen the card. A factory that raises leaves the old panels, prints the traceback and names the error in the status line (`packages/femforge-workbench/src/femforge_workbench/reload.py:322-336`, `packages/femforge-workbench/src/femforge_workbench/app.py:958-959`).

The shell core never reloads in process: `SHELL_MODULES` and the logging singleton survive every purge (`packages/femforge-workbench/src/femforge_workbench/reload.py:40-44`). Under `--dev`, the [Supervisor][femforge_workbench.dev.Supervisor] runs the server as a child in its own process group and restarts it after a save of one of those files (`packages/femforge-workbench/src/femforge_workbench/dev.py:76-159`). It stops the child with a break signal so its exit hooks flush the persisted state, and kills the process tree after 5 s (`packages/femforge-workbench/src/femforge_workbench/dev.py:32-33`, `packages/femforge-workbench/src/femforge_workbench/dev.py:111-120`, `packages/femforge-workbench/src/femforge_workbench/app.py:1289-1298`). The child serves a page script that probes the server every second, reloads once the server answers again, and reloads when `version.json` changes (`packages/femforge-workbench/src/femforge_workbench/dev.py:36-63`).

## The 3D path

Each scene panel owns one off-screen pyvista plotter and shows it through one of two transports, chosen per panel by its `ViewMode` toggle (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:89-93`).

| Transport | Mode | How it works |
| --- | --- | --- |
| VTK.wasm mirror | `client`, the default | trame-vtklocal serializes the render window into the wasm32 runtime in the browser, which renders and interacts locally (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:648-668`) |
| trame-rca stream | `server` | the server renders, encodes jpeg frames at a 30 fps target and sends them; a held button caps the frame at 2 Mpx until 0.4 s after the release, then one 2x still follows (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:385-461`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:620-646`) |

Before every mirror sync, `mirror_friendly` prepares the scene (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:216-292`):

- It turns depth peeling off. VTK.wasm renders through OpenGL ES 3, which has no dual depth peeling, so a peeling renderer logs an error per frame and sorts nothing (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:218-219`).
- It moves every polydata's cell arrays to 32-bit storage. VTK serializes a 64-bit `vtkIdTypeArray` under that class name and the wasm32 runtime reads it with its own 32-bit `vtkIdType`, so every line or polygon would run from its first point to point 0 (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:221-230`, `tests/workbench/test_scene.py:359`).
- It replaces every non-polydata input of a `vtkDataSetMapper` with its surface and point scalars. VTK serializes such an input through a `vtkGeometryFilter` it frees on return, so the mirror receives objects that no longer exist and stalls (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:283-286`).
- It sums the cells and memory the mirror would fetch. A scene above 64 MB streams instead, without persisting that choice (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:61`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:765-775`).

`push` runs `mirror_friendly` again before each sync, since a renderable may add actors after its draw, and then `refresh_composites` (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:777-789`). VTK.wasm 9.7 writes a block's visibility without `Modified()`, so a composite mapper whose attributes changed gets a new container over the same blocks (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:295-314`, `tests/workbench/test_scene.py:384`).

One page holds one mirror. VTK.wasm hands browser events to the last view mounted on a page, so the newest scene panel claims the mirror and every older one switches to the stream and says so in its status line (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:523-528`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:677-682`). The shell builds panels one at a time for this reason (`packages/femforge-workbench/src/femforge_workbench/app.py:912-913`).

The mirror's camera and canvas reach Python through events. `SceneSync` reports the canvas box in device pixels, the plotter window takes that size, and the first size re-fits the camera (`packages/femforge-workbench/js/src/components/SceneSync.tsx:145-148`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:842-867`). `SceneSync` also turns the corner triad with the camera inside the browser, through a synchronous wasm invoke (`packages/femforge-workbench/js/src/components/SceneSync.tsx:99-106`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:348-382`). A saved camera is copied into the live one with `DeepCopy`, because assigning a camera makes a new object active under an id the browser never saw (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:813-815`).

`packages/femforge-workbench/src/femforge_workbench/b3d_pv.py` draws a build123d compound for the scene: each unique shape is meshed once and every occurrence becomes an actor with its own world matrix and colour (`b3d_pv.py:18-19`).

## Persistence

The shell keeps UI state through the [UiStateStore][femforge_workbench.persist.UiStateStore] protocol, never browser storage, so a `?ui=` tab and a popout window share it (`packages/femforge-workbench/src/femforge_workbench/persist.py:35-54`). [LocalJsonStore][femforge_workbench.persist.LocalJsonStore] writes one JSON file per user, workbench and key under the per-user config directory `femforge-workbench`, at most once per 0.5 s, and a load sees a save not yet written (`packages/femforge-workbench/src/femforge_workbench/persist.py:16-17`, `packages/femforge-workbench/src/femforge_workbench/persist.py:67-132`). The scope is the OS user and the workbench name (`packages/femforge-workbench/src/femforge_workbench/persist.py:57-59`, `packages/femforge-workbench/src/femforge_workbench/app.py:346`). The server's exit hook flushes pending saves (`packages/femforge-workbench/src/femforge_workbench/app.py:1289-1298`).

| Document | Writer | Holds |
| --- | --- | --- |
| `layout` | the shell | the dockview layout the dock reports, restored only when it names exactly the authored panels, with popout and floating groups folded into the first group (`packages/femforge-workbench/src/femforge_workbench/app.py:1195-1202`, `packages/femforge-workbench/src/femforge_workbench/dock.py:243-295`) |
| `opened` | the shell | the cards open in the dock, in opening order, so a restart reopens them (`packages/femforge-workbench/src/femforge_workbench/app.py:1075-1105`) |
| `table-<panel id>` | the table panel | the layout the client reports: column order and visibility, group fields, sort and filters (`packages/femforge-workbench/src/femforge_workbench/panels/table.py:21-26`) |
| `view-<panel id>` | the scene panel | the transport the user chose (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:71`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:530-536`, `packages/femforge-workbench/src/femforge_workbench/panels/scene.py:696-697`) |

## Pinned upstream versions

The workbench pins the minors whose behaviour its workarounds depend on (`packages/femforge-workbench/pyproject.toml:24-41`). Re-check each workaround before you raise a pin.

| Pin | Workaround it protects |
| --- | --- |
| `vtk>=9.7.1,<9.8` | 32-bit cell storage for the wasm32 runtime (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:221-230`); the composite refresh for block visibility (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:295-314`); the surface swap for dataset mappers (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:283-286`); the wasm runtime that trame-vtklocal downloads matches this VTK version |
| `trame-vtklocal>=1.7.1,<1.8` | `tolerate_dropped_objects`: 1.7.1 reads `interactor` outside the branch that assigns it, so a status request for a released window raises; the patch answers an empty scene (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:317-345`) |
| `pyvista[colormaps]>=0.49.0,<0.50` | the plain `vtkRenderWindowInteractor` and C++ trackball style, since pyvista's Python-subclassed style is mirrored verbatim and never bound to browser events (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:486-489`); the per-panel font copy, since the theme shares its font object with pyvista's global theme (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:543-549`) |
| `build123d>=0.13,<0.14` | `b3d_pv.py` reads the private `_wrapped` attribute and meshes through `to_vtk_poly_data` (`b3d_pv.py:224-238`); the core package pins the same minor (`pyproject.toml:25`) |

trame, trame-client, trame-server and trame-rca carry floors only. Two trame-rca details are coded around: it declares `on_stats` under its Vue name only, so the scene panel adds the React name (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:632-633`), and it sizes a frame by width alone, so the image style fixes both dimensions (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:68-69`).

## Traps

- `SceneSync` waits until the mirror's wasm session runs its event loop and holds the camera and triad matrix, since a state read on an object the browser has not built logs a `vtkObjectManager` error (`packages/femforge-workbench/js/src/components/SceneSync.tsx:108-116`).
- dockview reports visibility only on a change, so `DockPanel` announces a panel visible when it mounts (`packages/femforge-workbench/js/src/components/Dock.tsx:106-107`); the shell keeps the visible ids for panels rebuilt in place (`packages/femforge-workbench/src/femforge_workbench/app.py:357-358`, `packages/femforge-workbench/src/femforge_workbench/app.py:1064-1065`).
- The xterm terminal has no mount event; the log panel listens for trame's `mounted` life-cycle event and replays its buffer (`packages/femforge-workbench/src/femforge_workbench/panels/log.py:37-39`).
- A `vtk-local` view mounted into a `display: none` host has no canvas and fails to start, so switching transport unmounts the other view (`packages/femforge-workbench/src/femforge_workbench/panels/scene.py:709-723`).
- Enabling trame-vtklocal fetches the wasm32 runtime for the installed VTK version, about 80 MB, on first use; the workbench tests point `VTK_WASM_DIR_OVERRIDE` at an empty directory to skip it (`tests/workbench/conftest.py:11-17`).
- dockview fires `onDidRemovePanel` for its own teardown too, so the dock counts its own calls and reports only a user close (`packages/femforge-workbench/js/src/components/Dock.tsx:190-191`, `packages/femforge-workbench/js/src/components/Dock.tsx:207-209`).
- The dock uses `defaultRenderer="always"`, which keeps inactive tabs mounted (`packages/femforge-workbench/js/src/components/Dock.tsx:270`).
- A JS-expression `Bind` that builds an array hands Radix a fresh array on every render, and a controlled value never settles; keep the array in state (`packages/femforge-workbench/src/femforge_workbench/panels/field_viewer.py:722`).
- trame's own `--app` flag means pywebview, so `run` strips it from `sys.argv` (`packages/femforge-workbench/src/femforge_workbench/app.py:1350-1351`).
- The supervisor polls its child every 0.25 s, since a bare wait would hold Ctrl-C on Windows (`packages/femforge-workbench/src/femforge_workbench/dev.py:22-23`).
- The reload probe sends HEAD to `index.html`, since the server root answers HEAD with 403 (`packages/femforge-workbench/src/femforge_workbench/dev.py:26-27`).
- Chrome raises a benign `ResizeObserver loop` error on busy resizes, and the trame client forwards window errors to the server log, so the client drops it (`packages/femforge-workbench/js/src/index.tsx:9-16`).
