# Agent Harness Adapter (ExtScope cross-agent dependency decoupling package)

> **Category**: `extscope/extscope-harness` (an extscope sub-package)  
> **Core goal**: remove the tight coupling between business capability modules (Biz Scopes) and the path contracts and formats of specific agents (Antigravity / Codex / Claude / Cursor, etc.).  
> **Design principles**: first principles, zero core intrusion, declarative adapters.

---

## Division of labor with extscope-dist (writes go through dist)

The adapter decides "what goes where"; extscope-dist does the "writing" (backups, manifest, rollback):

```bash
python3 adapters/project-harness.py --harness claude-host --assets-root ASSETS \
  --workspace W --user-home H --emit-dist-plan > plan.json      # writes no files
extscope-dist --config dist.json plan  plan.json
extscope-dist --config dist.json apply plan.json --all
```
- skills/rules/fragments → `symlink` / `copy` / `md_block`; MCP and Codex trust → `config_merge` (extscope-modules; the dist config needs `modules.enable: ["extscope_modules"]`).
- Sandbox probe degradation (`tiered`, `fallback_target_dir`, `extra_dirs_registration`) and other `runtime_bootstrap` steps are still handled by the adapter's own apply; `--emit-dist-plan` lists them and exits 3 when it meets one (`--allow-residual` lets it pass).
- The direct apply path without `--emit-dist-plan` is kept, but it does not get dist's backup and rollback.
- `extscope/plugins/agent-harness-adapter` is frozen (preview only, no execution side); do not extend it further.

---

## 🏛️ First-Principles Architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. Canonical Asset Layer (SSOT)                                         │
│    Neutral assets {skills,rules,mcp,fragments} (path set by --assets-root)│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. External Harness Adapter Layer                                       │
│    Independent manifests (harness-*.json) define each agent's file       │
│    paths and format contract                                            │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. Project Harness Dispatcher (Python Standalone)                       │
│    Reads the neutral assets and the target harness contract, and         │
│    dynamically projects the assets into the target workspace             │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────┬──────────────────┬──────────────────┬────────────────┐
│   Antigravity    │      Codex       │   Claude Code    │     Cursor     │
│ .agents/skills/  │ <repo>/AGENTS.md │ ~/.claude/skills │ .cursorrules   │
│ .agents/rules/   │ .codex/rules/    │ CLAUDE.md        │                │
│ .agents/mcp.json │ .codex/config    │                  │                │
└──────────────────┴──────────────────┴──────────────────┴────────────────┘
```

---

## 📦 Directory layout

```text
extscope-harness/                            # (formerly agent-harness-adapter)
├── README.md                          # this manual
├── spec/
│   ├── canonical-asset-spec.md        # Canonical Asset Specification
│   └── harness-adapter-spec.md        # Harness Adapter Specification
├── manifests/                         # declarative harness descriptors
│   ├── harness-antigravity.json       # Google Antigravity IDE contract
│   ├── harness-codex.json             # OpenAI Codex CLI / IDE contract
│   ├── harness-claude.json            # Anthropic Claude Code contract
│   └── harness-cursor.json            # Cursor IDE contract
├── adapters/
│   └── project-harness.py             # stdlib-only projection/dispatch script (zero dependencies)
└── tests/
    └── test_harness_adapter.py        # adapter unit and integration tests
