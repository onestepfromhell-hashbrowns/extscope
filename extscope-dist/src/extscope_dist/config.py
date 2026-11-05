"""Configuration and path resolution. No path is hardcoded: every root comes from
an explicit file, an explicit flag, an environment variable, or the platform's
standard per-user directories."""
import json
import os
import re
import sys
from dataclasses import dataclass, field

from .errors import DistError

ENV_CONFIG = "EXTSCOPE_DIST_CONFIG"
ENV_STATE = "EXTSCOPE_DIST_STATE_DIR"
_FIELDS = {"state_dir", "variables", "remap", "allowed_roots", "denied_roots", "source_roots", "modules"}
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _home(environ):
    return environ.get("HOME") or environ.get("USERPROFILE") or os.path.expanduser("~")


def default_config_path(environ=None, platform=None):
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    if platform.startswith("win"):
        base = environ.get("APPDATA") or os.path.join(_home(environ), "AppData", "Roaming")
    elif platform == "darwin":
        base = os.path.join(_home(environ), "Library", "Preferences")
    else:
        base = environ.get("XDG_CONFIG_HOME") or os.path.join(_home(environ), ".config")
    return os.path.join(base, "extscope", "dist.json")


def default_state_dir(environ=None, platform=None):
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    if platform.startswith("win"):
        base = environ.get("LOCALAPPDATA") or os.path.join(_home(environ), "AppData", "Local")
        return os.path.join(base, "extscope", "dist")
    if platform == "darwin":
        return os.path.join(_home(environ), "Library", "Application Support", "extscope", "dist")
    base = environ.get("XDG_STATE_HOME") or os.path.join(_home(environ), ".local", "state")
    return os.path.join(base, "extscope", "dist")


def expand(text, variables):
    """Replace ${NAME} from the given mapping only; an unknown name is a refusal."""
    def sub(m):
        if m.group(1) not in variables:
            raise DistError("unknown_variable", m.group(1))
        return variables[m.group(1)]
    return _VAR.sub(sub, text)


def _expand_tree(value, variables):
    if isinstance(value, str):
        return expand(value, variables)
    if isinstance(value, list):
        return [_expand_tree(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _expand_tree(v, variables) for k, v in value.items()}
    return value


def norm_abs(path, what="path"):
    if not isinstance(path, str) or not path or "\x00" in path:
        raise DistError("invalid_path", what)
    if not os.path.isabs(path):
        raise DistError("path_not_absolute", f"{what}: {path}")
    if ".." in re.split(r"[\\/]+", path):
        raise DistError("path_has_dotdot", f"{what}: {path}")
    return os.path.normpath(path)


def real(path):
    return os.path.realpath(path)


def under(path, root):
    path, root = os.path.normcase(path), os.path.normcase(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


@dataclass
class Config:
    state_dir: str
    variables: dict = field(default_factory=dict)
    remap: list = field(default_factory=list)          # [(from, to)] longest `from` wins
    allowed_roots: list = field(default_factory=list)
    denied_roots: list = field(default_factory=list)
    module_imports: list = field(default_factory=list)   # import names, explicitly enabled
    module_policy: dict = field(default_factory=dict)    # {module name: policy dict}
    source: str = ""
    source_roots: list = field(default_factory=list)   # pullback may write only here (fail-closed)

    def map_target(self, target):
        target = norm_abs(target, "target")
        best = None
        for frm, to in self.remap:
            if under(target, frm) and (best is None or len(frm) > len(best[0])):
                best = (frm, to)
        if best is None:
            return target
        frm, to = best
        return os.path.normpath(to + target[len(frm):])

    def check_target(self, mapped):
        """Allow only inside allowed_roots, never inside denied_roots. Both the
        lexical path and the real path of its parent directory chain are checked."""
        if not self.allowed_roots:
            raise DistError("no_allowed_roots", "configure allowed_roots; apply is fail-closed")
        parent = os.path.dirname(mapped)
        probe = parent
        while not os.path.lexists(probe) and os.path.dirname(probe) != probe:
            probe = os.path.dirname(probe)
        # resolve only the directory chain; the final component is never followed
        resolved = os.path.join(real(probe), os.path.relpath(parent, probe), os.path.basename(mapped))
        for candidate in (mapped, os.path.normpath(resolved)):
            for d in self.denied_roots:
                if under(candidate, d) or under(candidate, real(d)):
                    raise DistError("target_denied", mapped)
            if not any(under(candidate, a) or under(candidate, real(a)) for a in self.allowed_roots):
                raise DistError("target_outside_allowed_roots", mapped)


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
            raise DistError("bad_config", f"{path}: {exc}") from None
        source = path
    elif explicit:
        raise DistError("config_not_found", path)
    if not isinstance(data, dict) or set(data) - _FIELDS:
        raise DistError("bad_config", f"unknown or malformed fields in {path}")
    variables = dict(data.get("variables", {}))
    variables.update(cli_vars or {})
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in variables.items()):
        raise DistError("bad_config", "variables must be string pairs")

    def roots(key):
        vals = data.get(key, [])
        if not isinstance(vals, list):
            raise DistError("bad_config", key)
        return [norm_abs(expand(v, variables), key) for v in vals]

    remap = []
    for rule in data.get("remap", []):
        if not isinstance(rule, dict) or set(rule) != {"from", "to"}:
            raise DistError("bad_config", "remap rule needs exactly from/to")
        remap.append((norm_abs(expand(rule["from"], variables), "remap.from"),
                      norm_abs(expand(rule["to"], variables), "remap.to")))
    if len({f for f, _ in remap}) != len(remap):
        raise DistError("bad_config", "duplicate remap from")
    mods = data.get("modules", {})
    if not isinstance(mods, dict) or set(mods) - {"enable", "policy"}:
        raise DistError("bad_config", "modules accepts only enable/policy")
    imports = mods.get("enable", [])
    if not isinstance(imports, list) or not all(isinstance(x, str) and x for x in imports):
        raise DistError("bad_config", "modules.enable must be a list of import names")
    policy = _expand_tree(mods.get("policy", {}), variables)
    if not isinstance(policy, dict):
        raise DistError("bad_config", "modules.policy must be an object")
    sd = state_dir or environ.get(ENV_STATE) or data.get("state_dir") or default_state_dir(environ, platform)
    return Config(norm_abs(expand(sd, variables), "state_dir"), variables, remap,
                  roots("allowed_roots"), roots("denied_roots"), imports, policy, source, roots("source_roots"))
