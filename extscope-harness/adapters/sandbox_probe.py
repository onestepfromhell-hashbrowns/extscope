#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Sandbox Accessibility & Integrity Probe — sandbox accessibility and integrity probe (C-04).

Standalone module called by the tiered dispatcher (dispatch_tiered) and the fail-closed
gate (verify_harness) in project-harness.py; it can also run as a CLI to check a container at startup.

Contract: `target_path` is the directory of a single skill (the projected location of canonical `skills/<name>/`);
the probe checks only the `SKILL.md` directly inside that directory and does not scan the
whole skills directory. To check several skills, the caller iterates over their directories and
calls this function once per skill (`dispatch_tiered` and `verify_harness` both follow this contract).
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
    """Probe diagnosis reason; replaces unchecked string constants (avoids typos causing wrong decisions)."""

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
    """If path is a symlink whose real path falls outside the allowed prefixes, return an out-of-bounds ProbeResult; otherwise None."""
    if not path.is_symlink():
        return None
    real = os.path.realpath(path)
    if not _is_within_allowed(real, allowed_prefixes):
        return ProbeResult(ok=False, reason=ProbeReason.PATH_TRAVERSAL_BLOCKED, path=str(path))
    return None


def probe_sandbox_access(target_path: Path, allowed_prefixes: Optional[List[str]] = None) -> ProbeResult:
    """Check that target_path (a single skill directory) is fully readable by an unprivileged process from the sandbox's point of view.

    `allowed_prefixes` must reflect the path boundary the sandboxed process can actually see (e.g. a Codex sandbox
    sees only `/workspace`), not what the dispatcher on the host can see; otherwise the probe reports a false positive
    for symlinks the sandbox would actually block.
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
    parser.add_argument("--target-dir", required=True, help="target path of the single skill to check")
    parser.add_argument(
        "--allowed-prefix",
        action="append",
        default=[],
        help="real path prefixes the sandboxed process can see (repeatable, default /workspace)",
    )
    args = parser.parse_args()

    allowed = args.allowed_prefix or ["/workspace"]
    result = probe_sandbox_access(Path(args.target_dir), allowed)
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
