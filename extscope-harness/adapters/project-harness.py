#!/usr/bin/env python3
"""Project Harness Dispatcher — 容器內通用 Agent 投影適配分發器。

本腳本依據第一性原理設計：
- 僅使用 Python 3 標準庫（Zero External Dependencies）。
- 讀取 /run/ext/canonical/ 下的中立資產，並根據目標 Harness 描述檔動態投影。
- 完全冪等，支援重複執行與修復斷鏈。
"""

import argparse
import filecmp
import json
import os
import re
import shutil
import stat
import tempfile
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox_probe import ProbeReason, ProbeResult, probe_sandbox_access  # noqa: E402


class Tier(str, Enum):
    """階梯式分發實際採用的層級標記，取代未經驗證的字串常數。"""

    TIER1_SYMLINK = "tier1_symlink"
    TIER2_MATERIALIZED = "tier2_materialized"


def expand_path(template: str, workspace: Path, user_home: Path) -> Path:
    """Manifest paths are relative to the home dir (`~/x`) or the project dir (`./x`) and nothing else.

    The two roots come from --user-home / --workspace at run time; a manifest never names a host path.
    """
    if not isinstance(template, str):
        raise ValueError("manifest path must be a string")
    for token, root in (("~", user_home), (".", workspace)):
        if template == token or template == token + "/":
            return Path(root)
        if template.startswith(token + "/"):
            rel = template[2:]
            if ".." in rel.split("/") or rel.startswith("/"):
                break
            return Path(root) / rel
    raise ValueError(f"manifest path must be '~/<path>' or './<path>', got {template!r}")


def log(msg: str, verbose: bool = False, is_verbose_msg: bool = False) -> None:
    if is_verbose_msg and not verbose:
        return
    print(f"[Harness-Dispatcher] {msg}", file=sys.stderr)


def ensure_symlink(src: Path, dst: Path, verbose: bool = False) -> bool:
    """確保 dst 指向 src 的軟連結存在且有效。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        try:
            current_target = dst.resolve()
            if current_target == src.resolve() or str(os.readlink(dst)) == str(src):
                return False  # 已存在且正確
        except Exception:
            pass
        dst.unlink()
    elif dst.exists():
        log(f"警告: 目標路徑 {dst} 已存在且非軟連結，略過替換以防破壞資料", verbose)
        return False

    os.symlink(str(src), str(dst))
    log(f"已建立軟連結: {dst} -> {src}", verbose, is_verbose_msg=True)
    return True


def _normalize_permissions(root: Path) -> None:
    """確保實體解引用後的目錄/檔案至少具備 0755/0644，供低特權沙箱進程讀取。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dir_path = Path(dirpath)
        try:
            current = stat.S_IMODE(dir_path.stat().st_mode)
            dir_path.chmod(current | 0o755)
        except OSError:
            pass
        for name in filenames:
            file_path = dir_path / name
            try:
                current = stat.S_IMODE(file_path.stat().st_mode)
                file_path.chmod(current | 0o644)
            except OSError:
                pass


def _trees_equal(a: Path, b: Path) -> bool:
    """遞迴比較兩個目錄的內容是否等價（供冪等性判斷，不需寫入即視為等價）。"""
    if not a.exists() or not b.exists():
        return False
    comparison = filecmp.dircmp(a, b)
    if comparison.left_only or comparison.right_only or comparison.diff_files or comparison.funny_files:
        return False
    for sub in comparison.common_dirs:
        if not _trees_equal(a / sub, b / sub):
            return False
    return True


def ensure_materialized_copy(src: Path, dst: Path, verbose: bool = False) -> bool:
    """C-01: 實體解引用拷貝 (Materialized Copy)。

    以 `shutil.copytree(symlinks=False)` 將 src 完整解開所有巢狀軟連結後落地至
    dst，使目標對沙箱進程而言是 100% 純實體檔案。具冪等性：內容相同則不重複寫入。
    """
    if dst.is_symlink():
        dst.unlink()
    elif dst.exists() and not dst.is_dir():
        log(f"警告: 目標路徑 {dst} 已存在且非目錄，略過實體解引用以防破壞資料", verbose)
        return False

    if dst.exists() and _trees_equal(src, dst):
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)
    _normalize_permissions(dst)
    log(f"已實體解引用拷貝: {dst} <- {src}", verbose, is_verbose_msg=True)
    return True


