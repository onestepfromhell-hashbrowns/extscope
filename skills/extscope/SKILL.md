---
name: extscope
description: >-
  ExtScope 套件組(extscope 核心 + dist / modules / harness / container / tools)的操作 SOP:撰寫與驗證
  *.extscope.json / ext-default.json、展開計畫並綁定來源(plan / bind / verify)、用 extscope-dist 把內容寫到主機
  (dry-run、--receipt、rollback、prune)、用 extscope-harness 決定各 agent 平台的放置位置、檢查 canonical 暫存區與層級依賴、
  以 install.sh 安裝。要交付 skill / MCP / 環境變數到主機或容器、組或切換 canonical release、排查 extscope 拒絕碼時使用。
  不處理容器生命週期(container-ctl)與 ssh 入口。
---

# ExtScope

**核心只讀與規劃,寫入由獨立套件做。** 各套件只用標準函式庫、互不 import,只透過 JSON(計畫、收據、manifest、設定)溝通。

| 命令 | 職責 | 能寫? |
|---|---|---|
| `extscope` | validate / digest / plan / bind / verify / dag / query / index / resolve / init / pkg | 否 |
| `extscope-dist` | 主機端分發:copy / symlink / md_block;dry-run、`--receipt`、範圍閘門、目標鎖、備份、回滾、prune | 是 |
| `extscope-modules` | dist 的可選模組:chmod / chown / key_inject / env_inject / config_merge / custom_hook / pkg_import(`modules.enable` 啟用) | 是(經 dist) |
| `extscope-harness` | 各 agent 平台「放什麼、放哪」的規範,只存 `~/`、`./` 相對路徑;`--emit-dist-plan` 產出 dist 計畫 | 否 |
| `extscope-container` | 容器傳輸:creation-plan / run / lease / refs / gc / purge / secret-link | 是 |
| `extscope-tools` | gate(密鑰與掛載掃描)/ anomaly(異常帳本)/ authority(層級依賴)/ canonical(暫存區檢查)/ assemble(以 base release 加來源組出新 release,**不切換 current**,只寫 `--out-root`) | assemble 寫新目錄,其餘否 |

## 一份宣告怎麼落地
`init` → 人寫並提交 → `validate` → `plan` → `bind`(收據 = 來源位元組指紋)→ `verify` → `extscope-dist plan` → `apply --receipt` → `verify` →(需要時)`rollback`。
`plan` 的雜湊只綁宣告,**不綁來源位元組**;不加 `--receipt`,核心的批准與實際寫入之間沒有綁定。

## 位置與暫存區(canonical)
- **宣告**(人寫、進 git):`<scopes-dir>/{system,biz,solution,preset}/`;專案實例 `<project-dir>/ext-default.json`。
- **canonical** = `/var/lib/extscope/canonical`,主機上唯一的中立交付庫與**暫存區**:多個來源組成完整複本 `releases/<id>/`,`current`、`skills` 兩個 symlink 指向現行 release。流程是「來源 → 組裝成 release → 檢查 → 切換 `current` → 投射到帳號/專案」,**來源更新不會直接 apply 到應用環境**。
- release 規則:存複本,不得有指向 release 外的 symlink(特大檔案例外,須在 `DELIVERY.json` 的 `referenced` 明列);應用端連結一律指向 `canonical/...`,不直接連來源或 `/home/<user>`;**不做增量儲存**,不進 git(完整性靠 `DELIVERY.json` 的檔案摘要)。
- 檢查:`extscope-tools canonical <release> [--links <投射目錄> --canonical-root /var/lib/extscope/canonical]`。換 release = 切 symlink,回滾 = 切回。
- 設定 `/etc/extscope`、dist 狀態 `state_dir` 由使用者提供;`install.sh --system|--user [--prefix]` **只裝程式**,不建立或管理 canonical、狀態、設定(README §1.2)。

## 層級
System → Biz(原子能力,`include_items`;慣例上不 `include_scopes`,工具未強制)→ Solution(純組合 `include_scopes`)→ Preset(角色樣板)→ Project(`ext-default.json`)。命名 `<layer>-<domain>-<feature>`,檔名 `.extscope.json`;範本一律 `*.template`。
`category` 也接受相容別名 tooling / custom。`authority` 預設層級表**不含 system、biz**,檢查五層要 `--manifest <檔案路徑>`(吃檔案,不吃 JSON 字串),檔內容 `{"layers":{"system":1,"biz":2,"solution":3,"preset":4,"project":5}}`;只給單一 scope 時引用的 scope 找不到會報 `unresolved_include`,要把被引用的 scope 檔一併列入。

