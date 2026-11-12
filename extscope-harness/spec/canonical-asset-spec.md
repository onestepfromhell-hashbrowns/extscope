# Canonical Capability Asset Specification

## 1. Concept and positioning

By first principles, the business capability itself (skills, constraint rules, instruction fragments, MCP tools) is independent of any consuming agent.
To decouple completely, all business extension packages (Biz Scopes) deploy their assets into a neutral system staging directory:

```text
/run/ext/canonical/
├── skills/           # neutral skill directories (each subdirectory contains SKILL.md)
│   ├── container-workflow/
│   └── project-containerization-guide/
├── rules/            # neutral rule documents (*.md)
│   ├── dual-track-constraints.md
│   └── container-doctor.md
├── fragments/        # conversation/agent instruction fragments (*.md)
│   └── container-agents.md
└── mcp/              # MCP tool definitions (*.json)
    └── playwright-mcp.json
```

---

## 2. Asset type definitions

| Asset type | File structure contract | Core requirement |
| :--- | :--- | :--- |
| **`skill`** | A directory that must contain `SKILL.md` | `SKILL.md` contains YAML frontmatter (`name`, `description`) |
| **`rule`** | A single Markdown file (`*.md`) | Contains rule clauses; may include YAML frontmatter (`trigger`, `description`) |
| **`fragment`** | A single Markdown file (`*.md`) | An instruction fragment: plain text, or text containing HTML sentinel block tags |
| **`mcp`** | A single JSON file (`*.json`) | Describes the MCP start command and environment parameters |
| **`agent`** | A TOML or JSON role definition | Defines the sub-agent role, model and instruction contract |

---

## 3. Standard neutral pipeline example for a Biz Scope

A modern Biz Scope only needs to run `copy_inject` or `volume_mount` into the neutral directory:

```json
{
  "$schema": "urn:extscope:schema:extension-v1",
  "uuid": "8d72e61a-3e4b-4f91-a20c-5192837465ab",
  "name": "example-skills",
  "category": "biz",
  "include_items": [
    {
      "src": "/path/to/source/skills/container-workflow",
      "dst_fhs": "/usr/local/lib/ext-content/skills/container-workflow",
      "mode": "ro",
      "type": "copy_inject",
      "persist": false,
      "pipeline": [
        {
          "step": 1,
          "module": "copy_inject",
          "config": {
            "dst": "/run/ext/canonical/skills/container-workflow"
          }
        }
      ]
    }
  ]
}
```
> **Note**: a Biz Scope no longer contains a `symlink` or `md_block_inject` pointing at `/workspace/.agents/`; the path binding is handled uniformly by the Harness Adapter.
