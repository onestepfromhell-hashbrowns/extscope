# Harness Adapter Specification

## 1. Concept and responsibilities

The Harness Adapter dynamically projects the neutral assets in `/run/ext/canonical/` onto target paths inside the container, following the file contract (File Protocol & Discovery Hierarchy) of a specific agent/IDE.

An adapter must provide:
1. **A declarative contract (`manifests/harness-<id>.json`)**: defines the target path and projection mode (`symlink`, `inject`, `convert`) for each asset type.
2. **Idempotent execution**: repeated runs do not damage existing files, and broken symlinks are repaired automatically.
3. **Zero external dependencies**: runs inside the container with only the Python 3 standard library.

---

## 2. Manifest schema (`harness-<id>.json`)

```json
{
  "$schema": "urn:extscope:schema:harness-adapter-v1",
  "harness_id": "codex",
  "name": "OpenAI Codex CLI & IDE Adapter",
  "version": "1.0.0",
  "projections": {
    "skills": {
      "mode": "symlink_dir",
      "target_dir": "./.agents/skills",
      "fallback_target_dir": "~/.codex/skills"
    },
    "rules": {
      "mode": "copy_or_link",
      "target_dir": "./.codex/rules"
    },
    "fragments": {
      "mode": "md_block_append",
      "target_file": "./AGENTS.md"
    },
    "mcp": {
      "mode": "toml_mcp_merge",
      "target_file": "~/.codex/config.toml"
    }
  },
  "runtime_bootstrap": {
    "codex_trust": {
      "enabled": true,
      "config_file": "~/.codex/config.toml",
      "trusted_paths": ["./"]
    }
  }
}
```

---

## 3. Execution lifecycle

```text
1. Discovery    : scan /run/ext/canonical/{skills,rules,fragments,mcp}
2. Match        : load the manifest for the given harness_id
3. Projection   :
   - create target directories (mkdir -p)
   - create symlinks
   - append tagged blocks (sentinel block injection)
   - merge configuration (toml / json)
4. Verification : confirm symlinks point correctly and no broken links remain
```

---

## 4. Layer boundaries (revised 2026-10)

- **extscope (generic)**: only assembles assets into the canonical tree `canonical/{skills,rules,fragments,mcp}`; it knows no agent paths.
- **adapter (platform-specific)**: only reads the canonical tree and projects it into each platform's own home according to the manifest. The generic `.agents/` is not a projection target.
- Location of the neutral asset tree: specified by the consumer with `--assets-root`; the tool has no default (the old `/var/lib/extscope/canonical` staging area is retired; the in-container mount point `/run/ext/canonical` is merely a mount consumption point).

## 5. Manifest path rules

A manifest records only that model's conventions. Every path is `~/<path>` (from home) or `./<path>` (from the project directory),
and must not contain absolute paths or any host or account information; `trusted_paths` can only be `./`. The actual home and project directory are supplied by the run-time arguments
`--user-home` and `--workspace` (both required), and extra trust paths by `--trust-path`. A non-conforming manifest is rejected at
preflight. `manifests/harness-*.json` is authoritative; the host variants (`*-host`) differ only in the relative path layout
(skills → `~/skills`, rules → `~/rules`, fragments → `~/AGENTS.md`, mcp → `~/config.toml`).

## 6. MCP projection rules

- mode `toml_mcp_merge` (Codex): incrementally writes the `[mcp_servers."<name>"]` block of `config.toml`.
- mode `json_merge` (Claude, Antigravity): merges into the `mcpServers` object of the JSON.
- The strings `/workspace` and `/root` inside canonical MCP asset values are still replaced with `--workspace` and `--user-home` (a legacy convention; it belongs to the asset content, not to the manifest).
- Same-name server: the canonical definition overrides the same-named entry in home (JSON replaces that key; TOML replaces that `[mcp_servers.<name>]` block).
- Existing servers with other names are kept (confirmed by reading the code: `_merge_json_mcp`, `_merge_toml_mcp`).
- A JSON read failure, a non-object, a non-object mcpServers, or an unsafe target is rejected and the original file is kept; a valid change uses a same-directory temp file, fsync, atomic replace and directory fsync. This is single-file atomicity, not yet a multi-target transaction or a cross-writer lock.

## 7. Draft: AF standalone package/plugin

The ExtScope core only handles neutral delivery; agent-harness-adapter is an optional standalone plugin that the AF consumer declares and pins explicitly, and the core must not implicitly import an agent platform. The asset directory is specified by the consumer (the canonical staging area is retired); the container path /run/ext/canonical is only a mount consumption point. A generation is published on the same filesystem and mounted after the consumer pins it, with no cross-filesystem rename and no new control daemon. Running a plugin in the same process is not OS isolation; the full plugin API, leases and runtime migration are not finished yet.
