"""Configuration. No built-in paths: every root comes from a file, a flag or the environment."""
import json
import os
import re
import sys
from dataclasses import dataclass, field

from .errors import ContainerError

ENV_CONFIG = "EXTSCOPE_CONTAINER_CONFIG"
_FIELDS = {"allowed_roots", "denied_roots", "remap", "state_dir", "env_dir", "podman", "variables", "hooks_allowed"}
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def default_config_path(environ=None, platform=None):
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    home = environ.get("HOME") or environ.get("USERPROFILE") or os.path.expanduser("~")
    if platform.startswith("win"):
        base = environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    elif platform == "darwin":
        base = os.path.join(home, "Library", "Preferences")
    else:
        base = environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, "extscope", "container.json")


def default_state_dir(environ=None, platform=None):
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    home = environ.get("HOME") or environ.get("USERPROFILE") or os.path.expanduser("~")
    if platform.startswith("win"):
        return os.path.join(environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), "extscope", "container")
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "extscope", "container")
    return os.path.join(environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"), "extscope", "container")


def expand(text, variables):
    def sub(m):
        if m.group(1) not in variables:
            raise ContainerError("unknown_variable", m.group(1))
        return variables[m.group(1)]
    return _VAR.sub(sub, text)


def norm_posix(path, what="path"):
    """Container paths are POSIX regardless of the host OS."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path or "\\" in path:
        raise ContainerError("invalid_path", f"{what}: {path!r}")
    if ".." in path.split("/"):
        raise ContainerError("path_has_dotdot", f"{what}: {path}")
    out = "/" + "/".join(p for p in path.split("/") if p not in ("", "."))
    return out


def under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


@dataclass
class Policy:
    allowed_roots: list = field(default_factory=list)
    denied_roots: list = field(default_factory=list)
    remap: list = field(default_factory=list)
    state_dir: str = ""
    env_dir: str = ""
    podman: str = "podman"
    variables: dict = field(default_factory=dict)
    hooks_allowed: bool = False
    source: str = ""

    def map(self, path):
        path = norm_posix(path)
        best = None
        for frm, to in self.remap:
            if under(path, frm) and (best is None or len(frm) > len(best[0])):
                best = (frm, to)
        return path if best is None else norm_posix(best[1] + path[len(best[0]):])

    def check(self, mapped):
        if not self.allowed_roots:
            raise ContainerError("no_allowed_roots", "configure allowed_roots (container paths); fail-closed")
        if any(under(mapped, d) for d in self.denied_roots):
            raise ContainerError("target_denied", mapped)
        if not any(under(mapped, a) for a in self.allowed_roots):
            raise ContainerError("target_outside_allowed_roots", mapped)
        return mapped


def load(config_path=None, cli_vars=None, state_dir=None, environ=None, platform=None):
    environ = os.environ if environ is None else environ
    path = config_path or environ.get(ENV_CONFIG)
    explicit = path is not None
    path = path or default_config_path(environ, platform)
    data, source = {}, ""
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ContainerError("bad_config", f"{path}: {exc}") from None
        source = path
    elif explicit:
        raise ContainerError("config_not_found", path)
    if not isinstance(data, dict) or set(data) - _FIELDS:
        raise ContainerError("bad_config", "unknown or malformed fields")
    variables = {**data.get("variables", {}), **(cli_vars or {})}
    ex = lambda v: expand(v, variables)
    remap = []
    for r in data.get("remap", []):
        if not isinstance(r, dict) or set(r) != {"from", "to"}:
            raise ContainerError("bad_config", "remap rule needs from/to")
        remap.append((norm_posix(ex(r["from"])), norm_posix(ex(r["to"]))))
    return Policy([norm_posix(ex(x)) for x in data.get("allowed_roots", [])],
                  [norm_posix(ex(x)) for x in data.get("denied_roots", [])], remap,
                  os.path.normpath(ex(state_dir or data.get("state_dir") or default_state_dir(environ, platform))),
                  norm_posix(ex(data["env_dir"])) if data.get("env_dir") else "",
                  data.get("podman", "podman"), variables, bool(data.get("hooks_allowed", False)), source)
