# Agent Harness Adapter (ExtScope 跨 Agent 依賴解耦套件)

> **所屬分類**：`extscope/extscope-harness`（extscope 子套件）  
> **核心目標**：解決業務能力模組（Biz Scopes）與特定 Agent（Antigravity / Codex / Claude / Cursor 等）路徑契約與格式的強耦合問題。  
> **設計準則**：第一性原理、零核心程式碼入侵（Zero Core Intrusion）、宣告式外掛適配（Declarative Adapter）。

---

## 與 extscope-dist 的分工（寫入走 dist）

adapter 決定「放什麼、放哪」，extscope-dist 負責「寫」（備份、manifest、rollback）：

```bash
python3 adapters/project-harness.py --harness claude-host --canonical-root /var/lib/extscope/canonical \
  --workspace W --user-home H --emit-dist-plan > plan.json      # 不寫任何檔
extscope-dist --config dist.json plan  plan.json
extscope-dist --config dist.json apply plan.json --all
```
- skills/rules/fragments → `symlink` / `copy` / `md_block`；MCP 與 Codex 信任 → `config_merge`（extscope-modules，dist 設定需 `modules.enable: ["extscope_modules"]`）。
- sandbox 探針降級（`tiered`、`fallback_target_dir`、`extra_dirs_registration`）與其他 `runtime_bootstrap` 步驟仍由 adapter 自己的 apply 處理；`--emit-dist-plan` 遇到會列出並 exit 3（`--allow-residual` 才放行）。
- 不加 `--emit-dist-plan` 的直接套用路徑保留，但不享有 dist 的備份與回滾。
- `extscope/plugins/agent-harness-adapter` 已凍結（只能預覽、無執行端），不要再擴充。

---

## 🏛️ 第一性原理架構設計 (First-Principles Architecture)

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. 能力資產層 (Canonical Asset Layer - SSOT)                           │
│    業務模組只產出中立資產至 /run/ext/canonical/{skills,rules,mcp,fragments} │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. 外掛適配層 (External Harness Adapter Layer)                          │
│    獨立 Manifests (harness-*.json) 定義特定 Agent 的檔案路徑與格式契約   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. 投射分發器 (Project Harness Dispatcher - Python Standalone)          │
│    讀取中立資產與目標 Harness 契約，動態將資產投影至容器目標工作區      │
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

## 📦 目錄結構

```text
extscope-harness/
├── README.md                          # 本手冊
├── spec/
│   ├── canonical-asset-spec.md        # 中立資產規格 (Canonical Asset Specification)
│   └── harness-adapter-spec.md        # Harness 適配規格 (Harness Adapter Specification)
├── manifests/                         # 宣告式 Harness 描述檔
│   ├── harness-antigravity.json       # Google Antigravity IDE 規格契約
│   ├── harness-codex.json             # OpenAI Codex CLI / IDE 規格契約
│   ├── harness-claude.json            # Anthropic Claude Code 規格契約
│   └── harness-cursor.json            # Cursor IDE 規格契約
├── adapters/
│   └── project-harness.py             # 純標準庫投影分發核心腳本 (Zero Dependencies)
└── tests/
    └── test_harness_adapter.py        # 適配器單元與整合測試
```

---

## 🚀 跨 Agent 支援矩陣

| 能力類型 (Asset Type) | 中立存放路徑 | Antigravity 目標 | OpenAI Codex 目標 | Claude Code 目標 |
| :--- | :--- | :--- | :--- | :--- |
| **Skill** | `/run/ext/canonical/skills/<name>` | `/workspace/.agents/skills/<name>` | `/workspace/.agents/skills/<name>` *(共用原生支援)* | `/root/.claude/skills/<name>` |
| **Rule** | `/run/ext/canonical/rules/<name>.md` | `/workspace/.agents/rules/<name>.md` | `/workspace/.codex/rules/<name>.rules` *(自動轉換或投影)* | 附加至 `CLAUDE.md` |
| **Instruction** | `/run/ext/canonical/fragments/<id>.md` | `/workspace/.agents/AGENTS.md` | `/workspace/AGENTS.md` | `/workspace/CLAUDE.md` |
| **MCP Server** | `/run/ext/canonical/mcp/<id>.json` | `/workspace/.agents/mcp_config.json` | `/root/.codex/config.toml` (`[mcp_servers]`) | `~/.claude/mcp.json` |
| **Trust Config** | N/A (執行期注入) | 自動 | `/root/.codex/config.toml` 自動信任 `/workspace` | 自動 |

---

## 🛠️ 如何使用

### 1. 專案級聲明式引用 (`ext-default.json`)
在專案的 `ext-default.json` 中，可同時或選擇性引用所需的 Harness：

```json
{
  "uuid": "...",
  "name": "proj-my-service",
  "enabled": true,
  "include_scopes": [
    "solution-full-stack",
    "harness-antigravity",
    "harness-codex"
  ]
}
```

### 2. Manifest 規範：只收相對路徑

`manifests/harness-*.json` 只記錄各模型（Claude、Codex、Antigravity、Cursor）的 harness 規範，
路徑只有兩種寫法：

- `~/<path>`：相對於 home 目錄
- `./<path>`：相對於專案目錄

不得出現任何實體環境資訊（絕對路徑、主機名、帳號、`/root`、`/workspace`、`/opt`…）。
載入時違規即拒絕（`manifest path must be '~/<path>' or './<path>'`），測試
`test_manifests_carry_no_host_path` 另行把關。Codex 的信任路徑在 manifest 中只能是 `./`；
額外要信任的實體路徑在執行時用 `--trust-path`。

### 3. 執行：home 與專案目錄由參數給

`--workspace`（專案目錄）與 `--user-home`（home）**必填，沒有預設值**：

```bash
# 只規劃，不寫檔；交給 extscope-dist 寫入（備份、manifest、rollback）
extscope-harness --harness claude-host --canonical-root CANON \
  --workspace PROJECT_DIR --user-home HOME_DIR --emit-dist-plan > plan.json

# 直接套用（不經 dist，無備份與回滾）
extscope-harness --harness codex --canonical-root CANON --workspace PROJECT_DIR --user-home HOME_DIR \
  [--trust-path /abs/extra]
```

套件不附帶寫死主機路徑的啟動腳本；需要固定組合的環境，把參數寫在該環境自己的設定或排程裡。

Workflow 能力統一封裝為標準 `skills/<name>/SKILL.md` 進行投影；單獨存在的 `.agents/workflows/` 檔案並非跨平台通用的命令格式，因此分發器不另外建立第二條 workflow 專屬投影路徑。