```

---

## 🚀 Cross-agent support matrix

| Asset type | Neutral location | Antigravity target | OpenAI Codex target | Claude Code target |
| :--- | :--- | :--- | :--- | :--- |
| **Skill** | `<ASSETS>/skills/<name>` | `/workspace/.agents/skills/<name>` | `/workspace/.agents/skills/<name>` *(shared native support)* | `/root/.claude/skills/<name>` |
| **Rule** | `<ASSETS>/rules/<name>.md` | `/workspace/.agents/rules/<name>.md` | `/workspace/.codex/rules/<name>.rules` *(converted or projected)* | appended to `CLAUDE.md` |
| **Instruction** | `<ASSETS>/fragments/<id>.md` | `/workspace/.agents/AGENTS.md` | `/workspace/AGENTS.md` | `/workspace/CLAUDE.md` |
| **MCP Server** | `<ASSETS>/mcp/<id>.json` | `/workspace/.agents/mcp_config.json` | `/root/.codex/config.toml` (`[mcp_servers]`) | `~/.claude/mcp.json` |
| **Trust Config** | N/A (injected at run time) | automatic | `/root/.codex/config.toml` automatically trusts `/workspace` | automatic |

---

## 🛠️ How to use

### 1. Project-level declarative reference (`ext-default.json`)
In a project's `ext-default.json` you can reference the harnesses you need, all together or selectively:

```json
{
  "uuid": "...",
  "name": "proj-my-service",
  "enabled": true,
  "include_scopes": [
    "solution-container-develop-full",
    "harness-antigravity",
    "harness-codex"
  ]
}
```

### 2. Manifest rules: relative paths only

`manifests/harness-*.json` only records the harness conventions of each model (Claude, Codex, Antigravity, Cursor).
Paths may be written in only two forms:

- `~/<path>`: relative to the home directory
- `./<path>`: relative to the project directory

No physical environment information may appear (absolute paths, host names, accounts, `/root`, `/workspace`, `/opt`, ...).
A violation is rejected at load time (`manifest path must be '~/<path>' or './<path>'`), and the test
`test_manifests_carry_no_host_path` guards it separately. A Codex trust path in a manifest can only be `./`;
any extra physical path to trust is given at run time with `--trust-path`.

### 3. Running: home and project directory come from arguments

`--workspace` (project directory) and `--user-home` (home) are **required and have no defaults**:

```bash
# Plan only, write no files; hand over to extscope-dist to write (backup, manifest, rollback)
extscope-harness --harness claude-host --assets-root ASSETS \
  --workspace PROJECT_DIR --user-home HOME_DIR --emit-dist-plan > plan.json

# Apply directly (bypassing dist; no backup or rollback)
extscope-harness --harness codex --assets-root ASSETS --workspace PROJECT_DIR --user-home HOME_DIR \
  [--trust-path /abs/extra]
```

The package no longer ships `run-harness-*.sh` or `scopes/` with hard-coded host paths; an environment that
needs a fixed combination should put the arguments in its own configuration or scheduler.

The `ministep` entry point is exposed as a Codex Skill at
`skills/ministep/SKILL.md`; it delegates to the existing Return Harness
workflow source.  Codex's supported discovery boundary is Skills (use
`/skills` to inspect or `$ministep` to invoke).  A file under
`.agents/workflows/` alone is not a portable Desktop slash command, so the
host launcher deliberately does not invent a second workflow projection.

### 4. The neutral asset directory (`--assets-root`) is assembled by the consumer

The harness neither presets nor creates an asset directory. The simplest approach is to symlink to the sources inside your package tree (single source of truth; editing the repo takes effect immediately):

```bash
A=$(mktemp -d)                      # or a fixed location chosen by the consumer
mkdir -p "$A/skills" "$A/rules"
ln -s <packages-root>/<pkg>/skills/<name> "$A/skills/<name>"
ln -s <packages-root>/<pkg>/rules/<name>.md "$A/rules/<name>.md"
```

`--user-home` must be "the directory the agent actually reads": for `claude-host`, `~/skills`, `~/rules` and `~/mcp.json`
correspond to `CLAUDE_CONFIG_DIR` (for example a dedicated account directory), not the home of the login account.

### 5. Usage examples (usable directly as prompts)

> "Use extscope-harness to deploy the skills and rules of `<A>` to claude-host; the account directory is `<ACCT>`. Only show me the plan, do not write anything."

```bash
extscope-harness --harness claude-host --assets-root "$A" \
  --workspace /tmp/none --user-home "$ACCT" --emit-dist-plan > plan.json
extscope-dist --config dist.json plan plan.json          # dry run: shows the action for each target
```

> "Once the plan is confirmed, apply it, then verify, and tell me which repo and commit `<ACCT>/skills/<name>` came from."

```bash
extscope-dist --config dist.json apply plan.json --all   # dist.json needs allowed_roots (including $ACCT)
extscope-dist --config dist.json verify <manifest-id>
extscope-dist --config dist.json origin "$ACCT/skills/<name>"
```

> "List all distribution records, across all state dirs, whose destination is under `<ACCT>`."

```bash
extscope-dist inventory --scan <state-dirs-root> --target-prefix "$ACCT"
```

> "Roll back this distribution." → `extscope-dist --config dist.json rollback <manifest-id>`

Rules: every step requires user approval before apply; if `allowed_roots` is not set, everything is refused; skills use
symlink by default and fall back to copy only when the agent does not accept symlinks.
