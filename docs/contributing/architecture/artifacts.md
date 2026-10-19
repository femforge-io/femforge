# Artifacts and retention

Every expensive intermediate of a run is an artifact, keyed by the pydantic spec that produced it. A run asks the store before it computes, publishes what it made, and keeps in its bundle only what the store cannot give back. This page covers the store contract, how keys and names are derived, the publish and claim protocol, what the driver fetches and publishes per stage, the cache tiers, retention and purge, the mesh artifact, and the process boundary around gmsh. [Artifacts and retention](../../user-guide/artifacts.md) in the user guide covers configuration and the commands.

## The store protocol

[ArtifactStore][femforge.artifacts.ArtifactStore] is one storage tier addressed by `(kind, key)`. Its contract is small enough for any backend to implement: an artifact exists exactly when its sidecar can be read, and `put` is first-wins and atomic (`src/femforge/artifacts/store.py:28-71`).

| Method | Contract |
|---|---|
| `record(kind, key)` | the committed sidecar, or None when this tier lacks the key |
| `exists(kind, key)` | whether this tier holds the key |
| `fetch(kind, key, into, verify=)` | materialize every listed file into `into` and return the record |
| `put(kind, key, source, record)` | commit the listed files as the key; False when the key already exists |
| `delete(kind, key)` | remove the key from this tier |
| `keys(kind)` | every committed key of a kind |
| `try_claim(kind, key)` | claim the key for production, or None while another live claim holds it |

Failures raise `ArtifactError`, with `ArtifactMissing` for a key no tier holds and `ArtifactCorrupt` for files that disagree with their sidecar (`src/femforge/artifacts/store.py:16-25`).

[LocalArtifactStore][femforge.artifacts.LocalArtifactStore] is the directory implementation, and femforge ships no other. Its root holds one directory per kind:

| Path under the root | Holds | Where |
|---|---|---|
| `<kind>/<name>/` | one committed artifact: its files and `artifact.json` | `src/femforge/artifacts/local.py:97-99` |
| `<kind>/.staging/<name>.<host>.<pid>.<id>/` | a producer's workdir, unique per process and attempt | `src/femforge/artifacts/local.py:101-105` |
| `<kind>/<name>.lock/` | a producer's claim | `src/femforge/artifacts/local.py:334-337` |
| `<kind>/<name>.corrupt-<stamp>/` | an artifact whose sidecar could not be read, moved aside so a fresh publish can land | `src/femforge/artifacts/local.py:110-120` |

## Kinds

A kind is a module-level [ArtifactKind][femforge.artifacts.ArtifactKind] constant that callers pass by reference; no registry looks one up by name (`src/femforge/artifacts/kind.py:49-51`).

| Field | Meaning |
|---|---|
| `name` | the store path segment, restricted to `[a-z0-9_]+` because case-insensitive filesystems are in play |
| `spec_type` | the pydantic model whose instances key the kind |
| `producer_version` | bump it to invalidate every existing key of the kind |
| `required_files` | the files a complete artifact holds; a producer that leaves one out fails the publish |
| `produce` | the default producer for a miss, or None when the caller supplies one per request |
| `key` | an identity override, for a kind whose key must equal an existing hash |
| `in_place` | the producer works in the consumer's own directory and publish copies out of it |

`BuiltinKind` is the only place the built-in kind names are spelled (`src/femforge/artifacts/kind.py:19-29`). The built-in kinds live beside the run identity in `src/femforge/run/kinds.py`:

| Kind | Spec | Files | Produced by |
|---|---|---|---|
| [STEP][femforge.run.STEP] | `StepSpec`: the geometry source and the frame | `geometry.step` | the kind's own producer: build the geometry, then convert it into the frame (`src/femforge/run/kinds.py:102-121`) |
| [ELMER_MESH][femforge.run.ELMER_MESH] | [MeshSpec][femforge.run.MeshSpec] | the four ElmerGrid mesh files, `mesh.names`, `groups.json` | the driver, which owns gmsh and ElmerGrid (`src/femforge/run/kinds.py:176-182`) |
| [RUN_BUNDLE][femforge.run.RUN_BUNDLE] | [RunSpec][femforge.run.RunSpec]: the config | `manifest.json`, `result.json`, and what the bundle policy adds | the run itself, in place (`src/femforge/run/kinds.py:219-227`) |

