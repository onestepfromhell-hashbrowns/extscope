"""Sweep per-project directories under an explicit base. A project is an orphan when no
running container carries its name. Deletion is verified; sizes are measured, not assumed."""
import os
import re
import shutil

from .errors import ContainerError

SLUG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")
RESERVED = ("_shared",)


def safe_base(base):
    if not isinstance(base, str) or not os.path.isabs(base):
        raise ContainerError("base_not_absolute", str(base))
    if os.path.islink(base):
        raise ContainerError("base_is_symlink", base)
    real = os.path.realpath(base)
    if len([p for p in real.split(os.sep) if p]) < 2:
        raise ContainerError("base_too_shallow", real)
    if not os.path.isdir(real):
        raise ContainerError("base_missing", real)
    return real


def size(path):
    total = 0
    for cur, _, files in os.walk(path, followlinks=False):
        for f in files:
            try:
                total += os.lstat(os.path.join(cur, f)).st_size
            except OSError:
                pass
    return total


def delete_verified(path, boundary):
    real = os.path.realpath(path)
    if not real.startswith(boundary + os.sep):
        raise ContainerError("delete_outside_boundary", path)
    n = size(path) if os.path.isdir(path) and not os.path.islink(path) else 0
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    else:
        shutil.rmtree(path, ignore_errors=True)
    return (not os.path.lexists(path)), n


def gc(base, active, dry_run=False):
    root = safe_base(base)
    rep = {"scanned": 0, "cleaned": [], "planned": [], "skipped_active": [], "skipped_reserved": [],
           "reclaimed_bytes": 0, "errors": []}
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if name in RESERVED:
            (rep["skipped_reserved"] if os.path.isdir(p) and not os.path.islink(p) else rep["errors"]).append(
                name if os.path.isdir(p) and not os.path.islink(p) else {"project": name, "error": "invalid reserved entry"})
            continue
        if not SLUG.match(name):
            rep["errors"].append({"project": name, "error": "invalid name"}); continue
        if not os.path.isdir(p) and not os.path.islink(p):
            continue
        rep["scanned"] += 1
        if name in active:
            rep["skipped_active"].append(name); continue
        if dry_run:
            rep["planned"].append(name); rep["reclaimed_bytes"] += size(p); continue
        ok, n = delete_verified(p, root)
        if ok:
            rep["cleaned"].append(name); rep["reclaimed_bytes"] += n
        else:
            rep["errors"].append({"project": name, "error": "still exists after delete"})
    return rep


def purge(base, project):
    root = safe_base(base)
    if not SLUG.match(project) or project in RESERVED:
        raise ContainerError("bad_project", project)
    proj = os.path.join(root, project)
    target = os.path.join(proj, "ext") if os.path.isdir(os.path.join(proj, "ext")) else proj
    if not os.path.lexists(target):
        return 0
    ok, n = delete_verified(target, root)
    if not ok:
        raise ContainerError("purge_failed", target)
    if os.path.isdir(proj) and not os.listdir(proj):
        delete_verified(proj, root)
    return n
