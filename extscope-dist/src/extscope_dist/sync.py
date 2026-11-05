"""Reverse direction: compare applied targets with their sources, write edits back, locate origin.
Only `copy` and `md_block` entries are reversible; symlinks already are their source and module
entries are synthesised, so they are listed as not_reversible. Nothing here ever commits."""
import difflib
import os
import shutil
import subprocess

from . import fsops
from .config import under
from .engine import _load_manifest
from .errors import DistError

MAX_DIFF_BYTES = 1 << 20


def _text(path):
    try:
        if os.path.getsize(path) > MAX_DIFF_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeError):
        return None


def _files(root):
    out = {}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(base, n)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out


def _udiff(a_path, b_path, label):
    a, b = (_text(a_path) if a_path else ""), (_text(b_path) if b_path else "")
    if a is None or b is None:
        return "binary or oversized: " + label
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                        "source/" + label, "target/" + label))


def _block_inner(content, block_id):
    block = fsops.block_text(content, block_id)
    if block is None:
        return None
    lines = block.splitlines(True)
    return "".join(lines[1:-1])


def _row(e, state, **extra):
    return {"target": e["target"], "src": e.get("src", ""), "state": state, **extra}


def _entry_state(e):
    """-> (state, detail). state: in_sync | target_drift | source_changed | both_changed | missing."""
    t, s = e["target"], e["src"]
    if not os.path.lexists(t):
        return "missing", {}
    if not os.path.lexists(s):
        return "source_missing", {}
    if e["method"] == "md_block":
        with open(t, encoding="utf-8") as fh:
            inner = _block_inner(fh.read(), e["block_id"])
        if inner is None:
            return "missing", {}
        with open(s, encoding="utf-8") as fh:
            want = fh.read().strip()
        return ("in_sync" if inner.strip() == want else "target_drift"), {}
    target_moved = fsops.digest(t) != e["written"]
    source_moved = fsops.digest(s) != e["written"]
    if not target_moved and not source_moved:
        return "in_sync", {}
    if target_moved and source_moved:
        # both differ from what was written; identical content is still in sync
        return ("in_sync" if fsops.digest(t) == fsops.digest(s) else "both_changed"), {}
    return ("target_drift" if target_moved else "source_changed"), {}


def diff(ref, cfg):
    _, m = _load_manifest(ref, cfg)
    rows = []
    for e in m["entries"]:
        if e.get("module") or e["method"] == "symlink":
            rows.append({"target": e["target"], "state": "not_reversible", "method": e["method"]})
            continue
        state, _ = _entry_state(e)
        row = _row(e, state, method=e["method"])
        if state in ("target_drift", "both_changed"):
            if e["method"] == "md_block":
                with open(e["target"], encoding="utf-8") as fh:
                    inner = _block_inner(fh.read(), e["block_id"])
                with open(e["src"], encoding="utf-8") as fh:
                    src_text = fh.read()
                row["diff"] = "".join(difflib.unified_diff(src_text.strip().splitlines(True), inner.strip().splitlines(True),
                                                           "source/" + os.path.basename(e["src"]), "target/block:" + e["block_id"]))
            elif os.path.isdir(e["target"]):
                tf, sf = _files(e["target"]), _files(e["src"]) if os.path.isdir(e["src"]) else {}
                row["changed"] = sorted(k for k in tf if k in sf and fsops.digest(tf[k]) != fsops.digest(sf[k]))
                row["added_in_target"] = sorted(set(tf) - set(sf))
                row["removed_in_target"] = sorted(set(sf) - set(tf))
                row["diff"] = "".join(_udiff(sf[k], tf[k], k) for k in row["changed"])
            else:
                row["diff"] = _udiff(e["src"], e["target"], os.path.basename(e["src"]))
        rows.append(row)
    drift = [r for r in rows if r["state"] not in ("in_sync", "not_reversible")]
    return {"manifest": m["id"], "ok": not drift, "entries": rows}


