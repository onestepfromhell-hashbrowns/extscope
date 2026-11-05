# ExtScope

**English summary.** `extscope` is a standalone, dependency-free suite of Python packages for declarative extension and toolchain delivery. It declares which tools, skills, rules, and configuration fragments a container or host account should receive: the core (`extscope`) validates scope declarations, plans their expansion (DAG, content-tree digests) and verifies the sources, strictly read-only; `extscope-dist` applies a plan to host targets with backups and rollback; `extscope-container` is the container transport layer; `extscope-harness` decides what goes where for each agent platform; `extscope-tools` offers a secret/mount scanner, an anomaly ledger, a canonical release assembler, and a layer-authority check. Paths follow FHS and XDG and work on Linux, macOS and Windows.

- **Requirements:** Python 3.11+, standard library only.
- **Install:** `pip install .` (installs the `extscope` command), or `./install.sh --user` / `./install.sh --system` (see `./install.sh --help`); on Windows use `windows/install.ps1`. Sub-packages have their own `pyproject.toml`.
- **Run:** `extscope --help`; for example `extscope validate --scope /abs/path/my-tool.extscope.json`. A container image can be built with the included `Dockerfile` (`podman build -t extscope:latest .`).
- **Test:** `PYTHONPATH=src python3 -m pytest -q`
- **Packaging note:** build output (`build/`, `dist/`, `*.egg-info/`) is not tracked; rebuild with your packaging tool.

License: MIT, see `LICENSE`. The documentation below is mostly in Traditional Chinese.

---

# ExtScope: Portable Container Extension & Toolchain Delivery Core

> **Repository**: `extscope`  
> **Standard**: Filesystem Hierarchy Standard (FHS 3.0) & XDG Base Directory Specification  
> **Status**: Standalone Portable Core  
> **Platforms**: Linux (Container/Host), macOS, Windows  

---

## 1. 簡介 (Overview)

`extscope` 是一套獨立、中立、零外部相依的容器擴充（Container Extension）與工具鏈宣告式交付核心。
完全獨立的通用套件，不依賴任何特定主機路徑或 WSL2。

### 核心特性
- **FHS 與 XDG 嚴格相容**：
  - 系統模式：`/etc/extscope`, `/var/lib/extscope/canonical`, `/run/extscope`, `/var/cache/extscope`
  - 使用者模式：標準 `$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`, `$XDG_RUNTIME_DIR`
- **跨平台路徑抽象 (`platform_paths`)**：
  - 支援 Linux、macOS（Application Support & Caches）與 Windows（ProgramData & AppData）平滑轉譯。
- **唯讀規劃與來源驗證**：
  - 支援 Scope 依賴圖宣告解析、DAG 拓撲分析、SHA-256 內容樹指紋雜湊與 DirFD 安全讀取。
- **容器即時就緒**：
  - 提供獨立 `Dockerfile`，可作為微型工具容器直接部署於 Podman、Docker 與 Kubernetes。

---

## 1.1 套件分工

| 套件 | 職責 | 能寫目的地 |
| :-- | :-- | :-: |
| `extscope`（核心） | validate / plan / bind / verify / dag / query / index / resolve / where / init / pkg list·doctor·override-check | 否（唯讀） |
| `extscope-dist` | 主機端分發：copy / symlink / md_block、預演、範圍閘門、備份、回滾、prune、反向同步 | 是 |
| `extscope-modules` | dist 的可選模組：chmod / chown / key_inject / env_inject / config_merge / custom_hook / pkg_import | 是（經 dist） |
| `extscope-container` | 容器傳輸層：creation-plan / run / lease / refs / gc / purge / 憑證重注入 | 是 |
| `extscope-harness` | agent 特化業務層：決定各 agent 平台「放什麼、放哪」，`--emit-dist-plan` 產出 dist 計畫，不自己寫入 | 否（經 dist） |
| `extscope-tools` | 獨立工具：gate（密鑰與掛載掃描）/ anomaly（異常帳本）/ authority（層級依賴）/ canonical / assemble | 否（除 assemble 輸出新 release） |

各套件只用標準函式庫，互不 import；彼此只透過 JSON（計畫、manifest、設定檔）溝通。
所有路徑來自設定檔／參數／環境變數，程式內沒有預設的固定路徑。詳見各目錄的 `README.md`。

