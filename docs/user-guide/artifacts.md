# Artifacts and retention

An artifact store keeps the expensive intermediate products of a run, the solve-frame STEP, the Elmer mesh and the finished bundle, under a key computed from the inputs that made them. A second run that needs the same mesh fetches it instead of meshing again, and a run already solved on another machine comes back without solving. Retention is the other half: a policy that says which bundle files leave after a run, so a sweep of thousands of runs does not fill the disk. Both are driver settings, so neither changes a run's identity.

## Configure a driver with a store

```python
import femforge as ff

store = ff.artifacts.LocalArtifactStore("artifact-store", link=True)

driver = ff.Driver(
    runs_dir="runs",
    artifacts=ff.artifacts.ArtifactCache(store),
    reuse=True,
    bundle_policy=ff.run.BundlePolicy.FIELDS,
    retention=ff.run.RetentionPolicy.lean(),
)
```

With `link=True` the store hardlinks files into the run directory where the volume allows, else it copies them. A hardlinked file shares its bytes with the store, so never edit a materialized file in place.

## Kinds and keys

Each artifact belongs to a kind, a module-level [ArtifactKind][femforge.artifacts.ArtifactKind] passed by reference. A kind names its spec model, the files a complete artifact holds, and a producer version that you bump to invalidate every existing key of the kind.

| Kind | Spec | Files | Key |
|---|---|---|---|
| [STEP][femforge.run.STEP] | the geometry source and the frame | `geometry.step` | the parametric geometry's parameters, or the authored STEP's content digest, plus the frame |
| [ELMER_MESH][femforge.run.ELMER_MESH] | [MeshSpec][femforge.run.MeshSpec] | `mesh.header`, `mesh.nodes`, `mesh.elements`, `mesh.boundary`, `mesh.names`, `groups.json`, and `elmergrid.log` when produced | the STEP key, the mesh dimension, the element size, the gmsh options, `deterministic`, the tag selectors with their mesh sizes in domain order, and the sorted material tags |
| [RUN_BUNDLE][femforge.run.RUN_BUNDLE] | [RunSpec][femforge.run.RunSpec] | `manifest.json`, `result.json`, and what the bundle policy adds | the run's [cache_key][femforge.run.cache_key], so the store key and the run id agree |

A key is the hex SHA-256 of the spec's JSON with sorted keys, after every field marked `HashExclude()` is removed at any depth. The mesh key leaves out conditions, material properties, physics and metadata, so two configs that differ only in a boundary condition share one mesh. A cached mesh is rebound to the current config's domain: materials by tag and tags by selector. A mismatch raises [MeshRebindError][femforge.run.MeshRebindError], so a condition never lands on the wrong boundary.

An authored STEP under a `Solid()` frame passes through: the solve meshes the file itself, so no STEP artifact is cached for it.

## The store

[LocalArtifactStore][femforge.artifacts.LocalArtifactStore] keeps each artifact in a directory under the store root:

```text
artifact-store/
├── step/
│   └── glad-dove-5d20e8b1/
│       ├── artifact.json
│       └── geometry.step
├── elmer_mesh/
│   ├── .staging/                          producer workdirs
│   ├── jolly-crow-91a3f6c2/
│   │   ├── artifact.json
│   │   └── mesh.header ...
│   └── kind-coot-07be44d9.lock/            a producer's claim
└── run_bundle/
    └── calm-avocet-3f9a1c07/
        ├── artifact.json
        └── manifest.json ...
```

The directory name is `<adjective>-<noun>-<hash8>`, a pure function of the key, so a key resolves to its directory without a scan. Every persisted key carries that name beside it: [ArtifactRecord][femforge.artifacts.ArtifactRecord], [ArtifactRef][femforge.artifacts.ArtifactRef] and the manifest's `mesh_name` all derive it from the key on load.

An artifact exists exactly when its `artifact.json` sidecar can be read. The sidecar lists every file with its size and SHA-256, the exact spec payload that was hashed, the producer's host and process id, and the tool versions (femforge, the commit, gmsh, Elmer). A publish fills a staging directory and renames it into place, so the first publisher wins and a second one discards its copy. A fetch always checks file sizes, and checks hashes on request. An unreadable sidecar is moved aside as `<name>.corrupt-<stamp>` so a fresh publish can land.

## What the driver fetches and publishes

| Stage | With an artifact cache |
|---|---|
| `prepare_geometry` | Fetches the solve-frame STEP, or builds and publishes it. |
| `mesh` | Fetches the Elmer mesh, or meshes in a staging directory, drops the `.msh` and publishes the rest. |
| reuse check | Fetches a published bundle with the run's identity when no local one is reusable. |
| `publish` | Publishes the bundle files the bundle policy selects, leaving out the store-sourced inputs, unless the run ended in `error`. |

The manifest records each store-sourced input in `artifacts` and the bundle's own entry in `published`. That is enough to put the inputs back later (`femforge artifacts rehydrate`) and lets retention drop them freely.

A producer claims its key before it produces, refreshes the claim every 10 s, and another producer waits for it and serves its result. A claim silent for 120 s is stale and the next producer breaks it.

## ArtifactCache and the remote tier

