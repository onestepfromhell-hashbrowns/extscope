# extscope-container

Container-side companion to `extscope-dist`. Standard library only; imports nothing from the
other extscope packages. It reads plan JSON (`extscope plan` output) as data and acts on a
container through a transport. Nothing is built in: every container path comes from the
config (`allowed_roots`, `denied_roots`, `remap`, `env_dir`), every host path from a flag or
the plan. Config lookup: `--config`, `$EXTSCOPE_CONTAINER_CONFIG`, then the per-user config dir.

```json
{ "allowed_roots": ["/opt/app", "/run/secrets"], "denied_roots": [], "remap": [],
  "env_dir": "/etc/profile.d", "state_dir": "/home/<user>/.local/state/extscope/container",
  "hooks_allowed": false, "podman": "podman" }
```

| Command | What it does |
| --- | --- |
| `creation-plan PLAN [--format podman\|json] [--selinux]` | `-v src:dst:ro` lines for the mounts a container must be **created** with |
| `run PLAN --container ID [--dry-run] [--force] [--allow-hooks]` | applies the plan inside a running container |
| `lease mint\|check\|ensure\|purge --tmpfs-path P --disk-path P --ext-root D` | canary lease: tmpfs token must equal disk token; `ensure` purges and re-mints when it does not |
| `refs register\|release\|list\|status DIR` | who still uses a staging directory (`host_symlink`, `container_lease`, `host_registration`; unknown kinds keep it alive) |
| `gc --base DIR (--active NAME... \| --podman-active) [--dry-run]` | delete orphan project directories (verified, sizes measured) |
| `purge --base DIR PROJECT` | delete one project's `ext/` (or the project directory) |
| `quadlet-secret NAME TARGET [--link P] [--mode M]` | print a Quadlet `Secret=` snippet |
| `secret-link establish\|verify LINK SOURCE` | symlink to a tmpfs secret / check it |

`run` order: mount check, then one tar (copy, symlink, md_block, env, key) delivered with
`podman cp`, then chmod/chown, then hooks. A plan hash is stored per container; an unchanged
plan is skipped unless `--force`.

Rollback: before the first write, `run` snapshots every target (its tree, or that it is absent) and the mode and owner of each chmod/chown target. If a later step fails, the snapshot is restored newest-first: created paths are removed, overwritten trees are put back, modes and owners are reset. The error says how many items were restored, or lists what could not be restored (`ROLLBACK INCOMPLETE`). Hooks cannot be undone; the error names the hooks that had already completed. Parent directories created by a failed run are left in place.

Mounts cannot be added to a running container, so a missing
mount is reported with a pointer to `creation-plan`. Hooks need both `--allow-hooks` and
`hooks_allowed: true`. `gc` without `--active`, `--podman-active` or `--dry-run` is refused,
so "nothing is running" is never assumed.

Transports: `podman` (real) and `dir` (a host directory standing in for the container root,
for tests and offline staging; `--dir-root`). Verified against a real rootless podman container.
POSIX only for `refs` (uses `fcntl`).