## 1.2 目錄位置與生命週期

| 領域 | 位置（系統模式 / 使用者模式） | 誰建立、誰寫 | 週期 | install.sh |
| :-- | :-- | :-- | :-- | :-: |
| 程式 | `/usr/local/{bin,lib/extscope*}` / `~/.local/{bin,lib}` | install.sh | 隨版本覆蓋；`--uninstall` 移除 | 處理 |
| 設定 | `/etc/extscope` / `$XDG_CONFIG_HOME/extscope` | 人 | 長期；解除安裝時保留 | 只建空目錄 |
| 宣告（scope） | 專案自己的 `.scopes/`（進 git） | 人 | 長期，版本控管 | 不處理 |
| 建置暫存 | 呼叫端自選的 `--prefix` 或工作目錄（例：`/tmp/extscope-stage`） | install.sh `--prefix` | 用完即丟 | 支援 `--prefix` |
| canonical 發布 | `/var/lib/extscope/canonical/releases/<id>/`，`current`、`skills` 指向現行 | `extscope-tools assemble`（輸出新 release；不切換） | 不可變世代；切換 symlink 即上線 | 只建空目錄，不組裝、不切換、不回收 |
| dist 狀態 | `state_dir`（manifests、backups） | extscope-dist | 每次 apply 一份；rollback 後改名 `.rolledback` | 不處理（需自行設定 `state_dir`） |
| 快取 | `/var/cache/extscope` / `$XDG_CACHE_HOME/extscope` | — | 可刪 | 只建空目錄，目前無程式使用 |
| 執行期 | `/run/extscope` | — | 重開機消失 | 不建立（tmpfs，由服務自行建立） |

注意：系統模式下 dist／container／tools 並不會自動讀 `/etc/extscope`，設定查找順序仍是
`--config` → `$EXTSCOPE_*_CONFIG` → 每用戶預設路徑；不內建自動狀態遷移或 release 回收排程。

## 1.3 宣告（`*.extscope.json`）的層級與子階段

**層級**（`category`；核心接受 `system, biz, solution, preset, project` 與相容別名 `tooling, custom, resource, res, tool`）：

| 層 | 檔案 | 組成方式 | 慣例 |
| :-- | :-- | :-- | :-- |
| System | `.scopes/system/` | 系統基礎設施 | 最底層 |
| Biz | `.scopes/biz/` | `include_items` 原子能力 | 慣例上不 `include_scopes`（未被強制） |
| Solution | `.scopes/solution/` | 純組合 `include_scopes` | |
| Preset | `.scopes/preset/` | 角色樣板 | |
| Project | `<project-dir>/ext-default.json` | 專案實例 | 最上層 |

依賴方向：高層可依賴低層，反向由 `extscope-tools authority` 檢查（`reverse_layer_dependency`）。
注意：`authority` 預設層級表只有 `tooling, custom, solution, preset, project`，**沒有 `system` 與 `biz`**，
這兩層預設不被判斷；要檢查五層需用 `--manifest` 提供 `{"layers":{"system":1,"biz":2,"solution":3,"preset":4,"project":5}}`。

**子階段**（一份宣告從寫成到落地）：

| # | 階段 | 命令 | 寫入？ |
| :-: | :-- | :-- | :-: |
| 1 | 起稿 | `extscope init` | 否（印出範本） |
| 2 | 撰寫、提交 | 人寫，進 `.scopes` git | 人 |
| 3 | 結構驗證 | `extscope validate --scope <絕對路徑>` | 否 |
| 4 | 檢視 | `index` / `resolve` / `dag` / `query` | 否 |
| 5 | 展開計畫 | `extscope plan` → `extscope.plan-preview.v1` | 否 |
| 6 | 綁定來源 | `extscope bind --allow-root` → 收據（來源位元組指紋） | 否 |
| 7 | 重驗 | `extscope verify --receipt` | 否 |
| 8 | 乾跑 | `extscope-dist plan [--receipt]` | 否 |
| 9 | 套用 | `extscope-dist apply [--receipt]` → manifest | 是 |
| 10 | 驗證漂移 | `extscope-dist verify <manifest>` | 否 |
| 11 | 回滾 | `extscope-dist rollback <manifest>` | 是 |
| — | 汰換 | 宣告內 `deprecated` / `deprecated_by`；`enabled:false` 停用 | 人 |

