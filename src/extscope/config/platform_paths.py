"""Cross-platform FHS & OS path resolver for ExtScope.

Supports:
- Linux / Container (Standard Filesystem Hierarchy Standard / FHS + XDG Base Directory)
- macOS (Darwin Application Support & Preferences hierarchy)
- Windows (ProgramData & AppData hierarchy)

No hardcoded site assumptions. Environment-driven with safe fallbacks.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Mapping, Optional


class OSPlatform(str, Enum):
    LINUX = "linux"
    DARWIN = "darwin"
    WINDOWS = "windows"


@dataclass(frozen=True)
class PortablePaths:
    """Portable, normalized filesystem paths for ExtScope."""
    platform: OSPlatform
    mode: str  # "system" | "user"
    config_root: Path
    state_root: Path
    canonical_root: Path
    cache_root: Path
    runtime_root: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform.value,
            "mode": self.mode,
            "config_root": str(self.config_root),
            "state_root": str(self.state_root),
            "canonical_root": str(self.canonical_root),
            "cache_root": str(self.cache_root),
            "runtime_root": str(self.runtime_root),
        }


def detect_platform() -> OSPlatform:
    """Detect current operating system platform."""
    if sys.platform.startswith("win"):
        return OSPlatform.WINDOWS
    elif sys.platform == "darwin":
        return OSPlatform.DARWIN
    return OSPlatform.LINUX


def resolve_portable_paths(
    *,
    mode: str = "system",
    target_platform: Optional[OSPlatform] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path | str] = None,
    overrides: Optional[Mapping[str, Path | str]] = None,
) -> PortablePaths:
    """Resolve portable ExtScope paths adhering to FHS, XDG or platform conventions.

    Args:
        mode: "system" for service/daemon or "user" for local user execution.
        target_platform: Target OS (defaults to current platform).
        env: Environment variables mapping (defaults to os.environ).
        home: Explicit user home directory.
        overrides: Explicit overrides for any path field.
    """
    if mode not in ("system", "user"):
        raise ValueError(f"mode must be 'system' or 'user', got: {mode}")

    plt = target_platform or detect_platform()
    environ = env if env is not None else os.environ

    user_home = Path(home) if home else Path(environ.get("HOME") or environ.get("USERPROFILE") or "~").expanduser()

    ov = {k: Path(v) for k, v in (overrides or {}).items()}

    if plt == OSPlatform.LINUX:
        # Linux / Container Standard FHS & XDG
        if mode == "system":
            state = ov.get("state_root", Path("/var/lib/extscope"))
            config = ov.get("config_root", Path("/etc/extscope"))
            canonical = ov.get("canonical_root", state / "canonical")
            cache = ov.get("cache_root", Path("/var/cache/extscope"))
            runtime = ov.get("runtime_root", Path("/run/extscope"))
        else:
            xdg_config = Path(environ.get("XDG_CONFIG_HOME") or user_home / ".config")
            xdg_state = Path(environ.get("XDG_STATE_HOME") or user_home / ".local/state")
            xdg_data = Path(environ.get("XDG_DATA_HOME") or user_home / ".local/share")
            xdg_cache = Path(environ.get("XDG_CACHE_HOME") or user_home / ".cache")
            xdg_runtime = Path(environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid() if hasattr(os, 'getuid') else 1000}")

            config = ov.get("config_root", xdg_config / "extscope")
            state = ov.get("state_root", xdg_state / "extscope")
            canonical = ov.get("canonical_root", xdg_data / "extscope/canonical")
            cache = ov.get("cache_root", xdg_cache / "extscope")
            runtime = ov.get("runtime_root", xdg_runtime / "extscope")

    elif plt == OSPlatform.DARWIN:
        # macOS Conventions
        if mode == "system":
            state = ov.get("state_root", Path("/Library/Application Support/extscope"))
            config = ov.get("config_root", Path("/Library/Preferences/extscope"))
            canonical = ov.get("canonical_root", state / "canonical")
            cache = ov.get("cache_root", Path("/Library/Caches/extscope"))
            runtime = ov.get("runtime_root", Path("/var/run/extscope"))
        else:
            app_support = user_home / "Library/Application Support/extscope"
            config = ov.get("config_root", user_home / "Library/Preferences/extscope")
            state = ov.get("state_root", app_support / "state")
            canonical = ov.get("canonical_root", app_support / "canonical")
            cache = ov.get("cache_root", user_home / "Library/Caches/extscope")
            runtime = ov.get("runtime_root", app_support / "run")

    elif plt == OSPlatform.WINDOWS:
        # Windows Conventions
        program_data = Path(environ.get("ProgramData") or r"C:\ProgramData") / "extscope"
        local_app_data = Path(environ.get("LOCALAPPDATA") or user_home / r"AppData\Local") / "extscope"
        app_data = Path(environ.get("APPDATA") or user_home / r"AppData\Roaming") / "extscope"

        if mode == "system":
            config = ov.get("config_root", program_data / "config")
            state = ov.get("state_root", program_data / "state")
            canonical = ov.get("canonical_root", program_data / "canonical")
            cache = ov.get("cache_root", program_data / "cache")
            runtime = ov.get("runtime_root", program_data / "run")
        else:
            config = ov.get("config_root", app_data / "config")
            state = ov.get("state_root", local_app_data / "state")
            canonical = ov.get("canonical_root", local_app_data / "canonical")
            cache = ov.get("cache_root", local_app_data / "cache")
            runtime = ov.get("runtime_root", local_app_data / "run")

    return PortablePaths(
        platform=plt,
        mode=mode,
        config_root=config,
        state_root=state,
        canonical_root=canonical,
        cache_root=cache,
        runtime_root=runtime,
    )
