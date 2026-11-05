# extscope-modules

Optional pipeline modules for `extscope-dist`.
Standard library only; imports neither `extscope` nor `extscope_dist`. Enable with
`"modules": {"enable": ["extscope_modules"], "policy": {...}}` in the dist config.

| Method | Config | Policy (`modules.policy.<method>`) | Rollback |
| --- | --- | --- | --- |
| `chmod` | `target`, `mode` | none. setuid/setgid/sticky refused; symlink targets refused | restores previous mode |
| `chown` | `target`, `owner` (`user[:group]`) | `allowed_owners` and `allowed_roots` required | restores previous uid/gid |
| `key_inject` | `src`, `dst`, `chmod` (default `0600`), `sensitivity` | `allowed_dst_roots` required; optional `require_fs_types` (Linux, e.g. `["tmpfs"]`); `allow_group_other_access` to permit modes wider than 0600 | restores the previous file |
| `env_inject` | `key`, `value`, `dir`, `mode` (default `0600`) | `allowed_dirs` required; optional `default_dir` | restores the previous file |
| `pkg_import` | `root`, `locator`, `as_type` (external\|native), `package_id`, `commit`, `exports` | `allowed_source_roots` required for local sources; `allow_remote_git` for URLs | removes the package tree and its metadata |
| `custom_hook` | `script_path`, `sha256`, `args` | `script_roots` required; `require_sha256` (default true); `interpreter` (default `/bin/sh`); `timeout_seconds` (default 60) | **none** (marked irreversible) |

`pkg_import` fetches into `<root>/.staging`, checks the content digest (the same algorithm `extscope pkg doctor` uses; a test makes the core validate what it wrote), renames the tree into place, and writes the metadata last, so a half import is never treated as managed. It refuses to overwrite, never runs source scripts or git hooks, requires a commit for `external`, and rejects locators that contain credentials.

Security properties:
- No built-in paths: every directory comes from policy and plan.
- `env_inject` single-quotes the value to prevent shell injection. Keys must be valid identifiers.
- `key_inject` creates the temp file with `0600` before writing, sets the final mode before the atomic rename, and stores only a salted digest in the manifest. `chown` inside `key_inject` is not supported; use a separate `chown` item.
- `chown` uses `os.chown` with an owner allow-list instead of spawning a shell process.
- `custom_hook` needs a script under an allow-listed root, not group/world writable, pinned by sha256; it runs without a shell string, with a minimal environment and a timeout.
- Ephemeral secret routing: `key_inject` with `sensitivity: secret` and `persist: false` uses `tmpfs_dst` as the destination when reading an `extscope plan` file.
- Dry run (`extscope-dist plan`) never runs a hook or writes a secret.

POSIX only: `chown` is refused where `os.chown` is missing; `chmod` on Windows only toggles the read-only flag.

## Tests
```bash
python3 -m pytest -q tests     # needs ../extscope-dist/src on the path (the test file adds it)
```


## config_merge

Merge named entries into one table of an existing JSON or TOML config without touching other keys.
`{"method":"config_merge","config":{"target":"/p/mcp.json","format":"json","key":"mcpServers","entries":{"srv":{"command":"c"}}}}`;
TOML uses `"table"` and writes `[table."name"]` sections. `on_exist`: `replace` (default) or `keep`.
Refuses a symlink or non-regular target, unparseable existing content, and a result that does not parse.
Preserves the file mode. Rollback restores the previous bytes (or removes a file apply created) and stops on drift unless `--force`.
