"""Unit tests for Portable FHS & Cross-Platform Path Resolver."""
from __future__ import annotations

from pathlib import Path
import pytest
from extscope.config.platform_paths import (
    OSPlatform,
    PortablePaths,
    detect_platform,
    resolve_portable_paths,
)


def test_resolve_portable_paths_linux_system():
    paths = resolve_portable_paths(mode="system", target_platform=OSPlatform.LINUX)
    assert paths.platform == OSPlatform.LINUX
    assert paths.mode == "system"
    assert paths.config_root == Path("/etc/extscope")
    assert paths.state_root == Path("/var/lib/extscope")
    assert paths.canonical_root == Path("/var/lib/extscope/canonical")
    assert paths.cache_root == Path("/var/cache/extscope")
    assert paths.runtime_root == Path("/run/extscope")


def test_resolve_portable_paths_linux_user():
    fake_env = {
        "HOME": "/home/appuser",
        "XDG_CONFIG_HOME": "/home/appuser/.config",
        "XDG_STATE_HOME": "/home/appuser/.local/state",
        "XDG_DATA_HOME": "/home/appuser/.local/share",
        "XDG_CACHE_HOME": "/home/appuser/.cache",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }
    paths = resolve_portable_paths(mode="user", target_platform=OSPlatform.LINUX, env=fake_env)
    assert paths.config_root == Path("/home/appuser/.config/extscope")
    assert paths.state_root == Path("/home/appuser/.local/state/extscope")
    assert paths.canonical_root == Path("/home/appuser/.local/share/extscope/canonical")
    assert paths.cache_root == Path("/home/appuser/.cache/extscope")
    assert paths.runtime_root == Path("/run/user/1000/extscope")


def test_resolve_portable_paths_darwin_system_and_user():
    # macOS system
    sys_paths = resolve_portable_paths(mode="system", target_platform=OSPlatform.DARWIN)
    assert sys_paths.state_root == Path("/Library/Application Support/extscope")
    assert sys_paths.config_root == Path("/Library/Preferences/extscope")

    # macOS user
    usr_paths = resolve_portable_paths(
        mode="user",
        target_platform=OSPlatform.DARWIN,
        env={"HOME": "/Users/developer"}
    )
    assert usr_paths.canonical_root == Path("/Users/developer/Library/Application Support/extscope/canonical")
    assert usr_paths.config_root == Path("/Users/developer/Library/Preferences/extscope")


def test_resolve_portable_paths_windows_system_and_user():
    fake_env = {
        "ProgramData": r"C:\ProgramData",
        "LOCALAPPDATA": r"C:\Users\tester\AppData\Local",
        "APPDATA": r"C:\Users\tester\AppData\Roaming",
        "USERPROFILE": r"C:\Users\tester",
    }
    # Windows system
    sys_paths = resolve_portable_paths(mode="system", target_platform=OSPlatform.WINDOWS, env=fake_env)
    assert str(sys_paths.state_root).replace("/", "\\") == r"C:\ProgramData\extscope\state"

    # Windows user
    usr_paths = resolve_portable_paths(mode="user", target_platform=OSPlatform.WINDOWS, env=fake_env)
    assert str(usr_paths.canonical_root).replace("/", "\\") == r"C:\Users\tester\AppData\Local\extscope\canonical"


def test_resolve_portable_paths_overrides():
    paths = resolve_portable_paths(
        mode="system",
        target_platform=OSPlatform.LINUX,
        overrides={"canonical_root": "/opt/custom_canonical"}
    )
    assert paths.canonical_root == Path("/opt/custom_canonical")
    assert paths.config_root == Path("/etc/extscope")  # Default preserved
