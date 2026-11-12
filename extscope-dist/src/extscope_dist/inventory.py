# SPDX-License-Identifier: MIT
"""Read-only global view over many state dirs: which manifest wrote which target from which source.

Nothing here knows where state dirs live; the caller names the roots to scan. A root counts as a
state dir when it has `manifests/`, and its child directories are checked the same way."""
import json
import os

from .config import under


def _state_dirs(root, unreadable):
    found = [root] if os.path.isdir(os.path.join(root, "manifests")) else []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        unreadable.append(root)
        return found
    return found + [os.path.join(root, n) for n in names if os.path.isdir(os.path.join(root, n, "manifests"))]


def _manifests(state, unreadable):
    mdir = os.path.join(state, "manifests")
    try:
        names = sorted(os.listdir(mdir))
    except OSError:
        unreadable.append(state)
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        rolled = name.endswith(".rolledback.json")
        try:
            with open(os.path.join(mdir, name), encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("schema") == "extscope-dist.manifest.v1":
            yield m, rolled


def scan(roots, target_prefix="", src_prefix="", scope=""):
    """-> {"entries": [...], "state_dirs": [...], "unreadable": [...]}. Each row: manifest id, state_dir, status
    (applied | rolled_back | superseded), method, target, src, scope, src_digest.
    `superseded` = an applied manifest whose target was later written by a newer applied one."""
    rows, dirs, unreadable = [], [], []
    for root in roots:
        for state in _state_dirs(os.path.abspath(root), unreadable):
            if state in dirs:
                continue
            dirs.append(state)
            for m, rolled in _manifests(state, unreadable):
                for e in m.get("entries", []):
                    if e.get("module") or not e.get("target"):
                        continue
                    rows.append({"manifest": m["id"], "state_dir": state, "created": m.get("created", ""),
                                 "status": "rolled_back" if rolled else "applied", "method": e["method"],
                                 "target": e["target"], "src": e.get("src", ""), "scope": e.get("scope", ""),
                                 "src_digest": e.get("src_digest", "")})
    newest = {}
    for r in sorted((r for r in rows if r["status"] == "applied"), key=lambda r: (r["manifest"], r["state_dir"])):
        newest[r["target"]] = (r["manifest"], r["state_dir"])
    for r in rows:
        if r["status"] == "applied" and newest[r["target"]] != (r["manifest"], r["state_dir"]):
            r["status"] = "superseded"
    keep = [r for r in rows
            if (not target_prefix or under(r["target"], target_prefix))
            and (not src_prefix or (r["src"] and under(r["src"], src_prefix)))
            and (not scope or r["scope"] == scope)]
    keep.sort(key=lambda r: (r["target"], r["manifest"]))
    return {"state_dirs": dirs, "unreadable": sorted(set(unreadable)), "count": len(keep), "entries": keep}
