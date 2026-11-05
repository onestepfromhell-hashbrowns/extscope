"""Housekeeping for state_dir. Every apply leaves backups/<id>/ and manifests/<id>.json; nothing
else ever removes them. prune lists (default) or removes what is no longer needed:

  rolled back   manifests/<id>.rolledback.json and backups/<id>: the operation was undone.
  orphan        backups/<id> with no manifest: an apply that failed and rolled itself back.
  superseded    (--include-applied) an applied manifest whose every target was written again by a
                newer manifest. It can no longer be rolled back (drift), but its backups are the
                only copy of what was there before the first apply, so this is opt-in.

Manifests that are the latest record of a target are never touched: they are how dist knows it
owns that target. Nothing younger than --older-than is touched (orphans: at least MIN_ORPHAN_AGE,
so an apply still running is safe)."""
import json
import os
import shutil
import time

from .errors import DistError

MIN_ORPHAN_AGE = 3600.0
DAY = 86400.0


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(b, n)) for b, _d, fs in os.walk(path) for n in fs if os.path.isfile(os.path.join(b, n)))


def _targets(manifest):
    return {e["target"] for e in manifest["entries"] if not e.get("module")}


def plan(state_dir, older_than_days=30.0, include_applied=False, now=None):
    now = time.time() if now is None else now
    age = max(older_than_days, 0) * DAY
    mdir, bdir = os.path.join(state_dir, "manifests"), os.path.join(state_dir, "backups")
    names = sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []
    found, applied, ids = [], [], set()
    for name in names:
        path = os.path.join(mdir, name)
        if name.endswith(".rolledback.json"):
            ids.add(name[:-len(".rolledback.json")])
            if now - os.stat(path).st_ctime >= age:
                found.append(("rolled_back", name[:-len(".rolledback.json")], path))
        elif name.endswith(".json"):
            ids.add(name[:-5])
            try:
                with open(path, encoding="utf-8") as fh:
                    applied.append((name[:-5], path, json.load(fh)))
            except (OSError, ValueError) as exc:
                raise DistError("bad_manifest", f"{path}: {exc}") from None
    if include_applied:
        for i, (mid, path, m) in enumerate(applied):
            mine = _targets(m)
            later = set().union(*(_targets(n) for _a, _b, n in applied[i + 1:])) if applied[i + 1:] else set()
            modules = any(e.get("module") for e in m["entries"])
            if mine and not modules and mine <= later and now - os.stat(path).st_mtime >= age:
                found.append(("superseded", mid, path))
    for mid in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
        if mid not in ids and now - os.stat(os.path.join(bdir, mid)).st_mtime >= max(age, MIN_ORPHAN_AGE):
            found.append(("orphan", mid, None))
    rows = []
    for reason, mid, mpath in found:
        paths = [p for p in (mpath, os.path.join(bdir, mid)) if p and os.path.lexists(p)]
        rows.append({"id": mid, "reason": reason, "paths": paths, "bytes": sum(_size(p) for p in paths)})
    return rows


def run(state_dir, older_than_days=30.0, include_applied=False, apply=False):
    rows = plan(state_dir, older_than_days, include_applied)
    if apply:
        for row in rows:
            for p in row["paths"]:
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.unlink(p)
    return {"applied": apply, "state_dir": state_dir, "candidates": rows,
            "bytes": sum(r["bytes"] for r in rows), "count": len(rows)}