def dispatch_tiered(
    src: Path,
    dst: Path,
    allowed_prefixes: list[str],
    verbose: bool = False,
    fallback_target_dir: Path | None = None,
) -> Tier:
    """C-02: 階梯式分發調度器。

    Tier 1（通用預設）：優先以軟連結分發於 `dst`（專案工作區 `.agents/`）。
    探針：呼叫 sandbox_probe 驗證目標是否可被沙箱進程實質讀取。`allowed_prefixes`
    必須僅包含沙箱進程實際可見的路徑（通常就是 workspace 本身），不可納入
    canonical_root——canonical 資產本就位於工作區以外，才是這個探針該偵測的
    對象；若誤將其列入允許前綴，會讓每一個合法但越界的 Tier 1 軟連結都被判定
    為「可視」，使 Tier 2 降級永遠無法觸發 (詳見 spec TRUTH-2)。
    Tier 2（特化分發）：探針受阻時降級為實體解引用拷貝：
      - 方案 1（預設）：原地覆蓋 `dst`，路徑維持不變。
      - 方案 2：若 manifest 提供 `fallback_target_dir`（如 `~/.codex/skills`），
        則額外於該專屬目錄複製一份，供該類 Agent 專用路徑讀取。
    """
    ensure_symlink(src, dst, verbose)
    probe = probe_sandbox_access(dst, allowed_prefixes)
    if probe.ok:
        return Tier.TIER1_SYMLINK

    log(f"探針回報受阻 ({probe.reason})，降級為實體解引用: {dst}", verbose)
    ensure_materialized_copy(src, dst, verbose)

    if fallback_target_dir is not None:
        fallback_dst = fallback_target_dir / src.name
        ensure_materialized_copy(src, fallback_dst, verbose)

    return Tier.TIER2_MATERIALIZED


def inject_md_block(dst_file: Path, block_id: str, content: str, verbose: bool = False) -> bool:
    """冪等地向 markdown 檔案注入帶有 sentinel comment 的區塊。"""
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    open_tag = f"<!-- block: {block_id} -->"
    close_tag = f"<!-- /block: {block_id} -->"
    block_text = f"{open_tag}\n{content.strip()}\n{close_tag}\n"

    existing = ""
    if dst_file.exists():
        existing = dst_file.read_text(encoding="utf-8")

    pattern = re.compile(rf"{re.escape(open_tag)}.*?{re.escape(close_tag)}\n?", re.DOTALL)
    if pattern.search(existing):
        new_content = pattern.sub(block_text, existing)
    else:
        separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
        new_content = existing.rstrip("\n") + ("\n" if existing else "") + separator + block_text

    if new_content != existing:
        dst_file.write_text(new_content, encoding="utf-8")
        log(f"已更新 Markdown 區塊 [{block_id}] 於 {dst_file}", verbose, is_verbose_msg=True)
        return True
    return False


def _toml_key_escape(key: str) -> str:
    """跳脫 TOML 鍵名稱，防止換行、引號或特殊字元注入新 section。"""
    return json.dumps(key, ensure_ascii=False)


def ensure_codex_trust(config_file: Path, trusted_paths: list[str], verbose: bool = False) -> None:
    """確保 Codex config.toml 包含指定路徑的 trust_level = 'trusted'。"""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    content = config_file.read_text(encoding="utf-8") if config_file.exists() else ""

    modified = False
    for path in trusted_paths:
        section = f'[projects.{_toml_key_escape(path)}]'
        if section not in content:
            block = f'\n{section}\ntrust_level = "trusted"\n'
            content += block
            modified = True
            log(f"已向 {config_file} 新增 Codex 信任路徑: {path}", verbose)

    if modified:
        config_file.write_text(content, encoding="utf-8")


def ensure_extra_dirs_registration(config_file: Path, extra_dirs: list[str], verbose: bool = False) -> bool:
    """C-03: 設定檔主動登記 (Scheme 3)。

    針對支援額外掃描路徑之 Agent，於其設定檔頂層增量注入 `extra_dirs`
    陣列，絕不破壞既有鍵值；寫入前以 `tomllib`（唯讀解析）驗證合成後內容仍為
    合法 TOML，驗證失敗即放棄寫入並回報錯誤，避免損壞使用者設定檔。
    """
    import tomllib

    config_file.parent.mkdir(parents=True, exist_ok=True)
    content = config_file.read_text(encoding="utf-8") if config_file.exists() else ""

    existing_dirs: list[str] = []
    pattern = re.compile(r'^extra_dirs\s*=\s*\[(.*?)\]', re.MULTILINE | re.DOTALL)
    match = pattern.search(content)
    if match:
        existing_dirs = re.findall(r'"([^"]*)"', match.group(1))

    missing = [d for d in extra_dirs if d not in existing_dirs]
    if not missing:
        return False

    merged = existing_dirs + missing
    array_literal = "[" + ", ".join(f'"{d}"' for d in merged) + "]"
    line = f"extra_dirs = {array_literal}\n"

    if match:
        new_content = pattern.sub(line.rstrip("\n"), content, count=1)
    else:
        # 頂層鍵必須出現在第一個 section header 之前，否則會被 TOML 解析為屬於該 section。
        first_section = re.search(r"(?m)^\[", content)
        if first_section is None:
            new_content = content.rstrip("\n") + ("\n" if content else "") + line
        else:
            insert_at = first_section.start()
            new_content = content[:insert_at] + line + content[insert_at:]

    try:
        tomllib.loads(new_content)
    except tomllib.TOMLDecodeError as e:
        log(f"錯誤: extra_dirs 登記將產生無效 TOML，已放棄寫入 {config_file}: {e}", verbose)
        return False

    config_file.write_text(new_content, encoding="utf-8")
    log(f"已向 {config_file} 登記 extra_dirs: {missing}", verbose)
    return True


