# extscope-tools

Independent tools. Standard library only. None of them needs a declaration or a plan.

**`gate PROJECT`** scans a tree and exits 1 on violations. Rules: `secrets_dir_tracked` (a secrets
directory is tracked by git), `sensitive_assignment` (a secret-looking name has a literal value; `*_FILE`,
placeholders, empty values, allow-listed names and JSON/YAML numbers/booleans are fine),
`url_credentials`, `private_key`, `unsafe_secret_mount` (Quadlet `Volume=` or a scope mount of a
secrets directory that is not read-only), `unscannable` (oversized, binary or unreadable files
fail closed). Values are never printed. `--allowlist FILE` is a JSON list of names; `--waivers FILE`
is a JSON list of `{path, rule, reason, expires}` and expired waivers are ignored.
Names are compared without `_`, `-`, `.` and case, so `apiKey` = `API_KEY`.

**`anomaly --db FILE record|list|archive|check`** keeps structured events in SQLite. The database
path is required (`--db` or `$EXTSCOPE_ANOMALY_DB`). Secrets are redacted before storage. If SQLite
cannot be written the event goes to `<db>.fallback.jsonl`; `record` reports which one was used.

**`authority [--manifest FILE] SCOPE_FILE...`** checks layer rules: a higher layer may depend on a
lower one, never the reverse. Default layers: tooling 1, custom 2, solution 3, preset 4, project 5;
categories not in the map are not judged. The optional manifest sets `layers` and per-scope
`category` / `allowed_dependencies`, and then every supplied scope must be listed.

**`assemble --base RELEASE --out-root DIR --add SRC=DEST... [--source KEY=VALUE] [--allow-dirty]`** builds a new
canonical release: copies the base, overlays each SRC at DEST (relative, no `..`), rewrites `DELIVERY.json` in one
fixed format (`generation` = hash of the file digests, `base_generation`, `sources` = `<repo>: <sha>`), runs the
`canonical` check, and only then publishes `DIR/<generation>/`. A source with uncommitted changes is refused unless
`--allow-dirty` (recorded as `<sha>+dirty`). It never touches `current` or `skills`; switching stays a manual step.

```bash
python3 -m pytest -q tests
```
