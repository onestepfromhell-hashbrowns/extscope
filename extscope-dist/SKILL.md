---
name: extscope-dist
description: >-
  extscope-dist(主機端分發引擎)的操作 SOP:把 plan 的 copy / symlink / md_block(及已啟用模組)寫進主機目標,
  含 dry-run、範圍閘門、備份、manifest、verify、rollback。在要 apply 一份分發計畫、排查 apply/rollback 拒絕碼、
  或撰寫 dist.json 設定(allowed_roots / remap / state_dir)時使用。
  宣告與規劃請用 extscope 套件組 skill;容器傳輸用 extscope-container。
---

# extscope-dist

唯一會**寫主機檔案**的 extscope 元件(僅標準函式庫,不 import extscope)。核心決定「送什麼」,dist 只負責「寫」,兩者只透過 JSON 計畫交會。套件組總覽見 `../skills/extscope/SKILL.md`,細節見 `README.md`。

## 最小流程
```bash
# 全域旗標(--config / --state-dir / --var)必須寫在子命令「之前」,放後面會 unrecognized arguments
extscope-dist --config dist.json plan  plan.json                 # 乾跑,不寫
extscope-dist --config dist.json apply plan.json --filter-prefix /ws/x   # 或 --all
extscope-dist --config dist.json verify   <manifest-id|path>
extscope-dist --config dist.json rollback <manifest-id|path>
```
`dist.json`:`{"allowed_roots":[...], "denied_roots":[...], "remap":[{"from":..,"to":..}], "variables":{..}, "state_dir":.., "modules":{"enable":[..],"policy":{..}}}`。只允許這些欄位,多一個欄位即 `bad_config`。
設定查找:`--config` → `$EXTSCOPE_DIST_CONFIG` → 每用戶預設;**明確指定但不存在**是 `config_not_found`(不會靜默退回預設)。state_dir:`--state-dir` → `$EXTSCOPE_DIST_STATE_DIR` → config → 每用戶預設。`--var` 覆蓋 config `variables`。

## 與核心的授權銜接(--receipt)
核心的 `extscope bind` 收據記錄「被批准的來源位元組」。`plan` / `apply` 加 `--receipt 收據.json`:每個 copy / md_block 來源都必須在收據內且位元組未變(檔案 sha256 或 `extscope.tree.v1`),否則在寫入前拒絕(`source_not_bound` / `receipt_source_mismatch`;preview 計畫另驗 `receipt_plan_mismatch`;含 symlink 的綁定目錄 `receipt_tree_has_symlink`)。**不給 `--receipt` 時,核心的批准與實際寫入之間沒有任何綁定**。bind 時 `--allow-root` 要涵蓋來源目錄。

## 規則(違反就被拒,exit 2,stderr 為 `{"error":code,"detail":..}`)
1. **fail-closed**:沒有 `allowed_roots` 一律 `no_allowed_roots`。目標必須在 allowed 內、denied 外;檢查詞法路徑與父目錄真實路徑(symlink 逃逸 → `target_outside_allowed_roots` / `target_denied`)。
2. `apply` 必須給 `--filter-prefix`(可重複)或 `--all`,否則 `scope_required`。兩者同給時 `--all` 勝出,prefix 被忽略。
3. 所有路徑必須是絕對路徑且不含 `..`;`${NAME}` 未定義 → `unknown_variable`。
4. 目標已存在且**不是 dist 寫的** → `conflict`;`--force` 會先備份再取代。模組拒絕(`module_refused`)**不能**被 `--force` 覆蓋。
5. `remap` 只作用於**目標**(最長前綴);`src` 永不 remap。symlink 的連結內容就是 `src`,所以 `src` 要寫對最終位置。
6. 同一 (method, target, block_id) 重複 → `duplicate_target`。未啟用的模組方法 → `module_not_enabled`(不會略過)。

## 反向同步(目標被改過,要回寫源頭)
- 部署目標是產物,不是源頭。有人直接改了目標時,**不要 rollback**(會丟掉修改),照這個順序:
  `origin <目標路徑>` 找到來源與 git repo → `diff <manifest>` 看差異 → `pullback <manifest>` 乾跑 → 加 `--apply` 寫回來源 → 人看 `git diff`、提交 → 重新 `bind` + `apply`。