def _project_skills(
    cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, tiered: bool, verbose: bool
) -> None:
    mode = cfg.get("mode", "symlink_children")
    target_dir = expand_path(cfg["target_dir"], workspace, user_home)
    target_dir.mkdir(parents=True, exist_ok=True)

    fallback_target_dir = None
    if cfg.get("fallback_target_dir"):
        fallback_target_dir = expand_path(cfg["fallback_target_dir"], workspace, user_home)
        fallback_target_dir.mkdir(parents=True, exist_ok=True)

    extra_dirs_cfg = cfg.get("extra_dirs_registration")

    canonical_skills = canonical_root / "skills"
    if not canonical_skills.exists():
        return

    for skill_path in canonical_skills.iterdir():
        if not skill_path.is_dir():
            continue
        dst_skill = target_dir / skill_path.name
        if mode == "materialized_copy":
            # Manifest 明確指定，直接落地實體解引用 (跳過 Tier 1/探針)。
            ensure_materialized_copy(skill_path, dst_skill, verbose)
        elif tiered:
            # allowed_prefixes 僅含 workspace：canonical_root 本就位於工作區以外，
            # 是探針該偵測的越界對象，不可預先允許 (見 dispatch_tiered docstring)。
            tier = dispatch_tiered(skill_path, dst_skill, [str(workspace)], verbose, fallback_target_dir)
            if tier == Tier.TIER2_MATERIALIZED and extra_dirs_cfg:
                config_file = expand_path(extra_dirs_cfg["config_file"], workspace, user_home)
                ensure_extra_dirs_registration(config_file, [str(target_dir)], verbose)
        else:
            # 既有預設路徑，100% 維持原有被動式符號分發行為。
            ensure_symlink(skill_path, dst_skill, verbose)


def _project_rules(cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, verbose: bool) -> None:
    mode = cfg.get("mode", "symlink_children")
    canonical_rules = canonical_root / "rules"
    if not canonical_rules.exists():
        return

    if mode == "symlink_children":
        target_dir = expand_path(cfg["target_dir"], workspace, user_home)
        target_dir.mkdir(parents=True, exist_ok=True)
        for rule_path in canonical_rules.iterdir():
            if rule_path.is_file():
                ensure_symlink(rule_path, target_dir / rule_path.name, verbose)

    elif mode == "md_block_append":
        target_file = expand_path(cfg["target_file"], workspace, user_home)
        for rule_path in canonical_rules.iterdir():
            if rule_path.is_file():
                content = rule_path.read_text(encoding="utf-8")
                inject_md_block(target_file, f"rule-{rule_path.stem}", content, verbose)


def _project_fragments(cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, verbose: bool) -> None:
    target_file = expand_path(cfg["target_file"], workspace, user_home)
    canonical_fragments = canonical_root / "fragments"
    if not canonical_fragments.exists():
        return

    for frag_path in canonical_fragments.iterdir():
        if frag_path.is_file():
            content = frag_path.read_text(encoding="utf-8")
            inject_md_block(target_file, frag_path.stem, content, verbose)


def _expand_mcp_val(val, workspace: Path, user_home: Path):
    """遞迴展開 MCP 欄位中的 /workspace 與 /root 佔位符。"""
    if isinstance(val, str):
        return val.replace("/workspace", str(workspace)).replace("/root", str(user_home))
    elif isinstance(val, list):
        return [_expand_mcp_val(x, workspace, user_home) for x in val]
    elif isinstance(val, dict):
        return {k: _expand_mcp_val(v, workspace, user_home) for k, v in val.items()}
    return val


