"""Optional source-binding check. `extscope bind` records the exact bytes it approved
(`extscope.source-binding.v1`); with --receipt, apply refuses any source that no longer
matches. Only the JSON shape is read; the tree algorithm `extscope.tree.v1` is
re-implemented here (no import from extscope)."""
import hashlib
import json
import os
import stat

from .errors import DistError
from .fsops import digest as file_digest

SCHEMA = "extscope.source-binding.v1"


def tree_v1(root):
    """sha256 of the sorted [kind, relpath, mode, ...] records, symlinks recorded not followed."""
    records = []

    def walk(path, rel):
        records.append(["d", rel, stat.S_IMODE(os.lstat(path).st_mode)])
        for name in sorted(os.listdir(path)):
            full, sub = os.path.join(path, name), (rel + "/" + name if rel else name)
            st = os.lstat(full)
            if stat.S_ISREG(st.st_mode):
                records.append(["f", sub, stat.S_IMODE(st.st_mode), file_digest(full)])
            elif stat.S_ISLNK(st.st_mode):
                records.append(["l", sub, os.readlink(full)])
            elif stat.S_ISDIR(st.st_mode):
                walk(full, sub)
            else:
                raise DistError("receipt_unsupported_entry", full)

    walk(root, "")
    records.sort(key=lambda r: r[1])
    blob = json.dumps(["extscope.tree.v1", records], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(blob).hexdigest()


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DistError("bad_receipt", f"{path}: {exc}") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("sources"), list):
        raise DistError("bad_receipt", f"schema must be {SCHEMA}")
    return doc


def _has_symlink(root):
    return any(os.path.islink(os.path.join(b, n)) for b, ds, fs in os.walk(root) for n in ds + fs)


def check(items, receipt, core_plan_sha256=""):
    """Every copy / md_block source must be bound and unchanged; symlink sources are
    checked only when the receipt lists them. Module items carry no source."""
    if core_plan_sha256 and receipt.get("plan_sha256") != core_plan_sha256:
        raise DistError("receipt_plan_mismatch", "receipt was bound to a different plan")
    bound = {s.get("source"): s for s in receipt["sources"] if isinstance(s, dict)}
    for it in items:
        if it.method not in ("copy", "symlink", "md_block"):
            continue
        rec = bound.get(it.src)
        if rec is None:
            if it.method == "symlink":
                continue
            raise DistError("source_not_bound", it.src)
        if os.path.isdir(it.src) and not os.path.islink(it.src):
            if rec.get("algorithm") != "extscope.tree.v1":
                raise DistError("receipt_source_mismatch", it.src)
            if _has_symlink(it.src):
                raise DistError("receipt_tree_has_symlink", it.src)   # copy would follow it; the hash does not
            now = tree_v1(it.src)
        else:
            if rec.get("algorithm") != "sha256":
                raise DistError("receipt_source_mismatch", it.src)
            now = file_digest(it.src)
        if now != rec.get("sha256"):
            raise DistError("receipt_source_mismatch", it.src)