- `pullback` 只處理 `copy` 與 `md_block`;`symlink` 與模組項列為 `not_reversible`。寫入範圍受設定 `source_roots` 限制(fail-closed:未設定 `no_source_roots`,範圍外 `source_outside_source_roots`)。**不要把 canonical release 放進 `source_roots`**,回寫要寫到更上游的 repo。
- 來源在 apply 之後也被改過 → 該項 `source_changed`/`both_changed`,拒絕覆蓋,`ok:false`(exit 4)。目標新增的檔案會加回來源、修改的會更新;**目標刪除的檔案只列在 `left_alone`,不會從來源刪除**。
- `origin` 回傳 `repo.root/branch/commit/dirty`,並附下一步:`extscope where --scope-dir <宣告目錄> <src>` 反查宣告這個來源的 scope。
- 這三個命令都不提交 git。

## 並行與清理
- `apply`/`rollback` 對目標父目錄取 `flock`;取不到 30 秒後 `lock_timeout`(不是衝突,稍後重試)。`plan` 不鎖。
- `extscope-dist prune [--older-than 天] [--include-applied] [--apply]` 清 `state_dir`:預設只列出;`--apply` 才刪;預設只含已回滾與孤兒備份,`--include-applied` 才含被新紀錄完全取代的舊 manifest(會失去「還原到第一次 apply 之前」的備份)。每個目標最新的 manifest 永不刪。

## 退出碼
0 成功;2 拒絕(上列錯誤碼);4 僅 `verify` 發現漂移。`rollback_incomplete` 是 2,不是 4。

## 已實測、容易踩的行為(對抗式審查結果)
- **`plan` 與 `apply` 範圍一致**:`plan --filter-prefix DIR` 只列範圍內項目;不給 prefix 則列全部。
- **`sha256` 對 copy / symlink / md_block 都會驗證來源**(檔案摘要或 `tree:` 摘要),不符 → `source_digest_mismatch`。
- **md_block 的 rollback 有漂移檢查**:apply 後該檔被改過 → `rollback_incomplete`(`--force` 才整檔還原成 apply 前備份,並丟掉後來的修改)。對同一檔多次 apply 時,須由新到舊逐份 rollback。`verify` 對 md_block 只檢查區塊是否存在,不比對內容。
- **rollback 成功後 manifest 改名為 `*.rolledback.json`**:同一個 id 再 rollback / verify 會 `manifest_not_found`,不是冪等。
- **manifest 只記本次實際寫入的項目**:noop(內容已相同)的目標不在該次 manifest 裡,拿它 verify / rollback 管不到那些目標;重跑未變更的計畫 `applied: 0`(仍會寫一份空 entries 的 manifest)。
- copy 目錄時**會跟隨來源樹內的 symlink**(複製成實體);digest 對 symlink 取連結文字。
- 部分失敗會自動復原已套用項目並回 `apply_failed_rolled_back`;非 `DistError` 的例外也被包成這個碼。
- rollback 的 copy/symlink 項若目標在 apply 後被改過 → 整體停止並回 `rollback_incomplete`(`--force` 覆蓋)。不可逆模組項(hook)只列在 `not_undoable`,不會被還原。

## 計畫輸入
- `extscope-dist.plan.v1`(原生):`method` = copy / symlink / md_block(需 `block_id`),可選 `sha256`;copy/symlink/md_block 項不得帶 `config`。模組項只能有 `method` + `config`。
- `extscope.plan-preview.v1`(`extscope plan` 輸出,當純資料讀):`copy_inject`/`symlink`/`md_block_inject` 會套用,`volume_mount` 記入 `skipped`(容器專用),`enabled:false` 的項目被略過,其餘模組步驟當模組項,未啟用就拒絕。

## 與其他元件的邊界
- 要改宣告、validate、bind、查 DAG → `extscope`(唯讀)。
- 要建容器掛載、憑證注入 → `extscope-container`。
- 掃明文密鑰、檢查層級依賴 → `extscope-tools`。
- chmod / chown / key_inject / env_inject / config_merge / custom_hook / pkg_import → `extscope-modules`,須在 `modules.enable` 啟用並在 `modules.policy.<method>` 給政策;缺政策會拒絕。

## 上游:agent-harness-adapter
agent 專屬的「放什麼、放哪」由 `../extscope-harness`（extscope 子套件）的 `adapters/project-harness.py --emit-dist-plan` 產出 `extscope-dist.plan.v1`,dist 只負責寫。計畫含 `config_merge` 項(MCP / 信任設定)時,dist 設定須有 `modules.enable: ["extscope_modules"]`,否則 `module_not_enabled`。

## 測試
`python3 -m pytest -q extscope-dist/tests`(含守門測試:原始碼不得 import extscope、不得含站點專屬名稱或固定 `/opt` `/srv` `/var/lib` 路徑,所以 SKILL/README 的範例路徑不要寫進 `src/`)。