`json_record_kind` declares a further kind whose artifact is one `record.json` holding a typed result, such as one point of a sweep; `RecordKind.get_or_produce` serves the stored value or computes and publishes it under a claim (`src/femforge/artifacts/records.py`).

## Identity and names

`artifact_key` hashes the kind name, its producer version and the spec payload with every `HashExclude()` field removed, unless the kind overrides its key (`src/femforge/artifacts/keys.py:97-105`). The bundle kind overrides it with `cache_key`, so a bundle's store key and its run id agree (`src/femforge/run/kinds.py:219-226`). [Config objects](config-objects.md#the-run-identity) covers the run and STEP payloads.

An [ArtifactKey][femforge.artifacts.ArtifactKey] is a `str` of 18 to 64 lowercase hex characters, typed by the spec model it was hashed from, so a mesh key cannot pass where a bundle key belongs (`src/femforge/artifacts/keys.py:27-46`). Its `name` is the `<adjective>-<noun>-<hash8>` directory name.

**Every persisted key carries its directory name beside it.** [ArtifactRecord][femforge.artifacts.ArtifactRecord] and [ArtifactRef][femforge.artifacts.ArtifactRef] derive `name` from `key` in a validator and never read it from input (`src/femforge/artifacts/sidecar.py:80-81`, `src/femforge/artifacts/sidecar.py:187-199`), and `RunManifest.mesh_name` follows `mesh_key` the same way (`src/femforge/run/manifest.py:121-122`). A manifest's references therefore read straight to a store directory, and because the name is a pure function of the key, resolving a key never scans the store.

A name carries about 50 bits, so two keys can share one in principle. The store detects it: `record` returns None and logs an error when the sidecar holds a different key, and `put` raises (`src/femforge/artifacts/local.py:143-150`, `src/femforge/artifacts/local.py:221-222`). A collision is never served.

## The sidecar records existence

`artifact.json` is the commit marker, so it is always written last (`src/femforge/artifacts/sidecar.py:19-20`). An `ArtifactRecord` lists every file with its size and SHA-256, the exact spec payload that was hashed, the producer's host and process id, and the tool versions (`src/femforge/artifacts/sidecar.py:69-105`). Tool versions are provenance and never enter a key (`src/femforge/artifacts/sidecar.py:51-66`).

| Situation | Behavior | Where |
|---|---|---|
| the sidecar cannot be parsed | the directory is quarantined and the key reads as absent | `src/femforge/artifacts/local.py:132-141` |
| a fetch | sizes are always checked, hashes only with `verify` | `src/femforge/artifacts/local.py:167-182` |
| `link=True` | files are hardlinked where the volume allows, else copied; a hardlinked file shares its bytes with the store, so never edit one in place | `src/femforge/artifacts/local.py:158-165` |

A consumer records an `ArtifactRef`: the kind, the key, where it came from (`local`, `remote` or `produced`) and the consumer-relative files it materialized (`src/femforge/artifacts/sidecar.py:23-33`, `src/femforge/artifacts/sidecar.py:176-199`).

## First-wins publish

`LocalArtifactStore.put` commits with one directory rename (`src/femforge/artifacts/local.py:210-258`):

1. When the source is a staging directory, prune it to the listed files; otherwise copy or link the listed files into a fresh staging directory.
2. Write the sidecar into the staging directory atomically.
3. Rename the staging directory onto the final path. `os.replace` fails when the target directory already exists, which is the first-wins signal (`src/femforge/artifacts/local.py:36-38`).
4. On that failure, or when the key was already committed, discard the copy and return False. The key is content-addressed, so the winner's copy is as good as the loser's.

A reader therefore sees a whole artifact or none, with no lock taken on the read path.

## Heartbeat claims

A claim is a directory, because `mkdir` is atomic on NTFS, POSIX filesystems and SMB shares (`src/femforge/artifacts/lock.py:1`). [DirLock][femforge.artifacts.DirLock] writes an `owner.json` with the host, the process id, the start time and the last heartbeat (`src/femforge/artifacts/lock.py:45-60`).

