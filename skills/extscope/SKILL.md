---
name: extscope
description: >-
  Operating SOP for the ExtScope package set (extscope core + dist / modules / harness / container / tools): writing and validating
  *.extscope.json / ext-default.json, expanding plans and binding sources (plan / bind / verify), writing content to the host with
  extscope-dist (dry-run, --receipt, rollback, prune), deciding per-agent-platform placement with extscope-harness, checking layer
  dependencies, and installing with install.sh. Use it when delivering skills / MCP / environment variables to a host or container,
  or when diagnosing extscope rejection codes.
  It does not handle container lifecycle (container-ctl) or ssh entry points.
---

# ExtScope

**The core only reads and plans; writing is done by separate packages.** Each package uses only the standard library and none imports another; they communicate only through JSON (plans, receipts, manifests, config).

| Command | Responsibility | Can write? |
|---|---|---|
| `extscope` | validate / digest / plan / bind / verify / dag / query / index / resolve / init / pkg | No |
| `extscope-dist` | Host-side distribution: copy / symlink / md_block; dry-run, `--receipt`, scope gate, target lock, backup, rollback, prune | Yes |
| `extscope-modules` | Optional dist modules: chmod / chown / key_inject / env_inject / config_merge / custom_hook / pkg_import (enabled via `modules.enable`) | Yes (through dist) |
| `extscope-harness` | Rules for "what goes where" on each agent platform; stores only `~/` and `./` relative paths; `--emit-dist-plan` produces a dist plan | No |
| `extscope-container` | Container transport: creation-plan / run / lease / refs / gc / purge / secret-link | Yes |
| `extscope-tools` | gate (secret and mount scanning) / anomaly (anomaly ledger) / authority (layer dependencies) | No |

## How a declaration lands
`init` → a person writes and commits it → `validate` → `plan` → `bind` (receipt = source byte fingerprints) → `verify` → `extscope-dist plan` → `apply --receipt` → `verify` → (if needed) `rollback`.
The hash of `plan` binds only the declaration, **not the source bytes**; without `--receipt` there is no binding between the core's approval and the actual write.

## Locations and distribution chain
- **Distribution chain**: remote repository → source directory → dst (destination). A scope's `include_items.src` points at a real path inside the source directory, and `dst_fhs` is the destination path; there is no intermediate staging repo.
- **Declarations** (written by people, kept in git): `<scopes-dir>/{system,biz,solution,preset}/`; project instance `<project-dir>/ext-default.json`.
- The configuration `/etc/extscope` and the dist state `state_dir` are supplied by the user; `install.sh --system|--user [--prefix]` **installs the program only** and does not create or manage state or configuration (README §1.2).
- Do not create `.deploys/.extensions` inside a project or hand-write symlinks; make every change through `ext-default.json`.

## Layers
System → Biz (atomic capabilities, `include_items`; by convention no `include_scopes`, not enforced by the tools) → Solution (pure composition via `include_scopes`) → Preset (role templates) → Project (`ext-default.json`). Name files `<layer>-<domain>-<feature>`, with the suffix `.extscope.json`; templates are always `*.template`.
`category` also accepts the legacy names tooling / custom. The default layer table of `authority` **does not include system or biz**; to check all five layers pass `--manifest <file path>` (it takes a file, not a JSON string) whose content is `{"layers":{"system":1,"biz":2,"solution":3,"preset":4,"project":5}}`; when only a single scope is given, a scope it references that cannot be found is reported as `unresolved_include`, so list the referenced scope files as well.

## Forbidden anti-patterns
1. **Making a scope file a symlink**: the core reads with `O_NOFOLLOW` and always answers `SYMLINK_REJECTED`. To publish a scope, copy the real file into the authoritative directory and commit it.
2. **Search paths / first match wins**: the current design is one explicit directory, and every scope referenced by `include_scopes` must be in it (`scope reference is not in explicit catalog` = missing file).
3. **Bypassing the source directory to reach an old directory directly**, or pointing a scope's `src` at a non-authoritative path.
4. **Silent degradation**: failures must be loud; the exception class and message are printed only with `EXTSCOPE_DEBUG=1` (opaque by default, on purpose).
5. **Writing credentials into a scope**: use the `*_FILE` contract; `extscope-tools gate` blocks plaintext.
6. **Editing a downstream copy directly without writing back to the source** (see the next section).

