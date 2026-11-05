"""Module registry. Modules live outside this package and are loaded only when the
config lists their import name under modules.enable. dist never imports them statically.

Module protocol (duck-typed; a module package needs no import from extscope_dist).
`MODULES` is a dict {method name: object}; each object provides:

    path_fields: tuple[str]         config keys holding target paths; dist remaps and
                                    root-checks them before the module sees them
    irreversible: bool              True if rollback cannot undo it (e.g. a hook)
    validate(config, policy)        raise ValueError("code: detail") to refuse
    describe(config) -> str         redacted one-line text for plan output
    plan(config, policy) -> str     "noop" or "run" (read-only)
    apply(config, policy, ctx) -> dict   JSON record; ctx = {"backup_dir": str, "op": str}
    undo(record, force) -> None     raise on failure
    verify(record) -> list[str]     problems; empty means intact
"""
import importlib

from .errors import DistError
from .plan import METHODS

_REQUIRED = ("path_fields", "irreversible", "validate", "describe", "plan", "apply", "undo", "verify")


def load_registry(cfg):
    reg = {}
    for name in cfg.module_imports:
        try:
            pkg = importlib.import_module(name)
            mods = pkg.MODULES
        except Exception as exc:
            raise DistError("module_load_failed", f"{name}: {type(exc).__name__}: {exc}") from None
        for method, obj in mods.items():
            missing = [a for a in _REQUIRED if not hasattr(obj, a)]
            if missing:
                raise DistError("module_protocol", f"{name}.{method} lacks {missing}")
            if method in METHODS or method in reg:
                raise DistError("module_name_conflict", method)
            reg[method] = obj
    return reg