[ArtifactCache][femforge.artifacts.ArtifactCache] always consults its local store first. Its optional `remote` tier takes any [ArtifactStore][femforge.artifacts.ArtifactStore]: a hit there is pulled through into the local store, and every publish is pushed to it after the local commit.

| Parameter | Default | What it does |
|---|---|---|
| `local` | required | The local tier, always consulted first and always published to. |
| `remote` | `None` | The remote tier, pulled through on a hit and pushed to after a publish. |
| `poll_s` | `2.0` | Seconds a producer waits between checks while another claim holds its key. |
| `heartbeat_s` | `10.0` | Seconds between refreshes of a held claim while producing. |
| `verify` | `False` | Verify file hashes when pulling from the remote tier. |
| `push_required` | `False` | Raise when a publish cannot reach the remote tier, instead of logging a warning. |

With a remote tier, producers claim keys there, so machines that share it produce each artifact once. femforge ships the directory store only; a second `LocalArtifactStore` on a shared volume can serve as the remote tier.

The `FEMFORGE_ARTIFACTS` variable names a store root for the command line: the `femforge artifacts` commands use it when you pass no `--root`, and `femforge doctor` checks its free disk space. The driver never reads it; pass the cache to `Driver(artifacts=...)`.

## Bundle policy

[BundlePolicy][femforge.run.BundlePolicy] decides which bundle files a publish carries. It also decides reuse: under `FIELDS` or `FULL`, a local bundle whose field output was purged no longer counts as reusable.

| Policy | Publishes |
|---|---|
| `CORE` | Manifest, result, sidecars, sif, scalars, logs and geometry: everything but fields and meshes. |
| `FIELDS` | Core plus the VTU output. The default. |
| `FULL` | Every bundle file, meshes and partitioning included. |

## Retention policy

[RetentionPolicy][femforge.run.RetentionPolicy] holds one [Cleanup][femforge.run.Cleanup] rule per class of bundle file. `NEVER` keeps the files, `ON_SUCCESS` removes them when the run ended `ok`, and `ALWAYS` removes them whatever the status. `manifest.json` and `result.json` belong to no class a policy can name, so they stay.

| Field | Covers |
|---|---|
| `geometry` | `geometry.step` |
| `gmsh_mesh` | `*.msh` |
| `elmer_mesh` | `mesh.header`, `mesh.nodes`, `mesh.elements`, `mesh.boundary` |
| `partitioning` | `partitioning.*` and `elmergrid_partition.log` |
| `sif` | `case.sif` and `ELMERSOLVER_STARTINFO` |
| `fields` | `*.pvtu` and `*.vtu` |
| `scalars` | `*.dat`, `*.dat.names` and the per-rank `*.dat.<rank>` |
| `logs` | `elmergrid.log`, `elmersolver.log`, `convergence.jsonl`, `convergence_targets.json`, `run.log` |
| `sidecars` | `entities.json`, `boundaries.yml`, `components.yml`, `mesh.names`, `entities.sif`, `groups.json` |
| `run_dir` | The whole bundle; when it applies, nothing else matters. |
| `cached_inputs` | Not a class: the escalation for files the store can re-fetch. The stricter of this rule and the class rule wins. |

Every class defaults to `NEVER` and `cached_inputs` to `ALWAYS`.

| [Preset][femforge.run.Preset] | Constructor | Effect |
|---|---|---|
| `default` | `RetentionPolicy.default()` | Keep everything the run cannot re-fetch; drop store-sourced inputs. |
| `keep_all` | `RetentionPolicy.keep_all()` | Keep everything, store-sourced inputs included. |
| `lean` | `RetentionPolicy.lean()` | Drop the regenerable meshes and partitioning after a successful run. |
| `minimal` | `RetentionPolicy.minimal()` | Lean plus the field output after a successful run. |
| `nuke` | `RetentionPolicy.nuke()` | Remove the run directory whatever happened. |

The driver applies its policy in the `cleanup` stage, and `evaluate(retention=...)` overrides it for one call. Every pass that removes something appends a [CleanupRecord][femforge.run.CleanupRecord] to the manifest's `cleanups`. `Run.purged(file_class)` reads them back, and `Run.mesh()` names the pass when the VTU is gone.

## Purge existing bundles

`femforge purge` applies a policy to every bundle under a runs directory after the fact. It is a dry run unless you pass `--apply`:

```console
femforge purge --runs-dir runs --preset lean
femforge purge --runs-dir runs --preset lean --fields always --only-status ok --older-than 30 --apply
```

The dry run prints the files and gigabytes per class it would free. Each field of the policy has an override flag, such as `--fields` or `--cached-inputs`, taking `never`, `on_success` or `always`. `--only-status` limits the purge to bundles in the given statuses and `--older-than` to bundles created at least that many days ago. The purge skips links, directories that are not bundles, unreadable manifests, and `running` bundles whose manifest was written within `--running-grace` hours (6 by default).

## Browse the store

`femforge artifacts` lists and inspects a local store: `ls`, `show`, `path`, `materialize` and `rehydrate`. A reference is a full key, a hex prefix of 8 or more characters, a `<adjective>-<noun>-<hash8>` name, an artifact directory, or a path inside one. The store root is `--root`, else `FEMFORGE_ARTIFACTS`, else the store a path argument lies inside. `rehydrate` fetches a bundle's store-sourced inputs back beside it. See [CLI](cli.md) for every option.