## A deployed copy is not the source (reverse sync)
> The `where` / `origin` / `diff` / `pullback` commands below exist only in newer code; if the host's `/usr/local` has an older version, `extscope where` answers obsolete and `extscope-dist` has no such subcommands. Check with `extscope --help` and `extscope-dist --help` first, otherwise re-run `install.sh --system`.
dist targets (`~/.claude`, project directories, the `skills/` deployment directory) are all **artifacts**. If you edit them directly, the next distribution overwrites them and there is no version record. After editing, write the change back to the source:
```bash
extscope-dist --config dist.json origin <modified path>      # source src + git repo + next step
extscope where --scope-dir <scopes-dir> <src>               # which scope declares it, and who includes it
extscope-dist --config dist.json diff|pullback <manifest>   # pullback is a dry run by default, --apply writes; requires source_roots to be set
```
After writing back, check `git diff` in the source repo and commit, then `bind` → `apply`. See `skills/extscope-dist/SKILL.md` for details.

## Common operations
```bash
# Add a scope (init only prints a template, it does not write a file)
extscope init --category biz --description "..." biz-tools-foo > <scopes-dir>/biz/biz-tools-foo.extscope.json
extscope validate --scope <scopes-dir>/biz/biz-tools-foo.extscope.json                      # --scope must be an absolute path

# Inspect (read-only)
extscope dag|index|resolve|query --scope-dir <abs-dir> ...
extscope plan --scope <abs file> --entry <name|uuid> [--var NAME=VALUE] > plan.json         # fails if {{NAME}} has no --var
extscope bind --scope <abs file> --entry <name|uuid> --allow-root <abs> > receipt.json

# Write to the host (always rejected without allowed_roots; global flags such as --config must come before the subcommand)
extscope-dist --config dist.json plan  plan.json [--receipt receipt.json]                   # dry run, no lock, no write
extscope-dist --config dist.json apply plan.json --all|--filter-prefix DIR [--receipt ...]  # unmanaged files need --force (they are backed up)
extscope-dist --config dist.json verify|rollback <manifest>
extscope-dist --config dist.json prune [--older-than DAYS] [--include-applied] [--apply]    # lists only by default
extscope-dist inventory --scan <state_dir or a parent directory>... [--target-prefix P] [--src-prefix P] [--scope NAME]   # read-only; across state_dirs, "who wrote which destination, and from which source"

# Placement for each agent platform: harness only plans; home and project directories are given as (required) arguments, then dist does the writing
extscope-harness --harness claude-host --assets-root <neutral assets dir: skills/ rules/ fragments/ mcp/, assembled by the consumer; may be symlinks pointing into the source directory> \
  --workspace <project dir> --user-home <home> --emit-dist-plan > plan.json
```
The manifest of `claude-host` is `~/skills`, `~/rules`, `~/mcp.json` (no `.claude`), so `--user-home` must be the **final `.claude` directory** for output to land in `<...>/.claude/skills`. Before projecting, use `--emit-dist-plan` to see the actual targets.
`dist.json` example: `{"allowed_roots":["<dir>"],"state_dir":"<dir>","remap":[...],"modules":{"enable":["extscope_modules"]}}`. A distribution **target** may be a symlink (`method: symlink`); a **declaration** may not. Each manifest entry records `src`, `src_digest`, `scope`, `target`, `written`.
**Prefer symlinks**: for host installation (`install.sh` defaults to symlink) and for distribution into agent directories, use links first; use copy only when the agent does not accept links, the runtime identity cannot read the source directory, the version must be pinned, or the destination is inside a container.

## Containers and scanning
- `extscope-container creation-plan ...` produces the mounts to bind when creating a container; `run` applies the plan to a running container; credentials go only through `quadlet-secret` / `secret-link` (tmpfs 0600) and never into a scope.
- Before deployment: `extscope-tools gate <dir>` (a violation exits 1, values are not printed); layers: `extscope-tools authority [--manifest m.json] <scope>...`.

## Mapping from old commands (old engine `container-ext`)
`lint`→`validate`; `flatten`→`plan`; `apply-host` / `rollback-host`→`extscope-dist apply` / `rollback`; `run`→`extscope-container run`; `gate run`→`extscope-tools gate`; `hub` and the four Spokes have been removed.

See the package `README.md` and each sub-package's `README.md` / `SKILL.md` for details (`skills/extscope-dist/SKILL.md` covers rejection codes and known behavior).
