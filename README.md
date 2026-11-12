# ExtScope

ExtScope delivers extensions to containers and agent accounts. You write a declaration. ExtScope checks it, plans the work, and copies the files to the right place.

- **License:** MIT (see `LICENSE`).
- **Language:** Python 3.11 or later. It uses the standard library only.
- **Platforms:** Linux. The installer needs GNU `sed` and `readlink -f`; other POSIX systems are untested.
- **Paths:** FHS and XDG. The code has no fixed default paths.
- **Overview page:** `docs/overview.html` (diagrams, one file, no network needed). Published with GitHub Pages: <https://onestepfromhell-hashbrowns.github.io/extscope/>.

## Install

```bash
pip install .                      # installs the "extscope" command
./install.sh --user                # installs to ~/.local
sudo ./install.sh --system         # installs to /usr/local
./install.sh --help                # shows all options
```


## Use

```bash
extscope --help
extscope validate --scope /abs/path/my-tool.extscope.json
extscope plan --scope /abs/path/my-blueprint.extscope.json --entry <entry>
extscope digest /path/to/payload
```

Build a container image:

```bash
podman build -t extscope:latest .
podman run --rm extscope:latest --help
```

## Test

```bash
PYTHONPATH=src python3 -m pytest -q
```

Each sub-package also has a `tests/` directory.

## Packages

Each package uses only the standard library. No package imports another. They share data only through JSON files (plans, manifests, configuration).

| Package | Task | Writes to targets? |
| :-- | :-- | :-: |
| `extscope` | Validate, plan, bind, verify, query. | No |
| `extscope-dist` | Copy or link files to host targets. Make backups. Roll back. | Yes |
| `extscope-modules` | Optional steps for `extscope-dist` (mode, owner, key, environment, hook, import). | Yes, through `extscope-dist` |
| `extscope-container` | Move files into containers. Manage leases and cleanup. | Yes |
| `extscope-harness` | Decide which file goes where for each agent platform. | No. It makes a plan for `extscope-dist`. |
| `extscope-tools` | Scan for secrets and mounts. Keep an anomaly ledger. Check layer rules. | No |

## Directories

All paths come from configuration, arguments, or environment variables.

| Purpose | System mode | User mode |
| :-- | :-- | :-- |
| Programs | `/usr/local/bin`, `/usr/local/lib/extscope*` | `~/.local/bin`, `~/.local/lib` |
| Configuration | `/etc/extscope` | `$XDG_CONFIG_HOME/extscope` |
| Cache | `/var/cache/extscope` | `$XDG_CACHE_HOME/extscope` |
| Runtime | `/run/extscope` | `$XDG_RUNTIME_DIR/extscope` |
| State (manifests, backups) | The `state_dir` you set | The `state_dir` you set |

- `install.sh` creates empty configuration and cache directories only.
- Uninstall keeps configuration.
- The tools do not read `/etc/extscope` by themselves. The lookup order is `--config`, then `$EXTSCOPE_*_CONFIG`, then the per-user default path.

## Declarations

A declaration is a file named `*.extscope.json`. Each declaration has a layer. A higher layer can depend on a lower layer. A lower layer must not depend on a higher layer.

| Layer | Content |
| :-- | :-- |
| `system` | Base infrastructure. The lowest layer. |
| `biz` | Single capabilities (`include_items`). |
| `solution` | A set of other declarations (`include_scopes`). |
| `preset` | Role templates. |
| `project` | One project. The highest layer. |

The core also accepts the old names `tooling`, `custom`, `resource`, `res`, and `tool`.

`extscope-tools authority` finds reverse dependencies. By default it checks only `tooling`, `custom`, `solution`, `preset`, and `project`. To check all five layers, pass `--manifest` with `{"layers":{"system":1,"biz":2,"solution":3,"preset":4,"project":5}}`.

## Workflow

| Step | Command | Writes? |
| :-: | :-- | :-: |
| 1 | `extscope init` prints a template. | No |
| 2 | A person writes the declaration and commits it. | Person |
| 3 | `extscope validate --scope <abs path>` checks the structure. | No |
| 4 | `index`, `resolve`, `dag`, `query` show the content. | No |
| 5 | `extscope plan` makes a plan (`extscope.plan-preview.v1`). | No |
| 6 | `extscope bind --allow-root` makes a receipt with source digests. | No |
| 7 | `extscope verify --receipt` checks the sources again. | No |
| 8 | `extscope-dist plan [--receipt]` shows what will change. | No |
| 9 | `extscope-dist apply [--receipt]` writes the files and a manifest. | Yes |
| 10 | `extscope-dist verify <manifest>` finds drift. | No |
| 11 | `extscope-dist rollback <manifest>` restores the backups. | Yes |

The plan digest (`plan_sha256`) covers the declaration only. It does not cover the source bytes. Add `--receipt` to steps 8 and 9 to check the source bytes.

To retire a declaration, set `deprecated` or `deprecated_by`. To disable it, set `enabled` to `false`.

## Distribution chain

```
remote repository  ->  source directory  ->  destination
```

- `include_items.src` points to a real path in the source directory.
- `dst_fhs` is the destination path.
- After a source changes, run `plan`, `bind`, and `apply` again.

## Safety rules

- The core is read-only. It opens files with `O_NOFOLLOW` and rejects symbolic links in declarations.
- `extscope-dist` takes a `flock` on the parent directory of each target. It makes a backup before each change. It rolls back all changes if one step fails.
- `extscope-dist prune` lists old backups. It deletes them only with `--apply`.
- Errors are short by default. Set `EXTSCOPE_DEBUG=1` or use `--debug` to see details.

See the `README.md` in each package directory for more information.

## Contributing and security

- Contributions: `CONTRIBUTING.md`.
- Vulnerability reports: `SECURITY.md`.
