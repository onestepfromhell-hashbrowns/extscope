"""Explicit discovery, resolution and scaffolding of scope declarations. Read-only:
`scaffold` returns text and never writes. Search roots are always caller-supplied."""
import fnmatch
import json
import os
import uuid

from .declaration import ScopeError
from .resolver import load_document

PATTERNS = ("*.extscope.json", "ext-default.json")
MAX_FILES = 5000


def discover(roots):
    """Absolute normalized paths of scope files under explicit roots (no symlink following)."""
    found = []
    for root in roots:
        if not os.path.isabs(root) or os.path.normpath(root) != root:
            raise ScopeError("scope root must be absolute and normalized")
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                if any(fnmatch.fnmatch(name, p) for p in PATTERNS):
                    found.append(os.path.join(base, name))
                    if len(found) > MAX_FILES:
                        raise ScopeError("too many scope files")
    return found


def collect(files, roots):
    """Parse every file; return (documents, problems). One bad file never hides the rest."""
    docs, problems = [], []
    for path in list(files) + discover(roots):
        try:
            docs.append(load_document(path))
        except (ScopeError, OSError, ValueError) as exc:
            problems.append({"path": path, "error": str(exc)})
    return docs, problems


def listing(docs, problems):
    rows = [{"uuid": d.uuid, "name": d.name, "enabled": d.enabled,
             "category": d.document.get("category", ""), "path": d.origin,
             "includes": list(d.include_scopes)} for d in docs]
    dup = sorted({k for k in [r["uuid"] for r in rows] + [r["name"] for r in rows]
                  if ([r["uuid"] for r in rows] + [r["name"] for r in rows]).count(k) > 1})
    return {"count": len(rows), "scopes": sorted(rows, key=lambda r: r["name"]),
            "ambiguous": dup, "problems": problems}


def resolve(docs, refs):
    out, missing = [], []
    for ref in refs:
        hit = [d for d in docs if ref in (d.uuid, d.name)]
        if len(hit) == 1:
            d = hit[0]
            out.append({"ref": ref, "uuid": d.uuid, "name": d.name, "path": d.origin,
                        "category": d.document.get("category", ""), "enabled": d.enabled})
        else:
            missing.append({"ref": ref, "matches": len(hit)})
    return {"resolved": out, "unresolved": missing}


def _covers(a, b):
    return a == b or b.startswith(a.rstrip(os.sep) + os.sep)


def _repo_root(path):
    probe = path
    while probe and probe != os.path.dirname(probe):
        if os.path.lexists(os.path.join(probe, ".git")):
            return probe
        probe = os.path.dirname(probe)
    return None


def where(docs, path):
    """Which scope items declare `path` (or something under / above it) as a source, the scopes
    that include them, and the git repo root holding that source. Read-only."""
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise ScopeError("path must be absolute and normalized")
    hits = []
    for d in docs:
        for it in d.include_items:
            src = it.get("src", "") if hasattr(it, "get") else ""
            if isinstance(src, str) and src and (_covers(src, path) or _covers(path, src)):
                hits.append({"scope_uuid": d.uuid, "scope_name": d.name, "scope_path": d.origin,
                             "category": d.document.get("category", ""), "src": src,
                             "exact": src == path or _covers(src, path),
                             "included_by": sorted(o.name for o in docs
                                                   if d.name in o.include_scopes or d.uuid in o.include_scopes),
                             "repo_root": _repo_root(src if os.path.isdir(src) else os.path.dirname(src))})
    return {"path": path, "matches": sorted(hits, key=lambda h: (not h["exact"], h["scope_name"]))}


LAYERS = {"system": "ext-system-", "biz": "biz-", "solution": "solution-", "preset": "preset-",
          "project": "proj-", "tooling": "tool-", "custom": "custom-"}


def scaffold(name, category="biz", description="", include=()):
    if category not in LAYERS:
        raise ScopeError("unknown category")
    doc = {"uuid": str(uuid.uuid4()), "name": name, "category": category,
           "description": description or name, "enabled": True,
           "include_scopes": list(include), "exclude_scopes": [], "include_items": [],
           "exclude_items": []}
    from .declaration import parse_document          # the template must itself be valid
    parse_document(json.dumps(doc))
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
