"""Plan -> checked actions -> staged atomic publish -> manifest -> rollback/verify.
Built-in methods: copy, symlink, md_block. Everything else is a module (see modreg)."""
import hashlib
import json
import os
import shutil
import time

from . import fsops, receipt as receipt_mod
from .config import under
from .errors import DistError
from .locks import TargetLocks
from .modreg import load_registry
from .plan import METHODS


def plan_digest(items):
    blob = json.dumps([i.key() for i in items], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def resolve(plan, cfg):
    """Map + check every target and verify every source. Raises before any write."""
    registry = load_registry(cfg)
    for it in plan.items:
        if it.method not in METHODS:
            mod = registry.get(it.method)
            if mod is None:
                raise DistError("module_not_enabled", it.method)
            it.mconfig = dict(it.config)
            pol = cfg.module_policy.get(it.method, {})
            for f in mod.path_fields:
                if f not in it.mconfig and isinstance(pol.get("default_" + f), str):
                    it.mconfig[f] = pol["default_" + f]
                if not isinstance(it.mconfig.get(f), str):
                    raise DistError("bad_plan", f"{it.method}: missing {f}")
                mapped = cfg.map_target(it.mconfig[f])
                cfg.check_target(mapped)
                it.mconfig[f] = mapped
            try:
                mod.validate(it.mconfig, cfg.module_policy.get(it.method, {}))
            except ValueError as exc:
                raise DistError("module_refused", f"{it.method}: {exc}") from None
            it.mapped = it.mconfig[mod.path_fields[0]] if mod.path_fields else ""
            continue
        it.mapped = cfg.map_target(it.target)
        cfg.check_target(it.mapped)
        if not os.path.lexists(it.src):
            raise DistError("source_missing", it.src)
        if it.sha256 and fsops.digest(it.src) != it.sha256:
            raise DistError("source_digest_mismatch", it.src)
        if it.method == "md_block" and not os.path.isfile(it.src):
            raise DistError("md_block_source_not_file", it.src)
    if plan.receipt is not None:
        receipt_mod.check(plan.items, plan.receipt, plan.core_plan_sha256)
    return registry


def _expected(it):
    if it.method == "symlink":
        return "symlink:" + it.src
    return fsops.digest(it.src)


def classify(it, prior, cfg, registry):
    """-> (action, reason): noop | create | replace | conflict | run."""
    if it.method not in METHODS:
        mod = registry[it.method]
        try:
            act = mod.plan(it.mconfig, cfg.module_policy.get(it.method, {}))
        except ValueError as exc:      # a refusal, not a conflict: --force must not override it
            return "refuse", str(exc)
        return ("noop" if act == "noop" else "run"), mod.describe(it.mconfig)
    cur = fsops.kind(it.mapped)
    if it.method == "md_block":
        if cur == "absent":
            return "create", ""
        if cur != "file":
            return "conflict", f"target is a {cur}"
        with open(it.mapped, encoding="utf-8") as fh:
            have = fsops.block_text(fh.read(), it.block_id)
        with open(it.src, encoding="utf-8") as fh:
            want = fsops.render_block(it.block_id, fh.read())
        return ("noop", "") if have == want else ("replace", "")
    if cur == "absent":
        return "create", ""
    if fsops.digest(it.mapped) == _expected(it):
        return "noop", ""
    if prior and prior.get(it.mapped) == fsops.digest(it.mapped):
        return "replace", "previously managed"
    return "conflict", f"existing {cur} not managed by extscope-dist"


def _managed(state_dir):
    """target -> last digest written by us, from stored manifests (newest wins)."""
    out = {}
    mdir = os.path.join(state_dir, "manifests")
    if not os.path.isdir(mdir):
        return out
    for name in sorted(os.listdir(mdir)):
        if name.endswith(".json") and not name.endswith(".rolledback.json"):
            with open(os.path.join(mdir, name), encoding="utf-8") as fh:
                for e in json.load(fh)["entries"]:
                    if e.get("written"):
                        out[e["target"]] = e["written"]
    return out


def gate(items, prefixes, allow_all):
    if allow_all:
        return items
    if not prefixes:
        raise DistError("scope_required", "pass --filter-prefix PATH (repeatable) or --all")
    return [i for i in items if any(under(i.mapped, p) for p in prefixes)]


def preview(plan, cfg, prefixes=(), allow_all=True, registry=None):
    registry = resolve(plan, cfg)
    prior = _managed(cfg.state_dir)
    rows = []
    for it in gate(plan.items, prefixes, allow_all):
        action, reason = classify(it, prior, cfg, registry)
        rows.append({"method": it.method, "src": it.src, "target": it.mapped, "action": action,
                     "reason": reason, **({"irreversible": True} if it.method not in METHODS and registry[it.method].irreversible else {})})
    return {"plan_sha256": plan_digest(plan.items), "actions": rows, "skipped": plan.skipped}


def _publish(it, stage_tag):
    tmp = fsops.tmp_sibling(it.mapped, stage_tag)
    os.makedirs(os.path.dirname(it.mapped), exist_ok=True)
    fsops.remove(tmp)
    if it.method == "symlink":
        os.symlink(it.src, tmp)
    elif os.path.isdir(it.src):
        shutil.copytree(it.src, tmp, symlinks=False)
    else:
        shutil.copy2(it.src, tmp)
    return tmp


def _apply_builtin(it, n, op, backup_dir):
    prev = {"kind": fsops.kind(it.mapped), "backup": ""}
    if it.method == "md_block":
        with open(it.src, encoding="utf-8") as fh:
            text = fh.read()
        old = ""
        if prev["kind"] == "file":
            with open(it.mapped, encoding="utf-8") as fh:
                old = fh.read()
            os.makedirs(backup_dir, exist_ok=True)
            prev["backup"] = os.path.join(backup_dir, f"{n}.bak")
            shutil.copy2(it.mapped, prev["backup"])
        new = fsops.upsert_block(old, it.block_id, text)
        os.makedirs(os.path.dirname(it.mapped), exist_ok=True)
        tmp = fsops.tmp_sibling(it.mapped, op)
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        os.replace(tmp, it.mapped)
    else:
        tmp = _publish(it, op)
        if prev["kind"] != "absent":
            prev["backup"] = fsops.stash(it.mapped, os.path.join(backup_dir, str(n)))
        try:
            os.replace(tmp, it.mapped)
        except OSError:
            if prev["backup"]:
                fsops.unstash(prev["backup"], it.mapped)
            fsops.remove(tmp)
            raise
    return {"target": it.mapped, "method": it.method, "src": it.src, "block_id": it.block_id,
            "prev": prev, "written": fsops.digest(it.mapped)}


def apply(plan, cfg, prefixes=(), allow_all=False, force=False):
    registry = resolve(plan, cfg)
    items = gate(plan.items, prefixes, allow_all)
    with TargetLocks([i.mapped for i in items]):
        return _apply_locked(plan, cfg, items, registry, force)


def _apply_locked(plan, cfg, items, registry, force):
    prior = _managed(cfg.state_dir)
    todo, conflicts, refused = [], [], []
    for it in items:
        action, reason = classify(it, prior, cfg, registry)
        if action == "refuse":
            refused.append(f"{it.method} {it.mapped or ''} ({reason})")
        elif action == "conflict" and not force:
            conflicts.append(f"{it.mapped or it.method} ({reason})")
        elif action != "noop":
            todo.append(it)
    if refused:
        raise DistError("module_refused", "; ".join(refused))
    if conflicts:
        raise DistError("conflict", "; ".join(conflicts) + "  [use --force to back up and replace]")
    op = time.strftime("%Y%m%dT%H%M%S") + f"{time.time_ns() % 10**9:09d}-" + plan_digest(items)[:8]
    while os.path.exists(os.path.join(cfg.state_dir, "manifests", op + ".json")) or os.path.exists(os.path.join(cfg.state_dir, "backups", op)):
        op += "x"
    backup_dir = os.path.join(cfg.state_dir, "backups", op)
    entries = []
    try:
        for n, it in enumerate(todo):
            if it.method in METHODS:
                entries.append(_apply_builtin(it, n, op, backup_dir))
                continue
            mod = registry[it.method]
            os.makedirs(backup_dir, mode=0o700, exist_ok=True)
            rec = mod.apply(it.mconfig, cfg.module_policy.get(it.method, {}),
                            {"backup_dir": os.path.join(backup_dir, f"m{n}"), "op": op})
            entries.append({"target": it.mapped, "method": it.method, "module": True,
                            "irreversible": bool(mod.irreversible), "record": rec})
    except Exception as exc:
        _undo(entries, registry)
        raise DistError("apply_failed_rolled_back", f"{type(exc).__name__}: {exc}") from None
    manifest = {"schema": "extscope-dist.manifest.v1", "id": op, "plan_sha256": plan_digest(plan.items),
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "entries": entries}
    mdir = os.path.join(cfg.state_dir, "manifests")
    os.makedirs(mdir, mode=0o700, exist_ok=True)
    path = os.path.join(mdir, op + ".json")
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    os.replace(path + ".tmp", path)
    return {"manifest": path, "applied": len(entries), "unchanged": len(items) - len(todo),
            "irreversible": [e["method"] for e in entries if e.get("irreversible")]}


def _undo(entries, registry, force=False):
    problems = []
    for e in reversed(entries):
        t = e["target"]
        try:
            if e.get("module"):
                if e.get("irreversible"):
                    continue
                registry[e["method"]].undo(e["record"], force)
                continue
            prev = e["prev"]
            if e["method"] == "md_block":
                if not force and fsops.digest(t) not in (e["written"], None):
                    problems.append(f"{t}: changed since apply")
                    continue
                if prev["kind"] == "absent":
                    fsops.remove(t)
                else:
                    shutil.copy2(prev["backup"], t)
                continue
            if not force and fsops.digest(t) not in (e["written"], None):
                problems.append(f"{t}: changed since apply")
                continue
            fsops.remove(t)
            if prev["kind"] != "absent":
                fsops.unstash(prev["backup"], t)
        except (OSError, ValueError, KeyError) as exc:
            problems.append(f"{t or e['method']}: {exc}")
    return problems


def _load_manifest(ref, cfg):
    path = ref if os.path.isfile(ref) else os.path.join(cfg.state_dir, "manifests", ref + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        raise DistError("manifest_not_found", ref) from None
    if m.get("schema") != "extscope-dist.manifest.v1":
        raise DistError("bad_manifest", path)
    return path, m


def rollback(ref, cfg, force=False):
    path, m = _load_manifest(ref, cfg)
    with TargetLocks([e.get("target") for e in m["entries"]]):
        return _rollback_locked(path, m, cfg, force)


def _rollback_locked(path, m, cfg, force):
    registry = load_registry(cfg)
    needed = {e["method"] for e in m["entries"] if e.get("module") and not e.get("irreversible")}
    if needed - set(registry):
        raise DistError("module_not_enabled", ",".join(sorted(needed - set(registry))))
    problems = _undo(m["entries"], registry, force)
    if problems:
        raise DistError("rollback_incomplete", "; ".join(problems) + "  [--force to override drift]")
    os.replace(path, path[:-5] + ".rolledback.json")
    return {"rolled_back": len(m["entries"]), "manifest": m["id"],
            "not_undoable": [e["method"] for e in m["entries"] if e.get("irreversible")]}


def verify(ref, cfg):
    _, m = _load_manifest(ref, cfg)
    registry = load_registry(cfg)
    bad = []
    for e in m["entries"]:
        if e.get("module"):
            if e["method"] not in registry:
                bad.append({"target": e["target"], "state": "module_not_enabled"})
            elif not e.get("irreversible"):
                bad += [{"target": e["target"], "state": p} for p in registry[e["method"]].verify(e["record"])]
        elif e["method"] == "md_block":
            if not os.path.isfile(e["target"]):
                bad.append({"target": e["target"], "state": "missing"})
            else:
                with open(e["target"], encoding="utf-8") as fh:
                    if fsops.block_text(fh.read(), e["block_id"]) is None:
                        bad.append({"target": e["target"], "state": "block_missing"})
        elif fsops.digest(e["target"]) != e["written"]:
            bad.append({"target": e["target"], "state": "drift" if os.path.lexists(e["target"]) else "missing"})
    return {"manifest": m["id"], "ok": not bad, "problems": bad}