| Constant | Value | Meaning | Where |
|---|---|---|---|
| `HEARTBEAT_S` | 10 s | how often a holder refreshes its claim | `src/femforge/artifacts/lock.py:26-27` |
| `STALE_AFTER_S` | 120 s | how long a claim may stay silent before the next claimant breaks it | `src/femforge/artifacts/lock.py:29-30` |

`try_acquire` breaks a stale claim and retries once (`src/femforge/artifacts/lock.py:103-118`). A claim whose owner file is unreadable ages by its directory's modification time (`src/femforge/artifacts/lock.py:84-91`). [keep_alive][femforge.artifacts.keep_alive] beats from a daemon thread for as long as its block runs, in a copy of the caller's context, so a failed beat's warning keeps the run id and the telemetry route (`src/femforge/artifacts/lock.py:134-158`). A holder never needs a timeout sized to its longest job: a live holder never looks stale, and a dead one goes stale in two minutes.

The driver claims runs with the same lock: it takes `runs_dir/<run_id>.lock`, keeps it alive while it solves, and a second process asking for the same run checks every 2 s for a reusable bundle (`src/femforge/run/driver.py:54-55`, `src/femforge/run/driver.py:216-234`). [reap_runs][femforge.run.reap_runs] settles a driver that died: a claim counts as silent when its directory is gone, its heartbeat is stale, or its owner ran on this host under a process id that no longer exists (`src/femforge/run/reap.py:53-69`).

## What the driver fetches and publishes

The driver runs its pipeline as the [Stage][femforge.run.Stage] members in order, `PUBLISH` and `CLEANUP` last (`src/femforge/run/stages.py:11-45`). Three stages and the reuse check before them touch the artifact cache:

| Step | Kind | With an artifact cache | Without one | Where |
|---|---|---|---|---|
| reuse check | `RUN_BUNDLE` | the local bundle when it is reusable, else the published bundle fetched into the run directory | the local bundle only | `src/femforge/run/driver.py:237-277` |
| `prepare_geometry` | `STEP` | fetch the solve-frame STEP, or build and publish it; an authored STEP under `Solid()` passes through uncached | build and convert in the run directory | `src/femforge/run/driver.py:566-588` |
| `mesh` | `ELMER_MESH` | fetch the mesh, or mesh and convert in a staging directory, drop the `.msh` and publish the rest | mesh and convert in the run directory, keeping the `.msh` | `src/femforge/run/driver.py:593-607` |
| `mesh`, either way | | rebind the groups to this config, take ElmerGrid's ids from `mesh.names`, write `entities.json` | | `src/femforge/run/driver.py:628-647` |
| `publish` | `RUN_BUNDLE` | the files the bundle policy selects, minus the store-sourced inputs, unless the run ended in `error` | skipped | `src/femforge/run/driver.py:343-355`, `src/femforge/run/driver.py:390-432` |

Every store-sourced input lands in `manifest.artifacts` as it arrives (`src/femforge/run/driver.py:550-557`), and `manifest.cached_inputs()` lists their files (`src/femforge/run/manifest.py:134-136`). That list is what lets retention drop the inputs freely, what `rehydrate_bundle` fetches back, and what `prune_store_sourced` removes (`src/femforge/run/artifacts.py:145-169`). The publish stage records `manifest.published` and writes the manifest before it commits, so the published manifest already names its own store entry, and it runs after the run's log context has closed `run.log`, so the logs class can be published and cleaned up too (`src/femforge/run/driver.py:344-345`).

[BundlePolicy][femforge.run.BundlePolicy] maps to retention classes: `CORE` publishes everything but fields and meshes, `FIELDS` adds the VTU output, and `FULL` publishes every class (`src/femforge/run/artifacts.py:95-117`). It also decides reuse: under `FIELDS` or `FULL`, a local bundle whose field output a cleanup removed no longer counts as reusable (`src/femforge/run/driver.py:274-275`).

## The cache tiers

[ArtifactCache][femforge.artifacts.ArtifactCache] puts a typed front door on one or two stores (`src/femforge/artifacts/cache.py:60-88`):

