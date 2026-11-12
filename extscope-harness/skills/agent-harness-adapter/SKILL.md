---
name: agent-harness-adapter
description: >-
  Destination rules for distributing .agents/ assets (skill / rule / workflow / fragment) across agent platforms: the target paths,
  projection modes and project-harness.py usage for Antigravity, Codex and Claude Code. Use it when distributing a rule, skill or
  workflow under .agents/ with /extscope and you must decide which agent platforms dst should land on (.agents, .claude, .codex,
  ~/.claude/skills, CLAUDE.md, AGENTS.md), when writing the symlink target of a *.extscope.json, when running extscope-harness,
  or when diagnosing a skill that is not visible on some platform.
---

# Agent Harness Adapter: multi-dst platform distribution rules

Use together with `/extscope`: extscope decides "what to move"; this package decides "where each platform puts it and in what format".
Package root: `extscope-harness/` (an extscope sub-package).

## 1. Per-platform dst cheat sheet (the source is always a neutral asset)

| Asset | Antigravity | Codex | Claude Code |
| :-- | :-- | :-- | :-- |
| skill (directory containing SKILL.md) | `<ws>/.agents/skills/<n>` | `<ws>/.agents/skills/<n>` (shared with AGY) | `~/.claude/skills/<n>` or `<ws>/.claude/skills/<n>` |
| rule (`*.md`) | `<ws>/.agents/rules/<n>.md` | `<ws>/.codex/rules/` (symlink_children) | appended to `<ws>/CLAUDE.md` (sentinel block) |
| fragment (`*.md`) | `<ws>/.agents/AGENTS.md` block | `<ws>/AGENTS.md` block | `<ws>/CLAUDE.md` block |
| workflow | no dedicated channel | **not projected**; exposed as a Skill (`$name`); a lone `.agents/workflows/` is not a portable command | same as left, wrapped as a skill |
| cursor | rule/fragment only → `<ws>/.cursorrules` | | |

Key points:
- **Claude Code does not read `.agents/skills`**. Distributing only to `.agents/` means Claude cannot see it; add a `.claude/skills/` target as well.
  (Existing biz scope convention: `copy_inject` to `/run/ext/skills/<n>`, then `symlink` to `/workspace/.agents/skills/<n>` and `/workspace/.claude/skills/<n>`.)
- Codex and Antigravity share `.agents/skills`; one symlink serves both.
- Do not invent a second projection for workflows; wrap them as SKILL.md.
- Manifests use only `~/…` (home) and `./…` (project directory); the dispatcher expands them from `--user-home` / `--workspace` (both required).

## 2. Two paths

**Execution (the same program in the container and on the host)**: the manifest holds only relative paths; home and project directory come from arguments, and both are required.
```bash
extscope-harness --harness claude-host --assets-root <neutral-assets-dir> --workspace <project-dir> --user-home <home> --emit-dist-plan > plan.json
extscope-dist --config dist.json apply plan.json --all      # writes go through dist (backup/rollback)
```
Without `--emit-dist-plan` it applies directly (no backup); give Codex extra trust paths with `--trust-path <abs>`.
The package ships no launcher with hard-coded paths; put environment-specific fixed values in that environment's own configuration.

## 3. Decision flow (before distributing with /extscope)

1. Asset type → check §1 to decide which platforms need it.
2. Must Claude see it? → there must be a `.claude/skills/` target or `harness-claude`.
3. A new scope declares only the neutral `copy_inject`; unless it is a single-machine special case (such as a host-claude-only scope), do not hard-code multi-platform symlinks in a biz scope; reference `solution-multi-agent-harness` instead.
4. `extscope lint` → `dag` → run `project-harness.py` in the container or the launcher on the host → verify the targets exist (`ls -l` the target, SKILL.md is readable).

## 4. Known pitfalls (confirmed by reading the code)

- **MCP support (json_merge / toml_mcp_merge)**: implemented in `_project_mcp` of `project-harness.py`. Antigravity and Claude are merged into the `mcpServers` JSON; Codex is incrementally merged into the `[mcp_servers."<name>"]` block of `config.toml`, and the `/workspace` and `/root` placeholders are expanded automatically.
- **Required preflight**: an empty or unknown harness, or a missing canonical or required asset, exits non-zero; with multiple harnesses, all inputs and existing MCP configs are validated first, then projected. A broken or non-object JSON file is rejected and the original file is kept.
- **`tiered` (probe + physical degradation) is off by default**: no manifest currently sets `"tiered": true`; enable it yourself when the Codex sandbox blocks out-of-bounds symlinks; a failed probe returns exit 2 (fail-closed, stderr is JSON).
- **An existing non-symlink target is not overwritten** (warn and skip); an old physical directory keeps the new skill version from taking effect.
- In Claude, rules are appended to CLAUDE.md as sentinel blocks (`<!-- block: rule-<stem> -->`); re-running replaces them idempotently, so do not hand-edit the block contents.
- The asset directory is specified by the consumer with `--assets-root` and has no default (`--canonical-root` is a deprecated alias); the canonical staging area is retired. A missing directory, or one with no usable assets, exits non-zero; zero assets never counts as success. `~/.agents` is no longer used.
- Scope `src` paths are resolved inside the source directory that the consumer sets.

## 5. Where the specs live

`spec/canonical-asset-spec.md` (asset types), `spec/harness-adapter-spec.md` (manifest schema, lifecycle). After changing the dispatcher run `python3 -m pytest tests -q` (file by file; mind memory).

## Refactoring goal and legacy boundary

This package is an optional agent-harness delivery plugin of ExtScope; a consumer must import it explicitly and pin its version before use. Full plugin discovery/API and FHS extraction are still drafts, so the current dispatcher must not be equated with a finished plugin. The table above keeps the legacy container-target notes; generic `.agents` host publishing is already rejected by configuration, and container targets and trust must be migrated separately. A capability source is not a projection destination; do not delete the source `.agents`.