## 禁止的反模式
1. **scope 檔做成 symlink**:核心以 `O_NOFOLLOW` 讀取,一律 `SYMLINK_REJECTED`。要發佈 scope:複製真檔進權威目錄並提交。
2. **搜尋路徑 / 先到先得**:必須明確提供範圍目錄或檔案清單,被 `include_scopes` 引用的 scope 必須都在其中(`scope reference is not in explicit catalog` = 缺檔)。
3. **跨過 FHS canonical 路徑直接耦合外部目錄**,或使用相對路徑作為 `src`。
4. **靜默降級**:失敗要大聲;`EXTSCOPE_DEBUG=1` 才印例外類別與訊息(預設刻意不透明)。
5. **憑證寫進 scope**:用 `*_FILE` 契約;`extscope-tools gate` 會擋明文。
6. **直接改下游副本而不回寫源頭**(見下節)。
7. **讓應用端直連來源**,或手改既有 release 目錄(要改就組新 release)。

## 部署副本不是源頭(反向同步)
dist 目標(`~/.claude`、專案目錄)、canonical release、`skills/` 部署目錄都是**產物**。直接改它們,下次分發會被覆蓋,也沒有版本紀錄。改完要回寫源頭:
```bash
extscope-dist --config dist.json origin <被改的路徑>        # 來源 src + git repo + 下一步
extscope where --scope-dir <scopes-dir> <src>   # 哪個 scope 宣告了它、被誰 include
extscope-dist --config dist.json diff|pullback <manifest>   # pullback 預設乾跑,--apply 才寫;需設定 source_roots
```
回寫後在源頭 repo 檢查 `git diff` 並提交,再 `bind` → `apply`。細節見 `extscope-dist/SKILL.md`。

## 常用操作
```bash
# 新增 scope(init 只印範本,不寫檔)
extscope init --category biz --description "..." biz-tools-foo > <scopes-dir>/biz/biz-tools-foo.extscope.json
extscope validate --scope <scopes-dir>/biz/biz-tools-foo.extscope.json     # --scope 必須是絕對路徑
<scopes-dir>/check.sh && git -C <scopes-dir> add -A && git -C <scopes-dir> commit

# 檢視(唯讀)
extscope dag|index|resolve|query --scope-dir <abs-dir> ...
extscope plan --scope <abs file> --entry <name|uuid> [--var NAME=VALUE] > plan.json         # {{NAME}} 沒給 --var 會失敗
extscope bind --scope <abs file> --entry <name|uuid> --allow-root <abs> > receipt.json

# 寫入主機(沒有 allowed_roots 一律拒絕)
extscope-dist --config dist.json plan  plan.json [--receipt receipt.json]                   # 乾跑,不鎖不寫
extscope-dist --config dist.json apply plan.json --all|--filter-prefix DIR [--receipt ...]  # 非託管檔需 --force(會備份)
extscope-dist --config dist.json verify|rollback <manifest>
extscope-dist --config dist.json prune [--older-than 天] [--include-applied] [--apply]      # 預設只列出

# 各 agent 平台的放置:harness 只規劃,home 與專案目錄由參數給(必填),再交給 dist 寫入
extscope-harness --harness claude-host --canonical-root /var/lib/extscope/canonical \
  --workspace <專案目錄> --user-home <home> --emit-dist-plan > plan.json
```
`claude-host` 的 manifest 是 `~/skills`、`~/mcp.json`(沒有 `.claude`),所以 `--user-home` 要給**最終的 `.claude` 目錄**才會落在 `<...>/.claude/skills`。投射前先用 `--emit-dist-plan` 看實際 target。
`dist.json` 範例:`{"allowed_roots":["<dir>"],"state_dir":"<dir>","remap":[...],"modules":{"enable":["extscope_modules"]}}`。分發**目標**可以是 symlink(`method: symlink`);**宣告**不可以。

## 容器與掃描
- `extscope-container creation-plan ...` 產生建立容器時要綁的 mount;`run` 把計畫套進運行中容器;憑證只走 `quadlet-secret` / `secret-link`(tmpfs 0600),不入 scope。
- 部署前:`extscope-tools gate <dir>`(違規 exit 1,不印值);層級:`extscope-tools authority [--manifest m.json] <scope>...`。

細節見套件 `README.md` 與各子套件 `README.md` / `SKILL.md`(`extscope-dist/SKILL.md` 含拒絕碼與已知行為)。