| Request | Behavior | Where |
|---|---|---|
| a local hit | materialize from the local store | `src/femforge/artifacts/cache.py:111-124` |
| a remote hit | fetch into a local staging directory, commit it locally, then materialize from the local copy | `src/femforge/artifacts/cache.py:126-156` |
| a miss with a producer | claim the key on the authoritative tier, the remote one when it exists; while another claim holds it, poll and serve its result; after acquiring, check once more, then produce under `keep_alive` | `src/femforge/artifacts/cache.py:168-214` |
| producing | work in the consumer's directory for an in-place kind, else in a staging directory; check the required files; commit locally; push to the remote tier when this producer won | `src/femforge/artifacts/cache.py:216-260` |
| a remote push that fails | log a warning, or raise with `push_required` | `src/femforge/artifacts/cache.py:299-319` |

Claiming on the remote tier is what makes machines that share it produce each artifact once.

## Retention internals

A bundle's files fall into the retention classes of `RunFile`; each member carries the bundle-relative patterns it covers (`src/femforge/common/files.py:72-95`). `BUNDLE` holds `manifest.json` and `result.json` and is protected: no policy field names it, and the planner never removes a file it matches (`src/femforge/run/retention.py:338-339`).

[RetentionPolicy][femforge.run.RetentionPolicy] ties each field to its class with `Map(RunFile.X)` metadata, and `rules()` reads the pairs back through `mapped_items`, so no table beside the model lists the classes (`src/femforge/run/retention.py:67-109`). `cached_inputs` is no class: it escalates the rule for files the store can re-fetch, and the stricter rule wins. `Cleanup` members escalate in definition order, `NEVER` then `ON_SUCCESS` then `ALWAYS`, and `ON_SUCCESS` applies to status `ok` only (`src/femforge/run/retention.py:20-45`).

`plan_cleanup` resolves a policy against one bundle (`src/femforge/run/retention.py:342-403`):

1. Refuse a bundle that is a symlink or a junction.
2. When the `run_dir` rule applies, plan the whole directory and stop.
3. Glob each class's patterns, skipping links, protected files and paths an earlier class already claimed.
4. Escalate the rule of a store-sourced path by `cached_inputs`, and keep the paths whose rule applies to the run's status.

`apply_cleanup` never raises: a path it cannot remove lands in `failed`. It only reports a planned run directory, because the caller must write the manifest one last time first (`src/femforge/run/retention.py:406-434`). The driver's `cleanup` stage applies the plan and appends a `CleanupRecord` to the manifest; the directory itself goes after the stage's finish event (`src/femforge/run/driver.py:435-500`).

## Purge

`femforge purge` and `python -m femforge.run.purge` apply a policy to bundles that already exist, as a dry run unless you pass `--apply`:

```console
femforge purge --runs-dir runs --preset lean --dry-run
femforge purge --runs-dir runs --preset minimal --fields always --apply
femforge purge --runs-dir runs --preset lean --elmer-mesh always --only-status error abandoned --apply
python -m femforge.run.purge --runs-dir runs --preset lean
```

