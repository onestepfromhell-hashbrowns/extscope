---
name: agent-harness-adapter
description: >-
  跨 Agent 平台分發 .agents/ 資產（skill / rule / workflow / fragment）時的目的地規則：Antigravity、Codex、Claude Code
  各自的目標路徑、投影模式與 project-harness.py 用法。當使用 /extscope 分發 .agents/ 底下的 rule、skill 或 workflow，
  需決定 dst 要落到哪些 agent 平台（.agents、.claude、.codex、~/.claude/skills、CLAUDE.md、AGENTS.md），
  或撰寫 *.extscope.json 的 symlink target、執行 extscope-harness、診斷 skill 在某平台看不到時使用。
---

# Agent Harness Adapter：多 dst 平台分發規則

搭配 `/extscope`：extscope 決定「搬什麼」，本套件決定「各平台落在哪、什麼格式」。
套件根：`extscope/extscope-harness`（extscope 子套件）。

## 1. 各平台 dst 速查（source 一律是中立資產）

| 資產 | Antigravity | Codex | Claude Code |
| :-- | :-- | :-- | :-- |
| skill（目錄含 SKILL.md） | `<ws>/.agents/skills/<n>` | `<ws>/.agents/skills/<n>`（與 AGY 共用） | `~/.claude/skills/<n>` 或 `<ws>/.claude/skills/<n>` |
| rule（`*.md`） | `<ws>/.agents/rules/<n>.md` | `<ws>/.codex/rules/`（symlink_children） | 追加進 `<ws>/CLAUDE.md`（sentinel 區塊） |
| fragment（`*.md`） | `<ws>/.agents/AGENTS.md` 區塊 | `<ws>/AGENTS.md` 區塊 | `<ws>/CLAUDE.md` 區塊 |
| workflow | 無獨立通道 | **不投影**；以 Skill 暴露（`$name`），`.agents/workflows/` 單獨存在不是可攜指令 | 同左，包成 skill |
| cursor | 僅 rule/fragment → `<ws>/.cursorrules` | | |

要點：
- **Claude Code 不讀 `.agents/skills`**。只分發到 `.agents/` ＝ Claude 看不到；要多加 `.claude/skills/` 目標。
  （現有 biz scope 慣例：`copy_inject` 到 `/run/ext/skills/<n>`，再 `symlink` 到 `/workspace/.agents/skills/<n>` 與 `/workspace/.claude/skills/<n>`。）
- Codex 與 Antigravity 共用 `.agents/skills`，一份 symlink 兩家都有。
- workflow 別發明第二條投影；包成 SKILL.md。
- manifest 只用 `~/…`（home）與 `./…`（專案目錄）；dispatcher 以 `--user-home`／`--workspace`（必填）展開。

## 2. 兩條路徑

**執行（容器與宿主同一支）**：manifest 只有相對路徑，home 與專案目錄由參數給，兩者必填。
```bash
extscope-harness --harness claude-host --canonical-root <canonical> --workspace <專案目錄> --user-home <home> --emit-dist-plan > plan.json
extscope-dist --config dist.json apply plan.json --all      # 寫入走 dist(備份/回滾)
```
無 `--emit-dist-plan` 時直接套用（無備份）；Codex 額外信任路徑用 `--trust-path <abs>`。
套件不附帶寫死路徑的 launcher，環境固定值放在該環境自己的設定。

## 3. 決策流程（用 /extscope 分發前）

1. 資產型別 → 查 §1 決定哪些平台要落地。
2. 要 Claude 可見？ → 一定要有 `.claude/skills/` 或 `harness-claude`。
3. 新增 scope 只宣告中立 `copy_inject`；除非是單機特例（如 host-claude 專用 scope），不要在 biz scope 硬寫多平台 symlink，改引用 `solution-multi-agent-harness`。
4. `extscope validate` → `dag` → 跑 `extscope-harness --emit-dist-plan` + `extscope-dist apply` → 驗證目標存在（`ls -l` 目標、SKILL.md 可讀）。

## 4. 已知陷阱（讀碼確認）

- **MCP 支援 (json_merge / toml_mcp_merge)**：已實作於 `project-harness.py` 的 `_project_mcp`。Antigravity 與 Claude 會合併進 `mcpServers` JSON，Codex 則增量合流至 `config.toml` 的 `[mcp_servers."<name>"]` 區塊，並自動展開 `/workspace` 與 `/root` 佔位符。
- **Required preflight**：空/未知harness、缺canonical或required資產會非零退出；多harness先驗所有輸入與既有MCP設定，再投影。JSON壞檔或非object拒絕並保留原檔。
- **`tiered`（探針＋實體降級）預設關**：目前沒有 manifest 設 `"tiered": true`；Codex 沙箱擋越界 symlink 時需自行開啟，探針失敗回 exit 2（fail-closed，stderr 為 JSON）。
- **既存非 symlink 目標不覆蓋**（只警告略過），舊的實體目錄會讓新版 skill 不生效。
- rules 在 Claude 是追加進 CLAUDE.md 的 sentinel 區塊（`<!-- block: rule-<stem> -->`），重跑為冪等取代，勿手改區塊內文。
- host 的 canonical 預設 `/var/lib/extscope/canonical`（ExtScope 的受管發布 state）。目錄由受權publisher建立；未發布時launcher明確拒絕，不能以零資產算成功。可設 `AGENT_HARNESS_CANONICAL_ROOT` 指向已驗證版本。不使用 `~/.agents`。

## 5. 規格出處

`spec/canonical-asset-spec.md`（資產型別）、`spec/harness-adapter-spec.md`（manifest schema、生命週期）、
Tier 1/2 分發設計見 `extscope-harness/README.md`。改 dispatcher 後跑 `python3 -m pytest tests -q`。

## 6. 獨立插件與責任邊界

本套件是 ExtScope 的可選 agent harness 適配子套件；消費端按需顯式呼叫或透過 `--emit-dist-plan` 產出分發計畫交由 `extscope-dist` 落地。能力來源目錄不等於投影目的地；不得直接刪除或覆寫來源資產。