def _source_ok(src, cfg):
    if not cfg.source_roots:
        raise DistError("no_source_roots", "configure source_roots; pullback is fail-closed")
    parent = os.path.realpath(os.path.dirname(src))
    cand = os.path.join(parent, os.path.basename(src))
    if not any(under(src, r) or under(cand, os.path.realpath(r)) for r in cfg.source_roots):
        raise DistError("source_outside_source_roots", src)
    if os.path.islink(src):
        raise DistError("source_is_symlink", src)


def _pull_dir(e, apply):
    tf, sf = _files(e["target"]), _files(e["src"])
    changed = [k for k in tf if k in sf and fsops.digest(tf[k]) != fsops.digest(sf[k])]
    added = sorted(set(tf) - set(sf))
    if apply:
        for k in changed + added:
            dst = os.path.join(e["src"], k)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(tf[k], dst)
    return {"updated": sorted(changed), "added": added, "left_alone": sorted(set(sf) - set(tf))}


def pullback(ref, cfg, apply=False):
    """Write target edits back to their sources. Refuses entries whose source also moved.
    Deleted files are reported (`left_alone`) but never removed from the source."""
    _, m = _load_manifest(ref, cfg)
    rows, refused = [], []
    for e in m["entries"]:
        if e.get("module") or e["method"] == "symlink":
            rows.append({"target": e["target"], "state": "not_reversible", "method": e["method"]})
            continue
        state, _ = _entry_state(e)
        if state != "target_drift":
            rows.append(_row(e, state if state != "in_sync" else "unchanged"))
            if state in ("source_changed", "both_changed", "source_missing", "missing"):
                refused.append(f"{e['target']} ({state})")
            continue
        _source_ok(e["src"], cfg)
        if e["method"] == "md_block":
            with open(e["target"], encoding="utf-8") as fh:
                inner = _block_inner(fh.read(), e["block_id"])
            if apply:
                with open(e["src"], "w", encoding="utf-8", newline="") as fh:
                    fh.write(inner.strip() + "\n")
            rows.append(_row(e, "pulled" if apply else "would_pull"))
        elif os.path.isdir(e["target"]):
            if not os.path.isdir(e["src"]):
                refused.append(f"{e['target']} (source is not a directory)")
                continue
            rows.append(_row(e, "pulled" if apply else "would_pull", **_pull_dir(e, apply)))
        else:
            if apply:
                tmp = fsops.tmp_sibling(e["src"], "pullback")
                shutil.copy2(e["target"], tmp)
                os.replace(tmp, e["src"])
            rows.append(_row(e, "pulled" if apply else "would_pull"))
    return {"manifest": m["id"], "ok": not refused, "applied": apply, "entries": rows, "refused": refused,
            "next": "review `git diff` in the source repo, commit, then bind and apply again"}


def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def repo_of(path):
    probe = path if os.path.isdir(path) else os.path.dirname(path)
    while probe and not os.path.lexists(os.path.join(probe, ".git")):
        up = os.path.dirname(probe)
        if up == probe:
            return {"root": None}
        probe = up
    return {"root": probe, "branch": _git(probe, "rev-parse", "--abbrev-ref", "HEAD"),
            "commit": _git(probe, "rev-parse", "HEAD"),
            "dirty": bool(_git(probe, "status", "--porcelain", "--", path))}


def origin(path, cfg):
    """Newest applied manifest entry whose target equals or contains `path`."""
    if not os.path.isabs(path):
        raise DistError("path_not_absolute", path)
    path = os.path.normpath(path)
    mdir = os.path.join(cfg.state_dir, "manifests")
    names = sorted((n for n in os.listdir(mdir) if n.endswith(".json") and not n.endswith(".rolledback.json")),
                   reverse=True) if os.path.isdir(mdir) else []
    for name in names:
        _, m = _load_manifest(os.path.join(mdir, name), cfg)
        for e in reversed(m["entries"]):
            if e.get("module") or not e.get("src") or not under(path, e["target"]):
                continue
            rel = os.path.relpath(path, e["target"])
            src = e["src"] if rel == "." else os.path.join(e["src"], rel)
            return {"manifest": m["id"], "method": e["method"], "target": e["target"], "src": src,
                    "repo": repo_of(src),
                    "next": "extscope where --scope-dir <scopes dir> " + src}
    raise DistError("origin_not_found", path)
