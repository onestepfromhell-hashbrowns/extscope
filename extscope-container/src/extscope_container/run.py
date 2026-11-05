"""Apply container-side operations through a Transport. Order: mount checks, then one
tar (copy, symlink, md_block, env, key), then chmod/chown, then hooks."""
import hashlib
import io
import json
import os
import posixpath
import re
import tarfile
import time

from .errors import ContainerError
from .plan import _mode

OWNER = re.compile(r"[A-Za-z0-9_.-]+(:[A-Za-z0-9_.-]+)?\Z")


def _quote(v):
    return "'" + v.replace("'", "'\\''") + "'"


def _upsert(existing, block_id, text):
    o, c = f"<!-- block: {block_id} -->", f"<!-- /block: {block_id} -->"
    block = f"{o}\n{text.strip()}\n{c}\n"
    rx = re.compile(re.escape(o) + r".*?" + re.escape(c) + r"\n?", re.S)
    if rx.search(existing):
        return rx.sub(lambda _m: block, existing, count=1)
    sep = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block


def _norm(ti, mode=None):
    ti.mtime = ti.mtime or int(time.time())
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if mode is not None:
        ti.mode = mode
    return ti


def _add_tree(tf, src, arc):
    arc = arc.lstrip("/")
    if os.path.isdir(src) and not os.path.islink(src):
        ti = tarfile.TarInfo(arc); ti.type = tarfile.DIRTYPE; _norm(ti, os.stat(src).st_mode & 0o777); tf.addfile(ti)
        for cur, dirs, files in os.walk(src, followlinks=False):
            dirs.sort()
            for name in dirs + sorted(files):
                full = os.path.join(cur, name)
                rel = arc + "/" + os.path.relpath(full, src).replace(os.sep, "/")
                if os.path.islink(full):
                    ti = tarfile.TarInfo(rel); ti.type = tarfile.SYMTYPE; ti.linkname = os.readlink(full); tf.addfile(_norm(ti, 0o777))
                elif os.path.isdir(full):
                    ti = tarfile.TarInfo(rel); ti.type = tarfile.DIRTYPE; tf.addfile(_norm(ti, os.stat(full).st_mode & 0o777))
                else:
                    ti = tf.gettarinfo(full, rel); tf.addfile(_norm(ti), open(full, "rb"))
    else:
        ti = tf.gettarinfo(src, arc)
        with open(src, "rb") as fh:
            tf.addfile(_norm(ti), fh)


def _add_bytes(tf, arc, data, mode):
    ti = tarfile.TarInfo(arc.lstrip("/")); ti.size = len(data); ti.mtime = int(time.time())
    tf.addfile(_norm(ti, mode), io.BytesIO(data))


def _digest_local(path):
    h = hashlib.sha256()
    if os.path.isdir(path):
        for cur, dirs, files in os.walk(path):
            dirs.sort()
            for f in sorted(files):
                p = os.path.join(cur, f)
                h.update(os.path.relpath(p, path).encode() + b"\0" + (os.readlink(p).encode() if os.path.islink(p) else open(p, "rb").read()))
    else:
        h.update(open(path, "rb").read())
    return h.hexdigest()


def plan_hash(ops):
    safe = []
    for o in ops:
        o = dict(o)
        if o["op"] in ("copy", "key", "md_block"):
            if not os.path.exists(o["src"]):
                raise ContainerError("source_missing", o["src"])
            o["src_digest"] = _digest_local(o["src"])
        if o["op"] == "env":
            o["value"] = hashlib.sha256(o["value"].encode()).hexdigest()
        safe.append(o)
    return hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()


def resolve(ops, policy):
    """Map + check every container path. Mount destinations are checked too."""
    out = []
    for o in ops:
        o = dict(o)
        for f in ("dst", "target"):
            if f in o and not (o["op"] == "symlink" and f == "target"):
                o[f] = policy.check(policy.map(o[f]))
        if o["op"] == "symlink":
            o["dst"] = policy.check(policy.map(o["dst"]))
        if o["op"] == "hook":
            o["script"] = policy.check(policy.map(o["script"]))
        if o["op"] == "env":
            if not policy.env_dir:
                raise ContainerError("env_dir_required", "set env_dir in the config")
            o["dst"] = policy.check(policy.env_dir.rstrip("/") + "/" + o["key"] + ".sh")
        if o["op"] == "chown" and not OWNER.match(o["owner"]):
            raise ContainerError("bad_owner", o["owner"])
        out.append(o)
    return out


TREE_OPS = ("copy", "symlink", "md_block", "env", "key")   # ops that write a path in the container


def _retarget(tar_bytes, path):
    """Re-root a tar from Transport.read_tree so that it extracts at path again."""
    parent = posixpath.dirname(path.rstrip("/")).lstrip("/")
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as src, tarfile.open(fileobj=out, mode="w") as dst:
        for m in src.getmembers():
            m.name = posixpath.join(parent, m.name) if parent else m.name
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
    return out.getvalue()