`plan` 的 `plan_sha256` 只綁宣告，不綁來源位元組；來源由階段 6、7 的收據負責，階段 8、9 加 `--receipt` 才會把它帶到寫入端。

## 1.4 canonical(暫存區)的定位與取捨(已定)

**用途**:統籌分發的暫存區,隔在「來源」與「應用環境」之間。來源(`/extscope` 的源頭)更新時,**不得直接
apply 到應用環境**;要經過重新組裝成新 release、檢查、再切換 `current` 才會生效。

```
來源 ──組裝(複製)──► canonical/releases/<id>/ ──投射(經 current)──► 帳號 home、專案目錄
```

- 暫存區存的是**複本**,不是指向來源的連結。release 內不得有指向 release 外的 symlink。
- **特大檔案例外**:可以直接引用來源,但必須在該 release 的 `DELIVERY.json` 以
  `"referenced": {"<release 內相對路徑>": "<來源路徑>"}` 明列;未列的外連視為違規。
- 應用端的連結一律指向 `canonical/...`(例:`/var/lib/extscope/canonical/skills/<名>`),不得直接指向來源。
- 檢查:`extscope-tools canonical <release> [--links <投射目錄>… --canonical-root <canonical>]`
  驗 `DELIVERY.json` 的檔案摘要(不符、未登錄、缺檔)、未宣告的外連(`escapes_release`)、應用端直連來源(`direct_to_source`)。
- canonical 不進 git(衍生物,可由來源與 `DELIVERY.json` 重建);宣告與來源進 git。完整性靠上述檢查,不靠版控。
- **不做增量儲存**:每個 release 是完整複本(目前約 700 個檔、11 MB)。曾評估硬連結與以項目為單位的物件庫,
  決定不採用;「小改動也產生新 release」是刻意接受的代價,應用端只會多或少有變動的那幾條連結。
- 不為此另蓋新套件;能用 dist 的 `copy`、`--receipt` 與上述檢查解決的不加機制。

## 1.5 分發安全保證摘要

| 機制 | 實作位置 | 行為說明 |
| :-- | :-- | :-- |
| 跨進程目標鎖 | `extscope_dist/locks.py` | `apply` 與 `rollback` 依路徑排序對每個目標父目錄取得排他 `flock`（逾時 30 秒回 `lock_timeout`），避免並行寫入遺失區塊 |
| 來源位元組綁定 | `--receipt`、`conflict`、`source_digest_mismatch` | 套用前核對收據與來源雜湊，未受管目標預設拒絕覆寫 |
| 路徑逃逸防護 | `check_target` | 詞法路徑與父目錄真實路徑雙重檢查，確保目標在 `allowed_roots` 內且不在 `denied_roots` 內 |
| 原子替換與回滾 | `state_dir/backups/`、`manifests/` | 同目錄暫存後 `os.replace`；中途失敗自動還原當次已套用項目（`apply_failed_rolled_back`） |
| 狀態清理 | `extscope-dist prune` | 預設唯讀列出過期已回滾與孤兒備份，加 `--apply` 才實際移除；各目標最新 manifest 永久保留 |
| 容器重啟偵測 | `extscope-container lease` | 比對 tmpfs 與磁碟 epoch token，偵測容器重建或重開機並自癒 |

## 2. 快速開始 (Quick Start)

### 安裝
```bash
pip install .
```

### 命令列用法
```bash
# 查看幫助
extscope --help

# 驗證 Scope 宣告檔（只做結構驗證）
extscope validate --scope /abs/path/my-tool.extscope.json

# 預覽依賴展開計畫（唯讀）；--entry 為宣告內的入口，詳見 extscope plan --help
extscope plan --scope /abs/path/my-blueprint.extscope.json --entry <entry>

# 計算內容樹雜湊指紋
extscope digest /path/to/payload
```

### 容器運行
```bash
# 建置容器映像檔
podman build -t extscope:latest .

# 運行容器驗證
podman run --rm extscope:latest --help
```