def _load_canonical_mcp_servers(canonical_mcp: Path, workspace: Path, user_home: Path) -> dict[str, dict]:
    """從 canonical/mcp/ 讀取所有 MCP server 設定，標準化為 {server_name: server_def}。"""
    servers: dict[str, dict] = {}
    if not canonical_mcp.exists():
        return servers

    for json_file in sorted(canonical_mcp.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError("invalid canonical MCP JSON") from None
        if not isinstance(data, dict):
            raise ValueError("invalid canonical MCP JSON object")

        expanded = _expand_mcp_val(data, workspace, user_home)
        if "mcpServers" in expanded and isinstance(expanded["mcpServers"], dict):
            for name, s_def in expanded["mcpServers"].items():
                if isinstance(s_def, dict):
                    servers[name] = s_def
        elif "command" in expanded:
            name = expanded.pop("name", None) or json_file.stem
            servers[name] = expanded
        else:
            for k, v in expanded.items():
                if not k.startswith(("$", "_")) and isinstance(v, dict) and "command" in v:
                    servers[k] = v
    return servers


def _merge_json_mcp(target_file: Path, servers: dict[str, dict], verbose: bool = False, *, check_only: bool = False) -> bool:
    """將 MCP servers 合併進目標 JSON 設定檔 (json_merge)。"""
    try:
        st = target_file.lstat()
    except FileNotFoundError:
        st = None

    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise ValueError("Target file is invalid: must be a regular file owned by current user and not a symlink")
        orig_mode = stat.S_IMODE(st.st_mode)
        try:
            fd = os.open(target_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                        or (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino)):
                    raise ValueError("target changed before read")
                chunks = bytearray()
                limit = 4 * 1024 * 1024
                while len(chunks) <= limit:
                    block = os.read(fd, min(65536, limit + 1 - len(chunks)))
                    if not block:
                        break
                    chunks.extend(block)
                if len(chunks) > limit:
                    raise ValueError("target JSON exceeds size limit")
                content = chunks.decode("utf-8")
            finally:
                os.close(fd)
        except Exception:
            raise ValueError("Failed to read target JSON file") from None
        if not content.strip():
            raise ValueError("Target JSON file is empty")
        try:
            loaded = json.loads(content)
        except Exception:
            raise ValueError("Failed to parse target JSON file") from None
        if not isinstance(loaded, dict):
            raise ValueError("Target JSON root must be an object")
        if "mcpServers" in loaded and not isinstance(loaded["mcpServers"], dict):
            raise ValueError("Target JSON mcpServers must be an object")
        existing_data = loaded
    else:
        orig_mode = 0o600
        existing_data = {}

    if "mcpServers" not in existing_data:
        existing_data["mcpServers"] = {}

    mcp_servers = existing_data["mcpServers"]
    modified = False
    for name, s_def in servers.items():
        if name not in mcp_servers or mcp_servers[name] != s_def:
            mcp_servers[name] = s_def
            modified = True

    if check_only:
        return modified or st is None
    if not modified and st is not None:
        return False

    target_file.parent.mkdir(parents=True, exist_ok=True)
    new_text = json.dumps(existing_data, indent=2, ensure_ascii=False) + "\n"

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_file.parent,
            delete=False,
        ) as tf:
            temp_path = Path(tf.name)
            tf.write(new_text)
            tf.flush()
            os.fchmod(tf.fileno(), orig_mode)
            os.fsync(tf.fileno())

        if st is None:
            # No-clobber publication if another writer created the missing target.
            os.link(temp_path, target_file)
            temp_path.unlink()
        else:
            current = target_file.lstat()
            fingerprint = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                         value.st_mtime_ns, value.st_ctime_ns, value.st_mode)
            if fingerprint(current) != fingerprint(st):
                raise ValueError("Target changed before publication")
            os.replace(temp_path, target_file)
        temp_path = None

        dir_fd = os.open(target_file.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

    log(f"已合併更新 MCP 設定至 {target_file}", verbose, is_verbose_msg=True)
    return True


def _format_codex_mcp_section(name: str, s_def: dict) -> str:
    """格式化單一 MCP Server 為 Codex TOML 區塊。"""
    lines = [f'[mcp_servers.{_toml_key_escape(name)}]']
    if "command" in s_def:
        lines.append(f'command = {json.dumps(s_def["command"], ensure_ascii=False)}')
    if "args" in s_def and isinstance(s_def["args"], list):
        lines.append(f'args = {json.dumps(s_def["args"], ensure_ascii=False)}')
    if "env" in s_def and isinstance(s_def["env"], dict):
        env_pairs = [
            f'{json.dumps(k, ensure_ascii=False)} = {json.dumps(str(v), ensure_ascii=False)}'
            for k, v in s_def["env"].items()
        ]
        lines.append(f'env = {{ {", ".join(env_pairs)} }}')
    if "cwd" in s_def:
        lines.append(f'cwd = {json.dumps(s_def["cwd"], ensure_ascii=False)}')
    return "\n".join(lines) + "\n"


def _merge_toml_mcp(target_file: Path, servers: dict[str, dict], verbose: bool = False) -> bool:
    """將 MCP servers 合併進目標 TOML 設定檔 (toml_mcp_merge)。"""
    import tomllib

    target_file.parent.mkdir(parents=True, exist_ok=True)
    content = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    initial_content = content

    for name, s_def in servers.items():
        block = _format_codex_mcp_section(name, s_def)
        pattern = re.compile(
            rf'(?m)^\[mcp_servers\.(?:"{re.escape(name)}"|{re.escape(name)})\]\s*\n.*?(?=\n\[|\Z)',
            re.DOTALL,
        )
        if pattern.search(content):
            content = pattern.sub(block.rstrip("\n") + "\n", content)
        else:
            sep = "\n\n" if content.strip() else ""
            content = content.rstrip("\n") + sep + block

    if content != initial_content:
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as e:
            log(f"錯誤: MCP TOML 合併將產生無效 TOML，已放棄寫入 {target_file}: {e}", verbose)
            return False

        target_file.write_text(content, encoding="utf-8")
        log(f"已合併更新 MCP TOML 設定至 {target_file}", verbose, is_verbose_msg=True)
        return True
    return False


def _project_mcp(
    cfg: dict, canonical_root: Path, workspace: Path, user_home: Path, verbose: bool
) -> None:
    """C-05: MCP 工具設定動態合流投影。"""
    canonical_mcp = canonical_root / "mcp"
    if not canonical_mcp.exists():
        return

    servers = _load_canonical_mcp_servers(canonical_mcp, workspace, user_home)
    if not servers:
        return

    mode = cfg.get("mode", "json_merge")
    target_file = expand_path(cfg["target_file"], workspace, user_home)

    if mode == "json_merge":
        _merge_json_mcp(target_file, servers, verbose)
    elif mode == "toml_mcp_merge":
        _merge_toml_mcp(target_file, servers, verbose)
    else:
        log(f"警告: 未支援的 MCP 投影模式 [{mode}] 於 {target_file}", verbose)


def _run_runtime_bootstrap(bootstrap: dict, workspace: Path, user_home: Path, verbose: bool, extra_trust=()) -> None:
    b_cfg = bootstrap.get("ensure_workspace_trust")
    if not b_cfg or not b_cfg.get("enabled", False):
        return

    cfg_file = expand_path(b_cfg["config_file"], workspace, user_home)
    trusted_paths = [str(expand_path(t, workspace, user_home)) for t in b_cfg.get("trusted_paths", ["./"])] + list(extra_trust)
    ensure_codex_trust(cfg_file, trusted_paths, verbose)


def apply_harness(
    harness_id: str,
    manifest: dict,
    canonical_root: Path,
    workspace: Path,
    user_home: Path,
    verbose: bool = False,
    extra_trust=(),
) -> None:
    preflight_harness(manifest, canonical_root, workspace, user_home)
    log(f"開始套用 Harness [{harness_id}] ({manifest.get('name')})...", verbose)

    projections = manifest.get("projections", {})
    tiered = manifest.get("tiered", False)

    if "skills" in projections:
        _project_skills(projections["skills"], canonical_root, workspace, user_home, tiered, verbose)

    if "rules" in projections:
        _project_rules(projections["rules"], canonical_root, workspace, user_home, verbose)

    if "fragments" in projections:
        _project_fragments(projections["fragments"], canonical_root, workspace, user_home, verbose)

    if "mcp" in projections:
        _project_mcp(projections["mcp"], canonical_root, workspace, user_home, verbose)

    _run_runtime_bootstrap(manifest.get("runtime_bootstrap", {}), workspace, user_home, verbose, extra_trust)

    log(f"Harness [{harness_id}] 投影完畢。", verbose)


def verify_harness(manifest: dict, workspace: Path, user_home: Path, verbose: bool = False) -> list[ProbeResult]:
    """C-04 / T-04: Fail-Closed 門禁。

    僅於 manifest 宣告 `"tiered": true` 時執行（向後相容：舊 manifest 不受影響）。
    對已投影的 skills 逐一重新探針，回傳所有失敗的 ProbeResult。`allowed_prefixes`
    僅含 workspace，理由與 `dispatch_tiered` 相同：canonical_root 是探針該偵測的
    越界對象，不可預先允許，因此本函式不需要也不接受 canonical_root 參數。
    """
    if not manifest.get("tiered", False):
        return []

    projections = manifest.get("projections", {})
    if "skills" not in projections:
        return []

    cfg = projections["skills"]
    target_dir = expand_path(cfg["target_dir"], workspace, user_home)
    if not target_dir.exists():
        # Fail-Closed: 若投影後目標目錄理應存在卻不存在（apply_harness 對 skills
        # 段落必定 mkdir），視為投影失敗而非「無需驗證」，避免產生假性成功。
        return [ProbeResult(ok=False, reason=ProbeReason.TARGET_NOT_FOUND, path=str(target_dir))]

    failures: list[ProbeResult] = []
    allowed_prefixes = [str(workspace)]
    for skill_dir in target_dir.iterdir():
        if skill_dir.is_dir() or skill_dir.is_symlink():
            result = probe_sandbox_access(skill_dir, allowed_prefixes)
            if not result.ok:
                failures.append(result)
                log(f"Fail-Closed 探針失敗: {skill_dir} ({result.reason})", verbose)

    return failures


def preflight_harness(manifest: dict, canonical_root: Path, workspace: Path, user_home: Path) -> None:
    """Validate required input and existing config before any home mutation."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("projections"), dict):
        raise ValueError("invalid harness manifest")
    if not canonical_root.is_dir():
        raise ValueError("canonical asset root is missing")
    projections = manifest["projections"]
    for cfg_ in projections.values():
        for key in ("target_dir", "target_file", "fallback_target_dir"):
            if isinstance(cfg_, dict) and key in cfg_:
                expand_path(cfg_[key], workspace, user_home)
        if isinstance(cfg_, dict) and "extra_dirs_registration" in cfg_:
            expand_path(cfg_["extra_dirs_registration"].get("config_file"), workspace, user_home)
    boot = (manifest.get("runtime_bootstrap") or {}).get("ensure_workspace_trust")
    if boot:
        expand_path(boot.get("config_file"), workspace, user_home)
        for t_ in boot.get("trusted_paths", ["./"]):
            expand_path(t_, workspace, user_home)
    known = {"skills", "rules", "fragments", "mcp"}
    if not projections or set(projections) - known:
        raise ValueError("unsupported or empty harness projections")
    required = manifest.get("required_assets", [])
    if not isinstance(required, list) or any(kind not in known for kind in required):
        raise ValueError("invalid required assets")
    if any(kind not in projections for kind in required):
        raise ValueError("required projection is not configured")
    present = set()
    for kind, config in projections.items():
        if not isinstance(config, dict):
            raise ValueError("invalid projection config")
        modes = {"skills": {"symlink_children", "materialized_copy"},
                 "rules": {"symlink_children", "md_block_append"},
                 "fragments": {"md_block_append"},
                 "mcp": {"json_merge", "toml_mcp_merge"}}
        default_mode = {"skills": "symlink_children", "rules": "symlink_children",
                        "fragments": "md_block_append", "mcp": "json_merge"}[kind]
        if config.get("mode", default_mode) not in modes[kind]:
            raise ValueError("unsupported projection mode")
        target_key = "target_dir" if kind == "skills" or (kind == "rules" and config.get("mode", default_mode) == "symlink_children") else "target_file"
        if not isinstance(config.get(target_key), str) or not config[target_key]:
            raise ValueError("missing projection target")
        asset_dir = canonical_root / kind
        if asset_dir.is_dir():
            if kind == "skills":
                found = any(p.is_dir() and (p / "SKILL.md").is_file() for p in asset_dir.iterdir())
            else:
                found = any(p.is_file() for p in asset_dir.glob("*.json" if kind == "mcp" else "*.md"))
            if found:
                present.add(kind)
        if kind in required and kind not in present:
            raise ValueError("required canonical assets are missing")
        if kind == "mcp":
            for source in sorted(asset_dir.glob("*.json")) if asset_dir.is_dir() else ():
                try:
                    doc = json.loads(source.read_text(encoding="utf-8"))
                except Exception:
                    raise ValueError("invalid canonical MCP JSON") from None
                if not isinstance(doc, dict) or ("mcpServers" in doc and not isinstance(doc["mcpServers"], dict)):
                    raise ValueError("invalid canonical MCP JSON object")
            servers = _load_canonical_mcp_servers(asset_dir, workspace, user_home)
            if not servers:
                present.discard("mcp")
            if "mcp" in required and not servers:
                raise ValueError("required MCP servers are missing")
            mode = config.get("mode", "json_merge")
            if mode not in ("json_merge", "toml_mcp_merge") or "target_file" not in config:
                raise ValueError("invalid MCP projection config")
            target = expand_path(config["target_file"], workspace, user_home)
            if mode == "json_merge":
                _merge_json_mcp(target, servers, check_only=True)
            elif target.exists() or target.is_symlink():
                import tomllib
                st = target.lstat()
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                    raise ValueError("invalid MCP TOML target")
                try:
                    tomllib.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    raise ValueError("invalid existing MCP TOML") from None
    if not present:
        raise ValueError("canonical contains no usable assets for this harness")


DIST_PLAN_SCHEMA = "extscope-dist.plan.v1"


def plan_harness(manifest: dict, canonical_root: Path, workspace: Path, user_home: Path, extra_trust=()) -> tuple[list[dict], list[str]]:
    """Decide what goes where, without writing anything.

    Returns (items, residual). `items` are extscope-dist plan items for everything a
    copy / symlink / md_block / config_merge (extscope-modules) can express; dist must
    have modules.enable = ["extscope_modules"] or it refuses them. `residual` names what
    still needs agent-specific logic (sandbox-probe tiering, other bootstrap steps);
    the adapter's own apply path still owns those.
    """
    projections = manifest["projections"]
    canonical_root = Path(os.path.abspath(canonical_root))
    items: list[dict] = []
    residual: list[str] = []

    def target(template: str) -> str:
        return os.path.abspath(expand_path(template, workspace, user_home))

    def block(src: Path, dst: str, block_id: str) -> None:
        items.append({"method": "md_block", "src": str(src), "target": dst, "block_id": block_id})

    skills = projections.get("skills")
    if skills is not None:
        if manifest.get("tiered", False) or skills.get("fallback_target_dir") or skills.get("extra_dirs_registration"):
            residual.append("skills: tiered/fallback/extra_dirs needs a sandbox probe")
        else:
            method = "copy" if skills.get("mode", "symlink_children") == "materialized_copy" else "symlink"
            root = canonical_root / "skills"
            for path in sorted(root.iterdir()) if root.is_dir() else ():
                if path.is_dir():
                    items.append({"method": method, "src": str(path),
                                  "target": os.path.join(target(skills["target_dir"]), path.name)})

    rules = projections.get("rules")
    if rules is not None:
        root = canonical_root / "rules"
        files = [f for f in sorted(root.iterdir()) if f.is_file()] if root.is_dir() else []
        if rules.get("mode", "symlink_children") == "md_block_append":
            for f in files:
                block(f, target(rules["target_file"]), f"rule-{f.stem}")
        else:
            for f in files:
                items.append({"method": "symlink", "src": str(f),
                              "target": os.path.join(target(rules["target_dir"]), f.name)})

    fragments = projections.get("fragments")
    if fragments is not None:
        root = canonical_root / "fragments"
        for f in sorted(root.iterdir()) if root.is_dir() else ():
            if f.is_file():
                block(f, target(fragments["target_file"]), f.stem)

    mcp = projections.get("mcp")
    if mcp is not None:
        servers = _load_canonical_mcp_servers(canonical_root / "mcp", workspace, user_home)
        mode = mcp.get("mode", "json_merge")
        if servers and mode == "json_merge":
            items.append({"method": "config_merge", "config": {
                "target": target(mcp["target_file"]), "format": "json", "key": "mcpServers", "entries": servers}})
        elif servers and mode == "toml_mcp_merge":
            keep = ("command", "args", "env", "cwd")
            items.append({"method": "config_merge", "config": {
                "target": target(mcp["target_file"]), "format": "toml", "table": "mcp_servers",
                "entries": {n: {k: d[k] for k in keep if k in d} for n, d in servers.items()}}})

    bootstrap = dict(manifest.get("runtime_bootstrap") or {})
    trust = bootstrap.pop("ensure_workspace_trust", None)
    if trust and trust.get("enabled", False):
        items.append({"method": "config_merge", "config": {
            "target": target(trust["config_file"]), "format": "toml", "table": "projects", "on_exist": "keep",
            "entries": {p: {"trust_level": "trusted"} for p in
                        [str(expand_path(t, workspace, user_home)) for t in trust.get("trusted_paths", ["./"])] + list(extra_trust)}}})
    if bootstrap:
        residual.append("runtime_bootstrap: " + ",".join(sorted(bootstrap)))
    return items, residual


def _item_key(it: dict) -> tuple:
    if it["method"] == "config_merge":
        c = it["config"]
        return (it["method"], c["target"], c.get("key") or c["table"], "")
    return (it["method"], it["target"], it.get("block_id", ""))


def emit_dist_plan(prepared, canonical_root: Path, workspace: Path, user_home: Path, extra_trust=()) -> tuple[dict, list[str]]:
    items: list[dict] = []
    residual: list[str] = []
    seen = set()
    for h_id, manifest in prepared:
        got, left = plan_harness(manifest, canonical_root, workspace, user_home, extra_trust)
        residual += [f"{h_id}: {r}" for r in left]
        for it in got:
            key = _item_key(it)
            if key in seen:
                prior = next(x for x in items if _item_key(x) == key)
                if prior != it:
                    raise ValueError(f"harnesses disagree on {_item_key(it)[1]}")
                continue
            seen.add(key)
            items.append(it)
    return {"schema": DIST_PLAN_SCHEMA, "items": items}, residual


def main() -> int:
    parser = argparse.ArgumentParser(description="ExtScope Project Harness Dispatcher")
    parser.add_argument("--harness", required=True, help="目標 Harness 代碼 (逗號分隔，例如: antigravity,codex)")
    parser.add_argument("--canonical-root", default="/run/ext/canonical", help="中立資產目錄")
    parser.add_argument("--manifests-dir", default="", help="Harness Manifests 目錄")
    parser.add_argument("--workspace", required=True, help="project dir; manifest paths `./x` resolve under it")
    parser.add_argument("--user-home", required=True, help="home dir; manifest paths `~/x` resolve under it")
    parser.add_argument("--trust-path", action="append", default=[],
                        help="absolute extra path to trust (codex); repeatable. Manifests only trust `./`")
    parser.add_argument("--verbose", action="store_true", help="輸出詳細除錯訊息")
    parser.add_argument("--emit-dist-plan", action="store_true",
                        help="write nothing; print an extscope-dist.plan.v1 JSON to stdout for extscope-dist to apply")
    parser.add_argument("--allow-residual", action="store_true",
                        help="with --emit-dist-plan: exit 0 even if some projections cannot be expressed as a dist plan")
    args = parser.parse_args()

    canonical_root = Path(args.canonical_root)
    extra_trust = list(args.trust_path)
    if any(not os.path.isabs(t) for t in extra_trust):
        log("--trust-path must be absolute", args.verbose)
        return 2
    workspace = Path(args.workspace)
    user_home = Path(args.user_home)

    manifests_dir = Path(args.manifests_dir) if args.manifests_dir else Path(__file__).resolve().parent.parent / "manifests"
    if not manifests_dir.is_dir():
        log("required manifests directory is missing", args.verbose)
        return 2

    target_harnesses = [h.strip().lower() for h in args.harness.split(",")]
    prepared = []
    try:
        if not target_harnesses or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", h) for h in target_harnesses):
            raise ValueError("invalid harness id")
        if len(set(target_harnesses)) != len(target_harnesses):
            raise ValueError("duplicate harness id")
        for h_id in target_harnesses:
            manifest_file = manifests_dir / f"harness-{h_id}.json"
            if not manifest_file.is_file():
                raise ValueError("required harness manifest is missing")
            with manifest_file.open(encoding="utf-8") as f:
                manifest = json.load(f)
            preflight_harness(manifest, canonical_root, workspace, user_home)
            prepared.append((h_id, manifest))
    except Exception:
        log("Harness preflight failed; no projections applied", args.verbose)
        return 2

    if args.emit_dist_plan:
        try:
            plan, residual = emit_dist_plan(prepared, canonical_root, workspace, user_home, extra_trust)
        except Exception as e:
            log(f"emit-dist-plan failed: {e}", args.verbose)
            return 2
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        for r in residual:
            log(f"not expressible as dist plan (adapter still owns it): {r}")
        return 0 if args.allow_residual or not residual else 3

    for h_id, manifest in prepared:
        try:
            apply_harness(h_id, manifest, canonical_root, workspace, user_home, args.verbose, extra_trust)

            failures = verify_harness(manifest, workspace, user_home, args.verbose)
            if failures:
                diagnostic = {
                    "harness": h_id,
                    "status": "fail_closed",
                    "failures": [asdict(f) for f in failures],
                }
                print(json.dumps(diagnostic, ensure_ascii=False), file=sys.stderr)
                return 2
        except Exception as e:
            log(f"套用 Harness [{h_id}] 失敗: {e}", args.verbose)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
