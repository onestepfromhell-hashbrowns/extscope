# extscope-dist

Standalone distribution engine. It copies, symlinks, or injects markdown blocks
into target paths, with a dry run, a scope gate, staged atomic publish, backups,
and rollback. Standard library only. It imports nothing from `extscope` and can
run anywhere Python >= 3.11 runs, with or without the core installed.

The core (`extscope`) decides **what** to deliver (declare, plan, bind, verify).
`extscope-dist` does the **writing**. They meet only at a JSON file.

## Plan input

Either schema is accepted:

- `extscope-dist.plan.v1` (native):
  ```json
  {"schema": "extscope-dist.plan.v1", "items": [
    {"method": "copy",     "src": "/abs/skill-dir", "target": "/workspace/.harness/skills/x"},
    {"method": "symlink",  "src": "/abs/file",      "target": "/workspace/link"},
    {"method": "md_block", "src": "/abs/frag.md",   "target": "/workspace/AGENTS.md", "block_id": "rules"}
  ]}
  ```
  Optional `sha256` on an item pins the source (file digest or `tree:` digest).
- `extscope.plan-preview.v1`: the JSON printed by `extscope plan ...`. Read as plain data.
  `copy_inject`, `symlink`, `md_block_inject` are applied; `volume_mount` is skipped
  (container-only). Any other module (`key_inject`, `env_inject`, `custom_hook`, ...)
  is refused, never silently skipped.

`${NAME}` in paths is expanded from config `variables` and `--var NAME=VALUE`. An unknown name is a refusal.

## Configuration

No path is built in. Lookup order for the config file: `--config`, `$EXTSCOPE_DIST_CONFIG`,
then the per-user default (`$XDG_CONFIG_HOME/extscope/dist.json`, `%APPDATA%\extscope\dist.json`,
or `~/Library/Preferences/extscope/dist.json`). State directory: `--state-dir`,
`$EXTSCOPE_DIST_STATE_DIR`, config `state_dir`, then the per-user state dir.

```json
{
  "variables":     {"WS": "/home/<user>/work"},
  "remap":         [{"from": "/workspace", "to": "${WS}"}],
  "allowed_roots": ["${WS}"],
  "denied_roots":  ["${WS}/secrets"],
  "state_dir":     "/home/<user>/.local/state/extscope/dist"
}
```

- `remap`: longest-prefix match on the target; the source path is never remapped.
- `allowed_roots` is required to apply. With none configured, every write is refused.
- A target must be inside `allowed_roots` and outside `denied_roots`, checked on the
  lexical path and on the real path of its parent directories (symlink escapes are refused).

## Source binding (optional)

`extscope bind` records the exact bytes it approved. Pass that file with `--receipt FILE` to
`plan` or `apply`: every `copy` / `md_block` source must be listed and unchanged (file sha256, or
the `extscope.tree.v1` tree hash, re-implemented here), else the run is refused before any write
(`source_not_bound`, `receipt_source_mismatch`). A preview plan must also carry the plan hash the
receipt was bound to (`receipt_plan_mismatch`). A bound directory that contains a symlink is
refused (`receipt_tree_has_symlink`), because `copy` follows symlinks and the hash does not.
Without `--receipt`, nothing ties the source bytes to the core's approval.

## Commands

```bash
extscope-dist show-config
extscope-dist plan   plan.json                      # dry run; writes nothing
extscope-dist apply  plan.json --filter-prefix DIR  # only targets under DIR (repeatable)
extscope-dist apply  plan.json --all                # explicit "everything"
extscope-dist apply  plan.json --all --force        # back up and replace unmanaged targets
extscope-dist verify   <manifest-id|path>
extscope-dist rollback <manifest-id|path> [--force]
```

Exit codes: 0 ok, 2 refused (JSON `{"error": code, "detail": ...}` on stderr), 4 verify found drift.

Behavior:
- `apply` without `--filter-prefix` or `--all` is refused (`scope_required`).
- An existing target that this tool did not write is a `conflict` unless `--force`;
  forced replacements are moved to `state_dir/backups/<id>/`.
- Files are built next to the target and moved in with `os.replace`. A failure part-way
  undoes the items already applied.
- Every apply writes `state_dir/manifests/<id>.json` with the digest written per target.
  Re-running an unchanged plan applies 0 items.
- `rollback` restores backups. If a target changed since apply, it stops and reports
  (`rollback_incomplete`); `--force` overrides.
- `copy` follows symlinks inside the source tree. `symlink` needs symlink permission on
  Windows (Developer Mode or admin); use `copy` otherwise.

## Concurrency

`apply` and `rollback` take an exclusive `flock` on the parent directory of every target they
touch (sorted order, released together; refused with `lock_timeout` after 30 s). Without it, two
runs that read the same `AGENTS.md` and each add their own block would silently drop one of them
(measured: 34 of 60 paired runs lost a block). The lock lives on the directory itself, so it holds
across different configs and `state_dir`s. Local filesystems only; a plain `plan` takes no lock.

## Cleaning up `state_dir`

Every apply leaves `backups/<id>/` and `manifests/<id>.json`; nothing else removes them.

```bash
extscope-dist prune                              # list only, things at least 30 days old
extscope-dist prune --older-than 7 --apply       # delete rolled-back runs and orphan backups
extscope-dist prune --include-applied --apply    # also applied runs that a newer run fully replaced
```

- `rolled_back`: the run was undone; its manifest and backups are no longer needed.
- `orphan`: a backup directory with no manifest (an apply that failed and undid itself); never
  touched while younger than one hour, so a running apply is safe.
- `superseded` (opt-in): every target was written again by a newer run. It can no longer be rolled
  back, but its backups are the only copy of what existed before the first apply, so it is not
  removed unless you ask.
- The latest manifest for any target is never removed: it is how dist knows it owns that target.
  Deleting it would turn the next apply of that target into a `conflict`.

## Tests

```bash
python3 -m pytest -q tests
```
Includes a test that fails if the source imports `extscope` or contains fixed `/opt`, `/srv`, `/var/lib` paths.

## Modules (outside this package)

Besides `copy`, `symlink`, `md_block`, a plan item can name a **module**:
`{"method": "chmod", "config": {...}}`. dist contains no module code. It loads the
packages listed in `modules.enable` (import names) and refuses any method that is not
provided by one of them (`module_not_enabled`). Module-specific policy lives in
`modules.policy.<method>`; a module that needs policy and finds none refuses.

```json
{ "modules": { "enable": ["extscope_modules"],
               "policy": { "key_inject": {"allowed_dst_roots": ["/run/user/1000/keys"]} } } }
```

dist remaps and root-checks each module's `path_fields` before the module runs, gates items
by `--filter-prefix`, records the module's JSON record in the manifest, and calls the module
for `plan`/`apply`/`undo`/`verify`. Items a module marks `irreversible` (hooks) are shown in
`plan`, listed in `apply` output, and reported as `not_undoable` by `rollback`.
The protocol is documented in `src/extscope_dist/modreg.py`. The ready-made modules are in
`../extscope-modules/`.
