# Canonical Capability Asset Specification (中立能力資產規格)

## 1. 概念與定位

依據第一性原理，業務能力本體（技能、約束規則、指令片段、MCP 工具）獨立於任何消費端 Agent。
為了徹底解耦，所有業務擴充套件（Biz Scopes）將資產部署至系統中立中繼目錄：

```text
/run/ext/canonical/
├── skills/           # 中立技能目錄 (每個子目錄包含 SKILL.md)
│   ├── container-workflow/
│   └── project-containerization-guide/
├── rules/            # 中立規則文件 (*.md)
│   ├── dual-track-constraints.md
│   └── container-doctor.md
├── fragments/        # 對話/Agent 指令片段 (*.md)
│   └── container-agents.md
└── mcp/              # MCP 工具定義 (*.json)
    └── playwright-mcp.json
```

---

## 2. 資產型別定義 (Asset Types)

| 資產型別 (Type) | 檔案結構契約 | 核心要求 |
| :--- | :--- | :--- |
| **`skill`** | 目錄，必須包含 `SKILL.md` | `SKILL.md` 包含 YAML frontmatter (`name`, `description`) |
| **`rule`** | 單一 Markdown 檔案 (`*.md`) | 包含規則條款，可選包含 YAML frontmatter (`trigger`, `description`) |
| **`fragment`** | 單一 Markdown 檔案 (`*.md`) | 純文本或包含 HTML sentinel block 標籤的指令片段 |
| **`mcp`** | 單一 JSON 檔案 (`*.json`) | 描述 MCP 啟動指令與環境參數 |
| **`agent`** | TOML 或 JSON 角色定義 | 定義子代理角色、模型、指令契約 |

---

## 3. Biz Scope 的標準中立管線範例

現代化的 Biz Scope 只需執行 `copy_inject` 或 `volume_mount` 至中立目錄：

```json
{
  "$schema": "urn:extscope:schema:extension-v1",
  "uuid": "8d72e61a-3e4b-4f91-a20c-5192837465ab",
  "name": "biz-example-skills",
  "category": "biz",
  "include_items": [
    {
      "src": "/opt/example/.agents/skills/container-workflow",
      "dst_fhs": "/opt/ext/fhs/canonical/skills/container-workflow",
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
> **注意**：Biz Scope 不再包含指向 `/workspace/.agents/` 的 `symlink` 或 `md_block_inject`，路徑綁定轉移由 Harness Adapter 統一處理。
