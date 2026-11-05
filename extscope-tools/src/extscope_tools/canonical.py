"""Canonical staging check. A release is a staging copy: updating a source must not change what
applied environments see until a new release is promoted. So a release holds copies, not links
back to sources, except very large files that DELIVERY.json declares under `referenced`
({relative path: source path}). Read-only; reports violations."""
import hashlib
import json
import os


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def check_release(release):
    out = []
    try:
        with open(os.path.join(release, "DELIVERY.json"), encoding="utf-8") as fh:
            delivery = json.load(fh)
        files, referenced = delivery["files"], delivery.get("referenced", {})
        if not isinstance(files, dict) or not isinstance(referenced, dict):
            raise ValueError("files and referenced must be objects")
    except (OSError, ValueError, KeyError) as exc:
        return [{"path": "DELIVERY.json", "rule": "delivery_unreadable", "message": type(exc).__name__}]
    seen = set()
    for base, dirs, names in os.walk(release):
        dirs.sort()
        for name in sorted(names + [d for d in dirs if os.path.islink(os.path.join(base, d))]):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, release)
            if rel == "DELIVERY.json":
                continue
            seen.add(rel)
            if os.path.islink(full):
                if rel in referenced:
                    if os.readlink(full) != referenced[rel]:
                        out.append({"path": rel, "rule": "referenced_target_changed", "message": "link differs from DELIVERY.json"})
                elif not _inside(full, release):
                    out.append({"path": rel, "rule": "escapes_release", "message": "links outside the release: a source update would reach applied environments"})
                continue
            if rel not in files:
                out.append({"path": rel, "rule": "unlisted_file", "message": "not in DELIVERY.json"})
            elif _sha(full) != files[rel]:
                out.append({"path": rel, "rule": "digest_mismatch", "message": "differs from DELIVERY.json"})
    for rel in sorted(set(files) - seen):
        out.append({"path": rel, "rule": "missing_file", "message": "listed in DELIVERY.json, absent"})
    for rel in sorted(set(referenced) - seen):
        out.append({"path": rel, "rule": "referenced_missing", "message": "declared but not a link in the release"})
    return out


def check_links(directory, canonical_root):
    """Projected links must go through canonical, never straight to a source."""
    out = []
    for name in sorted(os.listdir(directory)):
        full = os.path.join(directory, name)
        if os.path.islink(full) and not os.readlink(full).startswith(canonical_root.rstrip(os.sep) + os.sep):
            out.append({"path": full, "rule": "direct_to_source", "message": f"-> {os.readlink(full)}"})
    return out
