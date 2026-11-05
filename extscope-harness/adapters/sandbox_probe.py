#!/usr/bin/env python3
"""Sandbox Accessibility & Integrity Probe — 沙箱存取完整性探針 (C-04)。

獨立模組，供 project-harness.py 的階梯式調度器 (dispatch_tiered) 與 Fail-Closed
門禁 (verify_harness) 呼叫，亦可作為獨立 CLI 於容器啟動時單獨驗收。

呼叫契約：`target_path` 應為單一 Skill 的目錄（即 canonical `skills/<name>/`
投影後的對應路徑），本探針只檢查該目錄「直接底下」的 `SKILL.md`，不遞迴掃描
整個 Skills 容器目錄——若要驗收多個 Skill，呼叫端應逐一迭代各自的目錄並分別
呼叫本函式（`dispatch_tiered` 與 `verify_harness` 皆遵循此契約）。
"""

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional


class ProbeReason(str, Enum):
    """探針診斷原因，取代未經驗證的字串常數 (避免拼字誤植導致判斷失準)。"""

    OK = "ok"
    TARGET_NOT_FOUND = "target_not_found"
    PATH_TRAVERSAL_BLOCKED = "path_traversal_blocked"
    SKILL_MD_MISSING = "skill_md_missing"
    SKILL_MD_EMPTY = "skill_md_empty"
    DANGLING_SYMLINK = "dangling_symlink"
    PERMISSION_DENIED = "permission_denied"
    DANGLING_OR_UNREADABLE = "dangling_or_unreadable"


@dataclass
class ProbeResult:
    ok: bool
    reason: ProbeReason
    path: str = ""


def _is_within_allowed(real_path: str, allowed_prefixes: List[str]) -> bool:
    for prefix in allowed_prefixes:
        normalized = prefix.rstrip("/") or "/"
        if real_path == normalized or real_path.startswith(normalized + "/"):
            return True
    return False


def _check_symlink_boundary(path: Path, allowed_prefixes: List[str]) -> Optional[ProbeResult]:
    """若 path 為軟連結且其真實路徑落在允許前綴以外，回傳越界之 ProbeResult；否則回傳 None。"""
    if not path.is_symlink():
        return None
    real = os.path.realpath(path)
    if not _is_within_allowed(real, allowed_prefixes):
        return ProbeResult(ok=False, reason=ProbeReason.PATH_TRAVERSAL_BLOCKED, path=str(path))
    return None


def probe_sandbox_access(target_path: Path, allowed_prefixes: Optional[List[str]] = None) -> ProbeResult:
    """驗證 target_path（單一 Skill 目錄）在沙箱視角下是否可被非特權進程完整讀取。

    `allowed_prefixes` 必須反映「沙箱進程實際可見」的路徑邊界（例如 Codex 沙箱
    僅可見 `/workspace`），而非分發器（host 端）自身可見的路徑；否則探針會對
    真正會被沙箱阻斷的軟連結產生假性通過 (false positive)。
    """
    allowed_prefixes = allowed_prefixes or ["/workspace"]
    target_path = Path(target_path)

    if not target_path.exists():
        return ProbeResult(ok=False, reason=ProbeReason.TARGET_NOT_FOUND, path=str(target_path))

    boundary_violation = _check_symlink_boundary(target_path, allowed_prefixes)
    if boundary_violation:
        return boundary_violation

    if target_path.is_dir():
        skill_files = [target_path / "SKILL.md"]
    else:
        skill_files = [target_path]

    for skill_file in skill_files:
        boundary_violation = _check_symlink_boundary(skill_file, allowed_prefixes)
        if boundary_violation:
            return boundary_violation

        try:
            if not skill_file.exists():
                reason = ProbeReason.DANGLING_SYMLINK if skill_file.is_symlink() else ProbeReason.SKILL_MD_MISSING
                return ProbeResult(ok=False, reason=reason, path=str(skill_file))
            if skill_file.is_dir():
                return ProbeResult(ok=False, reason=ProbeReason.SKILL_MD_MISSING, path=str(skill_file))
            if skill_file.stat().st_size == 0:
                return ProbeResult(ok=False, reason=ProbeReason.SKILL_MD_EMPTY, path=str(skill_file))
            with open(skill_file, "r", encoding="utf-8") as f:
                f.read()
        except PermissionError:
            return ProbeResult(ok=False, reason=ProbeReason.PERMISSION_DENIED, path=str(skill_file))
        except OSError:
            return ProbeResult(ok=False, reason=ProbeReason.DANGLING_OR_UNREADABLE, path=str(skill_file))

    return ProbeResult(ok=True, reason=ProbeReason.OK, path=str(target_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox Accessibility & Integrity Probe")
    parser.add_argument("--target-dir", required=True, help="欲驗收的單一 Skill 目標路徑")
    parser.add_argument(
        "--allowed-prefix",
        action="append",
        default=[],
        help="沙箱進程實際可見的真實路徑前綴（可重複指定，預設 /workspace）",
    )
    args = parser.parse_args()

    allowed = args.allowed_prefix or ["/workspace"]
    result = probe_sandbox_access(Path(args.target_dir), allowed)
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