def _rollback(tr, cid, journal, wrote):
    """Undo recorded state, newest first. Returns problems; an empty list means clean."""
    problems = []
    if wrote:
        for kind, path, prev in reversed(journal):
            if kind != "tree":
                continue
            try:
                tr.remove(cid, path)
                if prev is not None:
                    tr.put_tar(cid, _retarget(prev, path))
            except ContainerError as exc:
                problems.append(f"{path}: {exc}")
    for kind, path, prev in reversed(journal):
        if kind != "meta":
            continue
        mode, owner = prev
        rc, _, err = tr.exec(cid, ["chmod", mode, path])
        if rc:
            problems.append(f"chmod {path}: {err.strip()[-120:]}")
        if owner != "-":
            rc, _, err = tr.exec(cid, ["chown", owner, path])
            if rc:
                problems.append(f"chown {path}: {err.strip()[-120:]}")
    return problems


def _failed(tr, cid, journal, wrote, hooks_done, exc):
    code = exc.code if isinstance(exc, ContainerError) else "step_failed"
    why = exc.detail if isinstance(exc, ContainerError) and exc.detail else str(exc)
    problems = _rollback(tr, cid, journal, wrote)
    parts = [why]
    parts.append(f"rolled back {len(journal)} recorded item(s)" if not problems
                 else "ROLLBACK INCOMPLETE: " + "; ".join(problems))
    if hooks_done:
        parts.append("hooks already run are not undone: " + ", ".join(hooks_done))
    return ContainerError(code, " | ".join(parts))


def execute(ops, tr, cid, policy, *, dry_run=False, force=False, allow_hooks=False):
    ops = resolve(ops, policy)
    if not tr.running(cid):
        raise ContainerError("container_not_running", cid)
    have = tr.mounts(cid)
    missing = [o["dst"] for o in ops if o["op"] == "mount" and o["dst"] not in have]
    if missing:
        raise ContainerError("mounts_missing", ", ".join(missing) + "  (mounts can only be added when the container is created; see creation-plan)")
    if any(o["op"] == "hook" for o in ops) and not (allow_hooks and policy.hooks_allowed):
        raise ContainerError("hooks_not_allowed", "needs --allow-hooks and hooks_allowed=true in config")
    digest = plan_hash(ops)
    state = os.path.join(policy.state_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", cid) + ".json")
    prev = None
    if os.path.isfile(state):
        with open(state, encoding="utf-8") as fh:
            prev = json.load(fh).get("plan_hash")
    actions = [{"op": o["op"], "dst": o.get("dst") or o.get("target") or o.get("script")} for o in ops]
    if dry_run:
        return {"dry_run": True, "plan_sha256": digest, "unchanged": prev == digest, "actions": actions}
    if prev == digest and not force:
        return {"unchanged": True, "plan_sha256": digest, "actions": []}
    # Snapshot before the first write, so a failure part-way can be undone (hooks excepted).
    journal, wrote, hooks_done = [], False, []
    try:
        for dst in dict.fromkeys(o["dst"] for o in ops if o["op"] in TREE_OPS):
            journal.append(("tree", dst, tr.read_tree(cid, dst)))
        for o in ops:
            if o["op"] in ("chmod", "chown"):
                prev = tr.mode_owner(cid, o["target"])
                if prev is not None:
                    journal.append(("meta", o["target"], prev))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for o in ops:
                k = o["op"]
                if k == "copy":
                    _add_tree(tf, o["src"], o["dst"])
                elif k == "symlink":
                    ti = tarfile.TarInfo(o["dst"].lstrip("/")); ti.type = tarfile.SYMTYPE; ti.linkname = o["target"]; tf.addfile(_norm(ti, 0o777))
                elif k == "md_block":
                    old = tr.read_file(cid, o["dst"])
                    new = _upsert(old.decode() if old else "", o["block_id"], open(o["src"], encoding="utf-8").read())
                    _add_bytes(tf, o["dst"], new.encode(), 0o644)
                elif k == "env":
                    _add_bytes(tf, o["dst"], f"export {o['key']}={_quote(o['value'])}\n".encode(), 0o600)
                elif k == "key":
                    _add_bytes(tf, o["dst"], open(o["src"], "rb").read(), _mode(o.get("mode"), "0600"))
        if buf.tell() > 1024:
            wrote = True
            tr.put_tar(cid, buf.getvalue())
        for o in ops:
            if o["op"] == "chmod":
                rc, _, err = tr.exec(cid, ["chmod", format(_mode(o["mode"], None), "o"), o["target"]])
            elif o["op"] == "chown":
                rc, _, err = tr.exec(cid, ["chown", o["owner"], o["target"]])
            elif o["op"] == "hook":
                rc, _, err = tr.exec(cid, ["/bin/sh", o["script"], *o.get("args", [])])
            else:
                continue
            if rc:
                raise ContainerError("step_failed", f"{o['op']} {o.get('target') or o.get('script')}: {err.strip()[-200:]}")
            if o["op"] == "hook":
                hooks_done.append(o["script"])
            wrote = True
    except Exception as exc:
        raise _failed(tr, cid, journal, wrote, hooks_done, exc) from None
    os.makedirs(policy.state_dir, mode=0o700, exist_ok=True)
    with open(state + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"plan_hash": digest, "applied": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, fh)
    os.replace(state + ".tmp", state)
    return {"applied": len(ops), "plan_sha256": digest, "actions": actions}
