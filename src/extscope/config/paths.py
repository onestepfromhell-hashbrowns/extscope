"""Resolve explicit POSIX paths without accessing the environment or filesystem.

These paths are configuration, not proof of runtime ownership, permissions or
symlink safety. The future I/O boundary must verify those properties before use.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
import unicodedata


@dataclass(frozen=True)
class ExtScopePaths:
    config_root: PurePosixPath
    state_root: PurePosixPath
    canonical_root: PurePosixPath
    cache_root: PurePosixPath
    runtime_root: PurePosixPath


_FIELDS = frozenset(ExtScopePaths.__dataclass_fields__)
_XDG = frozenset({"XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                  "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"})


def _path(value: object) -> PurePosixPath:
    if not isinstance(value, (str, PurePosixPath)):
        raise ValueError("path must be an absolute POSIX path")
    raw = str(value)
    if (not raw or raw == "/" or "\\" in raw or
            any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in raw)):
        raise ValueError("invalid POSIX path")
    parsed = PurePosixPath(raw)
    if (not parsed.is_absolute() or raw.startswith("//") or
            ".." in parsed.parts or str(parsed) != raw):
        raise ValueError("path must be absolute and normalized")
    return parsed


def _values(value: Mapping | None, allowed: frozenset[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("path options must be a mapping")
    if any(k not in allowed for k in value):
        raise ValueError("unknown path option")
    return {k: _path(v) for k, v in value.items()}


def resolve_paths(*, mode: str = "system", overrides: Mapping | None = None,
                  xdg: Mapping | None = None,
                  home: str | PurePosixPath | None = None) -> ExtScopePaths:
    """Resolve system defaults or caller-supplied user roots.

    System mode ignores home/XDG inputs entirely and never reads os.environ.
    User mode requires explicit runtime input; no /tmp fallback is provided.
    Explicit canonical overrides may live outside state_root. In user mode,
    canonical data follows XDG_DATA_HOME, independently of the state ledger.
    PurePosixPath values have already normalized redundant separators/dots;
    use strings when validation of the original spelling is required.
    """
    if mode not in ("system", "user"):
        raise ValueError("unknown path mode")
    selected = _values(overrides, _FIELDS)
    if mode == "system":
        state = selected.get("state_root", PurePosixPath("/var/lib/extscope"))
        defaults = dict(config_root=PurePosixPath("/etc/extscope"),
                        state_root=state, canonical_root=state / "canonical",
                        cache_root=PurePosixPath("/var/cache/extscope"),
                        runtime_root=PurePosixPath("/run/extscope"))
    else:
        roots = _values(xdg, _XDG)
        user_home = _path(home) if home is not None else None

        def base(key: str, suffix: str) -> PurePosixPath:
            if key in roots:
                return roots[key]
            if user_home is None:
                raise ValueError("user paths require explicit XDG roots or home")
            return user_home / suffix

        defaults = {}
        for field, key, suffix in (
            ("config_root", "XDG_CONFIG_HOME", ".config"),
            ("state_root", "XDG_STATE_HOME", ".local/state"),
            ("cache_root", "XDG_CACHE_HOME", ".cache"),
        ):
            if field not in selected:
                defaults[field] = base(key, suffix) / "extscope"
        if "canonical_root" not in selected:
            defaults["canonical_root"] = base("XDG_DATA_HOME", ".local/share") / "extscope/canonical"
        if "runtime_root" not in selected:
            if "XDG_RUNTIME_DIR" not in roots:
                raise ValueError("user runtime root must be explicit")
            defaults["runtime_root"] = roots["XDG_RUNTIME_DIR"] / "extscope"
    defaults.update(selected)
    return ExtScopePaths(**defaults)
