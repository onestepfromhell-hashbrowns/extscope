"""User-visible path contracts; no legacy runtime or installed package required."""
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import tomllib

import pytest

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / "src"))
from extscope.config.paths import resolve_paths


def test_system_defaults_ignore_ambient_environment(monkeypatch):
    monkeypatch.setenv("HOME", "/untrusted")
    monkeypatch.setenv("XDG_DATA_HOME", "/untrusted/data")
    monkeypatch.setenv("EXTSCOPE_CANONICAL_ROOT", "/untrusted/canonical")
    p = resolve_paths(xdg=object(), home=object())
    assert tuple(map(str, (p.config_root, p.state_root, p.canonical_root,
                          p.cache_root, p.runtime_root))) == (
        "/etc/extscope", "/var/lib/extscope", "/var/lib/extscope/canonical",
        "/var/cache/extscope", "/run/extscope")


def test_overrides_and_canonical_derivation():
    p = resolve_paths(overrides={"state_root": "/state/extensions"})
    assert str(p.canonical_root) == "/state/extensions/canonical"
    p = resolve_paths(overrides={"state_root": "/state/extensions",
                                 "canonical_root": PurePosixPath("/separate/assets")})
    assert str(p.canonical_root) == "/separate/assets"
    with pytest.raises(AttributeError):
        p.state_root = PurePosixPath("/changed")


def test_explicit_xdg_without_home():
    p = resolve_paths(mode="user", xdg={
        "XDG_CONFIG_HOME": "/user/config", "XDG_DATA_HOME": "/user/data",
        "XDG_STATE_HOME": "/user/state", "XDG_CACHE_HOME": "/user/cache",
        "XDG_RUNTIME_DIR": "/runtime/user"})
    assert str(p.config_root) == "/user/config/extscope"
    assert str(p.canonical_root) == "/user/data/extscope/canonical"
    assert str(p.state_root) == "/user/state/extscope"
    assert str(p.cache_root) == "/user/cache/extscope"
    assert str(p.runtime_root) == "/runtime/user/extscope"


def test_explicit_home_fallback_and_runtime_override():
    p = resolve_paths(mode="user", home="/users/alice",
                      overrides={"runtime_root": "/session/extscope"})
    assert str(p.config_root) == "/users/alice/.config/extscope"
    assert str(p.canonical_root) == "/users/alice/.local/share/extscope/canonical"
    assert str(p.state_root) == "/users/alice/.local/state/extscope"
    assert str(p.cache_root) == "/users/alice/.cache/extscope"
    assert str(p.runtime_root) == "/session/extscope"


def test_all_user_overrides_require_no_implicit_home():
    overrides = {k: "/explicit/" + k for k in (
        "config_root", "state_root", "canonical_root", "cache_root", "runtime_root")}
    p = resolve_paths(mode="user", overrides=overrides)
    assert str(p.canonical_root) == overrides["canonical_root"]


@pytest.mark.parametrize("value", ["", "relative/path", "../path", "/a/../b",
    "//server/root", "/a//b", "/a/./b", "/a/", "/a\x00b", "/a\nb", "/a\tb",
    "/a\\b", "/", "/a\u202eb", "/a\u200bb", "/a\ud800b", None, 123, False])
def test_invalid_override_is_rejected(value):
    with pytest.raises(ValueError):
        resolve_paths(overrides={"canonical_root": value})


@pytest.mark.parametrize("kwargs", [
    {"mode": "unknown"}, {"overrides": {"unknown": "/root"}}, {"overrides": []},
    {"mode": "user", "home": "/users/alice"},
    {"mode": "user", "xdg": {"XDG_RUNTIME_DIR": "/runtime/user"}},
    {"mode": "user", "home": "/users/alice", "xdg": {"UNKNOWN": "/root"}},
    {"mode": "user", "home": "/users/alice", "xdg": {"XDG_RUNTIME_DIR": ""}},
    {"mode": "user", "home": "relative", "xdg": {"XDG_RUNTIME_DIR": "/runtime"}},
    {"mode": "user", "home": "/users/alice", "xdg": []},
])
def test_missing_or_invalid_user_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        resolve_paths(**kwargs)


def test_metadata_exposes_only_explicit_readonly_cli_without_runtime_dependencies():
    d = tomllib.loads((STAGE / "pyproject.toml").read_text())
    assert d["project"]["dependencies"] == []
    assert d["project"]["scripts"] == {"extscope": "extscope.cli:main"}
    assert "entry-points" not in d["project"]
    assert (STAGE / "src/extscope/__main__.py").is_file()


def test_import_and_resolution_have_no_external_side_effects():
    code = r'''
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event == 'import' and args[0].startswith(('agent_harness_adapter',)):
        raise AssertionError('plugin import')
    if event == 'open':
        mode, flags = args[1:3]
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise AssertionError('file write')
    if event in ('os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.symlink',
                 'os.chmod', 'os.chown', 'os.link', 'os.truncate',
                 'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork') or event.startswith('socket.'):
        raise AssertionError('external side effect: ' + event)
sys.addaudithook(audit)
from extscope.config.paths import resolve_paths
import extscope.digest
import extscope.declaration, extscope.resolver, extscope.plan
import extscope.binding, extscope.cli
assert str(resolve_paths().canonical_root) == '/var/lib/extscope/canonical'
assert str(resolve_paths(mode='user', home='/explicit/home',
    xdg={'XDG_RUNTIME_DIR':'/explicit/runtime'}).runtime_root) == '/explicit/runtime/extscope'
assert not any(n.startswith(('agent_harness_adapter',)) for n in sys.modules)
print('pure import and resolution')
'''
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(STAGE / "src")],
                          capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=15)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "pure import and resolution"


@pytest.mark.parametrize("value", [PurePosixPath("/a/../b"),
    PurePosixPath("a/..\\b"), PureWindowsPath("C:/a")])
def test_path_objects_cannot_bypass_traversal_or_platform_checks(value):
    with pytest.raises(ValueError):
        resolve_paths(overrides={"canonical_root": value})


def test_normalized_pure_path_is_explicitly_supported():
    p = resolve_paths(overrides={"canonical_root": PurePosixPath("/a/")})
    assert str(p.canonical_root) == "/a"
