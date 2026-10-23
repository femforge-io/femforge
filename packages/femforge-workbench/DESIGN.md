# Design system: femforge workbench

The workbench is an engineering instrument. It is dense, calm and exact. The model in the 3D view is the subject, and the panels around it stay quiet.

`js/src/styles.css` holds every value named here. A change to a value updates this file and the stylesheet in the same commit.

## Rules

1. **The browser owns UI state.** A control changes in the same frame as the click. UI state covers hover, expand and collapse, selection, visibility and sort order.
   - The server learns of a change afterwards. While a change of a key is pending, the browser ignores server values for that key.
   - Work the change starts on the server shows as busy. See [Busy feedback](#busy-feedback).
   - When the server rejects a change, the control reverts and shows the reason below it.
   - Loading a run replaces all UI state. Popout windows read the same browser state as the main window.
2. **Show inputs as inputs.** A value the author set in the config appears in the control that edits it, marked read-only. Values never get joined into a sentence.
3. **Every element earns its place.** Remove a label, count, note or section that gives the user nothing to read or act on.
4. **Motion connects states.** An element that appears, disappears or moves animates from one state to the next. Nothing pops into place.
5. **One kind, one form.** The same kind of item uses the same component, in the same column, in every panel.
6. **One state, one control.** Each piece of UI state has exactly one control in a panel. A layer's visibility, for example, has one checkbox per kind and one parent checkbox.

## Color

The workbench has one scheme, dark. All values are OKLCH. Neutrals are cool, with hue 255 and low chroma.

| Token | Value | Use |
|---|---|---|
| `--background` | `oklch(0.17 0.004 255)` | Panel bodies and the 3D view. |
| `--card` | `oklch(0.19 0.004 255)` | Tab strips, section headers, the app bar and the task bar. The only raised plane. |
| `--popover` | `oklch(0.245 0.008 255)` | Menus, select lists and tooltips. |
| `--stripe` | `oklch(1 0 0 / 3%)` | The fill of every second row in the task drawer. |
| `--scrim` | `oklch(0 0 0 / 55%)` | The shade behind a dialog and around the tour's highlight. |
| `--wash-hover` | `oklch(0.24 0.006 255)` | A hovered row or button. |
| `--wash-selected` | `oklch(0.3 0.008 255)` | A selected row, a pressed button or the active toggle item. Also `--accent`, `--muted` and `--secondary`. |
| `--wash-selected-hover` | `oklch(0.34 0.008 255)` | A selected row or active item under the pointer. |
| `--border` | `oklch(1 0 0 / 9%)` | Hairlines. |
| `--input` | `oklch(1 0 0 / 10%)` | Field borders and the off track of a switch. |
| `--readonly` | `oklch(0.8 0 0 / 10%)` | The fill of a read-only field. Darker than `--input`, so it never reads as editable. |
| `--readonly-foreground` | `oklch(0.8 0.008 255)` | The text of a read-only field. |
| `--foreground` | `oklch(0.93 0.006 255)` | Primary text. |
| `--muted-foreground` | `oklch(0.66 0.012 255)` | Labels, units, secondary text and inactive toggle items. |
| `--primary` | `oklch(0.8756 0.2202 153.5)` | The signature green. Use it only where the list below allows. |
| `--ring` | `--primary` at 50 % | The keyboard focus ring. |
| `--destructive` | `oklch(0.59 0.233 17.4)` | Failed tasks, rejected changes and invalid fields. |
| `--status-warning` | `oklch(0.854 0.171 100.9)` | Warnings. |

A status color always comes with an icon or a word.

Green (`--primary`) appears only on:

- the keyboard focus ring;
- checked switches and checkboxes, and the filled part of a slider;
- the 2 px marker of a selected row;
- the 2 px left edge of a definition tooltip;
- the hover and selection outlines in the 3D view;
- the progress bar of a running task and the busy line.

Green never fills a button or a toggle item, and green text never marks a state.

## Interaction states

Every interactive surface has these states.

| State | Treatment |
|---|---|
| Rest | No fill. |
| Hover or keyboard focus within | `--wash-hover`, pointer cursor. |
| Selected, pressed or active | `--wash-selected`. A row adds a 2 px `--primary` marker on its left edge. A toggle item switches its text from `--muted-foreground` to `--foreground`. |
| Selected and hovered | `--wash-selected-hover`. |
| Keyboard focus | A 2 px `--ring` outline on `:focus-visible`. Only the focused control draws it. A panel, a dock group or the canvas never draws it. |
| Disabled | 50 % opacity and the default cursor. The control keeps `aria-disabled`, stays hoverable and focusable, and its tooltip says why. |

A state change never changes the font weight, so text never shifts width.

Cursors:

- `pointer` on every element with a click action: buttons, toggles, switches, checkboxes, tabs, select triggers, select items, menu items and clickable rows.
- `grab` on drag handles.
- `text` on editable fields only.
- `default` everywhere else, read-only fields included.

## Typography

- Inter for UI text. JetBrains Mono for ids, paths and code.
- `tabular-nums` applies everywhere, so digits line up in columns.
- `text-label`, 12.5 px: all panel text, including rows, labels, toolbars and section headers.
- `text-body`, 14 px: the app bar's project name and dialog titles.
- Weights: 400 for text, 500 for toggle items, 600 for section headers and row names.

## Spacing and shape

- The base unit is 4 px. The scale is 4, 8, 12, 16 and 24 px. Spacing is tight inside a group and looser between groups.
- Rows are 26 px (`--ff-row-height`). Tab strips and toolbars are 30 px (`--ff-tab-height`). The app bar is 40 px (`--ff-bar-height`).
- Panels pad their content 8 px from each side.
- The radius is 6 px (`--radius: 0.375rem`).
- Planes separate with 1 px borders. Only floating layers cast a shadow: menus, select lists, tooltips and popouts.

## Layout

- A table or list orders its columns as: icon, name, values, row actions.
  - The icon column is 16 px wide and has no header.
  - Values align right, with the unit after the value.
  - Row actions keep a fixed-width column. A control revealed on hover also shows on `:focus-within`, and it never moves the text beside it.
- A header never names an icon column.
- Controls of one kind (checkboxes, color maps, sliders) line up in one column across the rows of a section.
- Text that does not fit truncates with an ellipsis and shows the full text in a tooltip. A field grows into free width before it truncates.
- A section whose content would be empty with every filter cleared is hidden. A filter that leaves no rows keeps the section and shows one muted row that says so, such as "No boundaries have conditions."

## Panel states

Each panel defines these states.

| State | Shows |
|---|---|
| Loading | Skeleton rows with the shape of the panel's rows. |
| No run | One sentence and one action: "No run loaded. Open one from the Run menu." |
| Error | Two sentences: what failed, then what to do. The file or entity it names is a link. |
| Loaded | The panel. |

## Busy feedback

A click always gets an answer in the same frame, even when its result takes seconds, such as new streamlines.

1. In the same frame, the control shows its new state: a toggle flips, a select shows the new value, a button shows pressed.
2. When the result has not arrived after `--delay-busy`, an indeterminate 2 px line runs along the bottom edge of the window, in `--primary` at 60 %. It fades in over `--duration-micro` and overlays the page, so nothing moves.
3. Once shown, the line stays for at least `--busy-hold`, so a result that arrives just after the delay does not flash it.
4. When the result arrives, the line fades out over `--duration-micro`. When the work fails, the control reverts and shows the reason below it.

| Token | Value | Use |
|---|---|---|
| `--delay-busy` | 150 ms | The wait before a busy indicator shows. Faster work shows none. |
| `--busy-hold` | 400 ms | The shortest time a busy indicator stays. |

## Motion

| Token | Value | Use |
|---|---|---|
| `--duration-micro` | 120 ms | Hover washes, icon swaps, chevron turns and tooltips. |
| `--duration-panel` | 200 ms | Expand and collapse, the task drawer, rows entering and leaving, scrolling a row into view, and shared elements moving. |
| `--duration-camera` | 400 ms | Camera moves: Fit, the standard views and fit to an entity. |
| `--ease-instrument` | `cubic-bezier(0.2, 0, 0, 1)` | Every transition. No springs and no bounce. |

- Expand and collapse animate height and opacity. Controls that depend on a setting, such as the deformation scale under Deformed Shape, expand and collapse with it (`Reveal`).
- A state change animates every property it changes over one duration. A switch's thumb fades its color while it slides, so no part of the control jumps while another part moves.
- An element that moves between two containers keeps one shared layout id and animates between its positions. The task summary moving from the task bar into the drawer header is one example.
- A camera move interpolates its orientation by quaternion slerp, the focal point linearly and the distance geometrically. Euler angles never drive a camera move, because they lock at the poles.
  - A new move starts from the current pose and cancels the running move.
  - Any pointer or wheel input in the view cancels the running move.
- Numeric readouts, slider thumbs, anything that follows a drag, and scene data updates never animate.
- A transition lists its properties. `transition-all` is not allowed, because it also transitions `visibility` and keeps a hidden dock panel painted for the length of the transition.
- Under `prefers-reduced-motion`, every duration is 0. The CSS and the JS motion helper both apply it.

## Components

### Tooltip

- A tooltip carries information the screen does not show. It never repeats the text of the element it describes.
  - An icon button's tooltip names its action and shortcut, because the icon has no text.
  - Truncated text shows its full text.
  - A row or label gets a tooltip only for facts it does not show, such as the run a reused task came from.
- The `popover` plane, a 1 px `--foreground` ring at 10 % and the floating shadow.
- A tooltip is a label or a definition:
  - A label names what a control does in a word or two, such as `Top view` on a view button. It has no edge.
  - A definition explains a value, such as what Plane Stress means or why a component is inactive. It has a 2 px `--primary` edge on the left.
- `text-label`, at most 20 rem wide, no arrow.
- A tooltip opens below its element. It flips above only where the window has no room below.
- A tooltip opens after the pointer rests for `--delay-tooltip`. Once one is open, the next one opens at once while the pointer moves within `--skip-tooltip` of leaving the last.
- Every tooltip also opens on keyboard focus.
- The native `title` attribute is not used.

| Token | Value | Use |
|---|---|---|
| `--delay-tooltip` | 700 ms | The rest before the first tooltip opens. |
| `--skip-tooltip` | 300 ms | The window in which the next tooltip opens at once. |

`js/src/lib/motion.ts` reads both from the stylesheet, so one edit in `styles.css` changes every tooltip.

### Icon button

- 24 px square, no fill at rest, 16 px Lucide icon.
- An `aria-label`, and a tooltip with the action and its shortcut.
- A toggle button (on or off) shows its on state with the selected treatment and sets `aria-pressed`.

### Toggle group

- Items sit in a track with a 1 px `--input` border.
- An inactive item is `--muted-foreground`. The active item uses the selected treatment.
- Use a toggle group to choose one of two to four modes, such as Client and Server.

### Checkbox and switch

- A checkbox controls visibility. It is a row action, so it sits in the last column. A parent checkbox in the first row, such as All Glyphs, controls the rows below it and shows the mixed state when they differ.
- A row whose only control is its checkbox is a label around it. A click anywhere on the row toggles the checkbox, and the row takes the hover wash and the pointer cursor.
- A switch turns a setting on or off, in a settings row: the label on the left, the switch in the right-hand column.

### Read-only field

- The same height and layout as the editable field.
- A `--readonly` fill with no border, `--readonly-foreground` text and the default cursor. The text stays selectable.
- A read-only select shows its value without the chevron.

### Quantity field

- A number with the unit inside the field's end, for example `100` with `N`.

### Vector field

- One cell per component, labelled with the frame's axis names: X, Y and Z, or Radial and Axial in an axisymmetric run.
- The unit follows the last cell. In one set of fields, every vector ends at the same column: a vector without a unit keeps an empty unit slot when another vector has a unit.
- A free component shows an empty cell with the placeholder `Free`.
- An authored component the solve frame has no coordinate for, such as Z in a 2D run normal to Z, is inactive: its cell shows `—` at 50 % opacity, and its tooltip gives the reason.

### Condition editor

- One card per condition. The card sits on the `--background` plane, so a hovered or selected row washes around the card, never under its fields.
- The first row holds the condition type as a select, led by the condition's glyph icon.
- The rows below hold the condition's own fields, such as Magnitude and Direction.
- Two conditions on one entity are two cards.

### Entity row

- Bodies and boundaries list in entity rows: kind icon, name and measure (volume, area or length). A body also shows its material in a read-only field.
- Kind icons: `box` for a body and `scan` for a face; in a 2D run, `pentagon` for a body and `minus` for an edge. A kind icon never looks like a control.
- A boundary without a name shows the bodies it touches as color chips: `● Flexure ↔ ● External`. Each body name truncates by the same amount, and the tooltip shows the full pair.
- Hover or keyboard focus washes the row and outlines the entity in the 3D view.
- A click or Enter selects the row and the entity. Clicking a selected row keeps it selected. Escape clears the selection wherever the focus is, the 3D view included, unless a menu or dialog is open.
- A double-click or `F` fits the camera to the entity.

### Section

- The header sits on the `card` plane: title at weight 600, count right-aligned in `--muted-foreground`, and a chevron that turns over `--duration-micro`.
- The body expands and collapses over `--duration-panel`.
- A section with nothing to show is hidden, never shown empty. A run with nothing to expand has no Symmetry section, and a run with no displacement has no Deformation section.
- A section that comes or goes with the run expands or collapses like a `Reveal`, and the sections below glide to their new places in the same motion.
- A section shows only what its tab is about. The Setup tab shows the run's inputs: no computed results such as the geometry check, and no display choices such as Show As.

### Frame section

- The Frame section is always present for a run whose frame is known. Its first row is the frame Type, such as Solid, Cartesian 2D or Axisymmetric, with a definition tooltip saying what the frame solves.
- A frame that solves a section adds its own facts below the Type, such as Normal, Plane Origin and Thickness.
- A failed geometry check is a warning notice over the tables, one line per failing body and test.

### Select and menu

- An item under the pointer or the keyboard takes the hover wash.
- The chosen item uses the selected-row treatment: `--wash-selected` with the 2 px `--primary` marker on its left edge. No checkmark.
- An item's label never wraps. Detail after the label truncates with an ellipsis.

### Run select

- The trigger shows the run's name.
- Each item in the list shows the run's name, then its description, which truncates.
- Beside the select, the run's folder reads as a read-only field, at most 36 rem wide, followed by an Open Folder icon button.

### Status bar

- A 24 px bar under the dock holds the task bar on its left and the status line on its right.
- The status line is `text-label` in `--muted-foreground`, at most 40 % of the bar, and truncates. It never moves the controls beside it.

### Toast

- A toast confirms an action that finished out of view, such as a screenshot download. It rises in the bottom-right corner over the status bar and fades after 3 s.
- The `popover` plane and `text-label`, with no edge and no buttons.

### Task bar and drawer

- The task bar is hidden while there are no tasks; the status bar stays.
- The whole task bar is one button with the pointer cursor. A click opens or closes the drawer.
- The bar counts tasks by status, each count with its icon. A failed count is `--destructive`.
- The drawer slides up over `--duration-panel`. The summary moves from the bar into the drawer header.
- Every second row has the `--stripe` fill. Rows take the hover wash.
- A click on a row opens it: a done or reused task opens its result, a running task its live log and a failed task its error. An `arrow-up-right` icon in the row-actions column marks this at rest.
- Status icons: `check` for done, `copy-check` for reused, a turning `loader-circle` for running and `x` for failed. A reused row's tooltip names the run it reused.

## 3D view

- The camera's near and far planes enclose every visible actor, glyphs included. The view recomputes them after every scene change and every camera move. The near plane may come as close as 0.001 of the far plane's distance (`NEAR_TOLERANCE` in `panels/scene.py`), so a model zoomed in close keeps its glyphs.
- Each renderer smooths edges as Render Settings asks: FXAA in Client mode and 8× MSAA in Server mode by default.
- The orientation axes sit in the corner, colored X red, Y green and Z blue, without labels. The scene holds no other axes.
- Hover outlines an entity's feature edges at 2 px in `--primary` mixed 60 % into `--background`. The outline is a hidden actor the browser shows, so it follows the pointer without a server round trip; in Server mode no outline shows. Selection outlines them at 2 px in `--primary`. The hovered or selected entity's glyph arrows and pins draw in the same color as its outline.
- A tint, fill or accent copy keeps its points on the geometry it covers. A depth offset draws it over that geometry instead of lifting it off, in layers: feature edges, then face tint, then selection, then hover (`Layer` and `FORWARD_STEP` in `viz/field_display.py`). The offset holds at any zoom. A body drawn in a plain color also takes a 30 % `--primary` tint when selected. A field plot takes no tint, because its colormap can contain green.
- A body with no field to show draws shaded in its body color, with its feature edges outlined. Body colors keep neighbouring bodies apart without looking like a field. Body `i`, counted from 0 in the run's body order, has the color `oklch(0.72 0.06 h)` with hue h = (255 + 137.5·i) mod 360.
- A run solved on a 2D section offers its own forms under Show As: Section, Mirrored and Revolved for an axisymmetric section; Section and Extruded for a Cartesian one. Each opens in its 3D form.
- Extruded draws the section through its thickness, with the section plane at mid-depth like the Setup ghost. Condition glyphs sit on the front face, a boundary's tint, hover and selection fill its face through the depth, and field lines run at the depth of their seeds.

### View toolbar

| Action | Icon | Key |
|---|---|---|
| Fit | Lucide `scaling` | `F` with nothing selected |
| Top | Lucide `arrow-down-to-line` | |
| Bottom | Lucide `arrow-up-to-line` | |
| Right | Lucide `arrow-left-to-line` | |
| Left | Lucide `arrow-right-to-line` | |
| Front | `js/src/assets/view-front.svg` | |
| Back | `js/src/assets/view-back.svg` | |
| 2D | `js/src/assets/view-2d.svg` | |
| Isometric, from (−0.6, −0.8, 0.5) with Z up | Lucide `box` | |
| Axes | Lucide `axis-3d`, as a toggle button | |
| Screenshot, downloaded by the browser at twice the panel's size | Lucide `camera` | |

### View menu

- A right click that stays put opens the view's menu at the pointer. A right press that moves more than 4 px drags, so right-drag zoom opens nothing.
- Show Axes is a checkbox item on the same state as the toolbar's Axes button.
- Render Settings… opens the Render Settings dialog.

### Render Settings

- The dialog docks at the bottom right above the status bar with no scrim, so the views it changes stay in sight. A choice applies at once; a slider applies on its release.
- The dialog stays open while you work in the page. Only its close buttons, or Escape pressed inside it, close it.
- Renderer chooses Client or Server for the view the menu was opened on, on the same state as the toolbar toggle. Everything else applies to every 3D view of the user, in every workbench.

| Group | Setting | Values | Default |
|---|---|---|---|
| Antialiasing | Client | Off, FXAA, SSAA | FXAA |
| Antialiasing | Server | Off, FXAA, MSAA, SSAA | MSAA |
| Edges | Outline Width | 0.5 to 6 px | 2 px |
| Edges | Feature Angle | 5° to 90° | 30° |
| Edges | Highlight Width | 0.5 to 6 px; a selected silhouette draws 1.5× and a selected 2D boundary 2× | 2 px |

- Client mode has no MSAA, because VTK.wasm ignores the window's multisamples.
- Client SSAA renders the canvas 2× each side and the browser averages it down. VTK.wasm maps the pointer by the device pixel ratio alone, so the view scales each pointer offset by 2 before VTK.wasm reads it.
- Server SSAA renders each side √5 times larger. Lines and text scale by √5 to keep their width, and its translucent stage peels depth like the renderer's own.
- The server keeps the settings per user under the user config directory, not in browser storage: browser storage is separate per port, and the server needs the settings before its first frame.
- Reset to Defaults puts every setting back.

## Voice

- Labels are nouns in Title Case. Tooltips and messages use sentence case.
- A unit follows its value after a space: `200 mm²`.
- Panels hold no explanatory sentences. Explanations go into tooltips.
- An error or empty state uses at most two sentences: what happened, then what to do.
- Internal figures such as cell counts and sync times go to the log, never to the UI.

## Checks

`tests/workbench/test_design.py` runs these checks with the rest of the suite. Each failure lists the offending lines.

| Check | Fails on |
|---|---|
| No `transition-all` | `transition-all` anywhere in `js/src` or the Python package. |
| Colors only from tokens | A hex color, `oklch(`, `rgb(` with numbers, or `hsl(` outside `styles.css`. |
| No raw palette classes | `bg-white`, `text-black`, `border-[#…]`, or a Tailwind palette class such as `text-green-500`. |
| No native `title` tooltips | A `title` prop on a plain HTML element in any catalogue workbench's UI tree. |
