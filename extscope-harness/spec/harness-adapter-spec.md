# Harness Adapter Specification (適配器規格)

## 1. 概念與責任

Harness Adapter 負責將 `/run/ext/canonical/` 中的中立資產，按照特定 Agent/IDE 的檔案契約（File Protocol & Discovery Hierarchy）動態投影（Project）至容器內部目標路徑。

適配器必須具備：
1. **宣告式契約 (`manifests/harness-<id>.json`)**：定義各資產類型的目標路徑、投影方式（`symlink`、`inject`、`convert`）。
2. **冪等性執行 (Idempotent Execution)**：重複執行不會破壞既有檔案，自動修復斷開的軟連結。
3. **零外部依賴 (Zero External Dependencies)**：在容器內僅依賴 Python 3 標準庫即可執行。

---

## 2. Manifest Schema 規範 (`harness-<id>.json`)

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

## 3. 執行流程 (Execution Lifecycle)

```text
1. 發現 (Discovery)   : 掃描 /run/ext/canonical/{skills,rules,fragments,mcp}
2. 匹配 (Match)       : 根據指定的 harness_id 載入對應 manifest
3. 投影 (Projection)  :
   - 建立目標目錄 (mkdir -p)
   - 建立軟連結 (symlink)
   - 區塊標籤追加 (sentinel block injection)
   - 配置合流 (merge toml / json)
4. 自檢 (Verification): 確認軟連結指向正常、無殘留斷鏈
```

---

## 4. 分層邊界（2026-10 修訂）

- **extscope（通用）**：只負責把資產整合成 canonical 樹 `canonical/{skills,rules,fragments,mcp}`，不認得任何 agent 路徑。
- **adapter（平台特化）**：只讀 canonical 樹，依 manifest 投影到各平台自己的 home。通用 `.agents/` 不作為投影目標。
- canonical 樹位置：容器 `/run/ext/canonical`；host `/var/lib/extscope/canonical`（ExtScope獨立套件受管發布state；不含站點專屬路徑）。

## 5. Manifest 路徑規範

manifest 只記錄該模型的規範，路徑一律是 `~/<path>`（home 起算）或 `./<path>`（專案目錄起算），
不得含絕對路徑或任何主機、帳號資訊；`trusted_paths` 只能是 `./`。實際 home 與專案目錄由執行參數
`--user-home`、`--workspace` 提供（必填），額外信任路徑用 `--trust-path`。違規的 manifest 在
preflight 被拒絕。以 `manifests/harness-*.json` 為準；host 版（`*-host`）的差異只是相對路徑佈局不同
（skills → `~/skills`、rules → `~/rules`、fragments → `~/AGENTS.md`、mcp → `~/config.toml`）。

## 6. MCP 投影規則

- mode `toml_mcp_merge`（Codex）：增量寫入 `config.toml` 的 `[mcp_servers."<name>"]` 區塊。
- mode `json_merge`（Claude、Antigravity）：合併進 JSON 的 `mcpServers` 物件。
- canonical MCP 資產值內的 `/workspace`、`/root` 字樣仍會被替換為 `--workspace`、`--user-home`（舊慣例，屬資產內容，不屬 manifest）。
- 同名 server：canonical 定義覆寫 home 內同名項（JSON 取代該 key；TOML 取代該 `[mcp_servers.<name>]` 區塊）。
- 非同名的既有 server 保留（讀碼確認：`_merge_json_mcp`、`_merge_toml_mcp`）。
- JSON讀取失敗、非object、mcpServers非object或不安全目標會拒絕並保留原檔；有效變更採同目錄temp、fsync、atomic replace與dir fsync。這是單檔原子性，尚非多目標交易或跨writer鎖。

## 7. 獨立插件與分發邊界

ExtScope core 只處理中立交付與規劃；`extscope-harness` 是獨立的平台適配層，由消費端顯式宣告與釘選，core 不隱式載入任何特定 agent 平台邏輯。系統 canonical 根目錄為 `/var/lib/extscope/canonical`，容器內 `/run/ext/canonical` 為唯讀掛載消費點。每個 release generation 在同一檔案系統內組裝發布，消費端釘選後掛載或透過 `extscope-dist` 投射落地。
