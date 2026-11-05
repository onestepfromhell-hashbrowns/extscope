"""Plan input. Native schema `extscope-dist.plan.v1`, plus a pure-data reader for the
JSON that `extscope plan` prints (`extscope.plan-preview.v1`). No code is shared with
extscope; only the JSON shape is read."""
import json
import os
from dataclasses import dataclass, field

from .config import expand, norm_abs
from .errors import DistError

METHODS = ("copy", "symlink", "md_block")
NATIVE = "extscope-dist.plan.v1"
PREVIEW = "extscope.plan-preview.v1"


@dataclass
class Item:
    method: str
    src: str
    target: str
    block_id: str = ""
    sha256: str = ""            # optional expected source digest (file or tree)
    mapped: str = ""            # filled by the engine after remap
    config: dict = field(default_factory=dict)   # module items only
    mconfig: dict = field(default_factory=dict)  # config after remap (engine)
    note: str = ""

    def key(self):
        return {"method": self.method, "src": self.src, "target": self.target,
                "block_id": self.block_id, "sha256": self.sha256, "config": self.config}


@dataclass
class Plan:
    items: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    core_plan_sha256: str = ""      # preview schema only: the core's own plan hash
    receipt: dict | None = None     # set by the CLI when --receipt is given


def _expand_cfg(value, variables):
    if isinstance(value, str):
        return expand(value, variables)
    if isinstance(value, list):
        return [_expand_cfg(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _expand_cfg(v, variables) for k, v in value.items()}
    return value


def _native(doc, variables):
    items = []
    for raw in doc.get("items", []):
        if not isinstance(raw, dict) or set(raw) - {"method", "src", "target", "block_id", "sha256", "config"}:
            raise DistError("bad_plan", "item fields")
        method = raw.get("method")
        if not isinstance(method, str) or not method:
            raise DistError("bad_plan", f"method {method!r}")
        if method in METHODS:
            if "config" in raw:
                raise DistError("bad_plan", f"{method} takes no config")
            if method == "md_block" and not raw.get("block_id"):
                raise DistError("bad_plan", "md_block needs block_id")
            items.append(Item(method, norm_abs(expand(raw["src"], variables), "src"),
                              norm_abs(expand(raw["target"], variables), "target"),
                              raw.get("block_id", ""), raw.get("sha256", "")))
        else:   # a module method; the module validates its own config at resolve time
            cfg = raw.get("config")
            if not isinstance(cfg, dict) or set(raw) - {"method", "config"}:
                raise DistError("bad_plan", f"module item {method!r} needs only method + config")
            items.append(Item(method, "", "", config=_expand_cfg(cfg, variables)))
    return Plan(items)


def _preview(doc, variables):
    plan = Plan()
    for entry in doc.get("items", []):
        it = entry.get("item", {})
        if it.get("enabled") is False:
            continue
        src, dst, steps = it.get("src"), it.get("dst_fhs"), it.get("pipeline")
        if not steps:
            method = "symlink" if it.get("type") == "symlink" else "copy"
            plan.items.append(Item(method, norm_abs(expand(src, variables), "src"),
                                   norm_abs(expand(dst, variables), "target")))
            continue
        for step in steps:
            mod, cfg = step.get("module"), step.get("config", {})
            if mod == "copy_inject":
                plan.items.append(Item("copy", norm_abs(expand(src, variables), "src"),
                                       norm_abs(expand(cfg["dst"], variables), "target")))
            elif mod == "symlink":
                plan.items.append(Item("symlink", norm_abs(expand(cfg["src"], variables), "src"),
                                       norm_abs(expand(cfg["target"], variables), "target")))
            elif mod == "md_block_inject":
                plan.items.append(Item("md_block", norm_abs(expand(cfg["src"], variables), "src"),
                                       norm_abs(expand(cfg["dst"], variables), "target"), cfg["block_id"]))
            elif mod == "volume_mount":
                plan.skipped.append({"module": mod, "reason": "container-only"})
            elif isinstance(mod, str) and mod:
                cfg = dict(cfg)
                if mod == "key_inject" and cfg.get("sensitivity") == "secret" and cfg.get("persist") is False and cfg.get("tmpfs_dst"):
                    cfg["dst"] = cfg["tmpfs_dst"]      # legacy: non-persistent secrets go to the tmpfs path only
                cfg = {k: v for k, v in cfg.items() if k not in ("tmpfs_dst", "persist")}
                plan.items.append(Item(mod, "", "", config=_expand_cfg(cfg, variables)))
            else:
                raise DistError("bad_plan", "pipeline step without module")
    return plan


def load_plan(path, variables):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DistError("bad_plan", f"{path}: {exc}") from None
    schema = doc.get("schema") if isinstance(doc, dict) else None
    if schema == NATIVE:
        plan = _native(doc, variables)
    elif schema == PREVIEW:
        plan = _preview(doc, variables)
        plan.core_plan_sha256 = doc.get("plan_sha256") or ""
    else:
        raise DistError("bad_plan", f"schema must be {NATIVE} or {PREVIEW}")
    seen = set()
    for i in plan.items:
        if i.method in METHODS and (i.method, i.target, i.block_id) in seen:
            raise DistError("duplicate_target", i.target)
        seen.add((i.method, i.target, i.block_id))
    return plan