The parser adds one override flag per `RetentionPolicy` field, so a new file class gets its flag without a CLI change (`src/femforge/run/purge.py:243-300`). [CLI](../../user-guide/cli.md#purge) lists every option. `purge_runs` walks the runs directory and leaves a directory alone for one of these reasons (`src/femforge/run/purge.py:129-212`):

| Reason | When |
|---|---|
| `link` | the directory is a symlink or a junction |
| `not_a_bundle` | it holds no `manifest.json` |
| `unreadable` | the manifest does not validate; a foreign or older manifest is skipped, never rewritten blind |
| `running` | the manifest says `running` and was written within `--running-grace`, 6 h by default, since a live driver rewrites it at every stage (`src/femforge/run/purge.py:23-24`) |
| `status` | its status is outside `--only-status` |
| `age` | it is younger than `--older-than` days |
| `failed` | its cleanup raised; one bundle never aborts the pass |

Each pass that removes something appends a `CleanupRecord` with trigger `purge` (`src/femforge/run/purge.py:88-126`).

## The mesh artifact

[MeshSpec][femforge.meshing.MeshSpec] holds everything that decides the Elmer mesh files and their group names, and nothing that decides only the solve (`src/femforge/meshing/spec.py:25-49`):

| Field | Why it is in the key |
|---|---|
| `step_key` | the geometry, in its solve frame |
| `model_dim` | the frame's body dimension |
| `global_mesh_size`, `gmsh_options`, `deterministic` | the sizes, as floats in metres, and the mesher settings; thread count and terminal output are `HashExclude()` |
| `tags` | each tag's selector and mesh size, in domain order, because an unnamed body's group name joins the matched tag names in that order |
| `materials` | the sorted material tags, because an unnamed body's group name starts with its material tag |

Conditions, material properties, physics and metadata stay out, so configs that differ only there share one mesh ([mesh_key][femforge.run.mesh_key], `src/femforge/run/kinds.py:145-187`).

The artifact keeps ElmerGrid's files, never the `.msh`, plus `groups.json`, a `MeshGroupsFile` of body and boundary group records and the element count (`src/femforge/meshing/groups.py:156-258`). `rebind_groups` turns the records back into groups against the current domain: materials by tag, tags by selector, and each body's name composed again with `compose_group_name`. Any mismatch raises `MeshRebindError`, so a condition never lands on the wrong boundary (`src/femforge/run/artifacts.py:35-92`). The driver then sets each group's id from `mesh.names`, because ElmerGrid renumbers the groups, boundary ids after body ids (`src/femforge/run/driver.py:618-627`, `src/femforge/elmer/mesh_names.py`).

The boundary groups come from body adjacency. `partition_boundary_faces` sorts every boundary face into an interface `A__B`, an external shell `A__EXT` or, for a single body on the symmetry axis, `A__AXIS` (`src/femforge/meshing/boundary.py:126-157`). The partition is total: once a model defines physical groups, gmsh saves only the elements that belong to one, so a face outside every group would vanish from the mesh. It is also sorted, so gmsh assigns the same ids on every run. A face that borders no known body is skipped, because it has no name to take (`src/femforge/meshing/boundary.py:134-135`).

## The gmsh firewall

gmsh, the mesh backend implemented today, runs in the driver's process or behind a process boundary (`src/femforge/meshing/backend.py:141-258`):

| Mesher | gmsh runs | Notes |
|---|---|---|
| [GmshMesher][femforge.meshing.GmshMesher] | in this process | gmsh loads on the first job and is finalized after every one, so no model outlives its job (`src/femforge/meshing/gmsh_backend.py:18-19`) |
| [WorkerMesher][femforge.meshing.WorkerMesher] | in a child `python -m femforge.meshing.worker` | gmsh, its license and its crashes stay out of the caller's process |

`WorkerMesher` writes the job as JSON into a temporary directory and starts the worker on it (`src/femforge/meshing/backend.py:224-258`). The worker logs every record to stdout as one JSON line, meshes, and writes an outcome file, turning any exception into a failed outcome (`src/femforge/meshing/worker.py:33-65`). The parent relays each line into its own log with `worker=mesh` bound and strips `FEMFORGE_TELEMETRY` from the child's environment, so the child writes no telemetry of its own and its records reach the parent's terminal, `run.log` and telemetry through the relay (`src/femforge/meshing/backend.py:232-248`). A worker that exits without an outcome, or runs past `timeout_s`, raises `MeshError` with the tail of its log.

[block_gmsh][femforge.meshing.block_gmsh] installs an import hook that makes every later `import gmsh` raise, and refuses when gmsh is already loaded (`src/femforge/meshing/firewall.py:18-34`). An entry point calls it to prove it never loads gmsh.

`tests/run/test_gmsh_firewall.py::test_a_worker_driver_never_loads_gmsh` guards the boundary. It runs `tests/run/worker_driver.py` in a fresh interpreter, which blocks gmsh and then solves through a `WorkerMesher` and a stand-in Elmer three times: uncached, producing a cached mesh, and reusing that mesh for a config with the magnetization flipped (`tests/run/worker_driver.py:134-159`). The test asserts three `ok` results, a written `.msh`, the installed gmsh version on the mesh record, and no gmsh among the driver process's modules (`tests/run/test_gmsh_firewall.py:32-50`).
