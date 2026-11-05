"""Canary lease and staging references. Every path is a parameter; nothing is built in.

Canary: a token in a tmpfs file must equal a token on disk. If the tmpfs copy is lost
(reboot, tmpfs wipe), the lease is invalid and the disk side must be purged."""
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import time
import uuid

from .errors import ContainerError

RUN_DIRS = {"bin": 0o755, "env": 0o755, "tokens": 0o700, "hooks": 0o755}


def _read(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def valid(tmpfs_path, disk_path):
    a, b = _read(tmpfs_path), _read(disk_path)
    return bool(a) and a == b


def _write_private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    os.replace(tmp, path)


def mint(tmpfs_path, disk_path, epoch=None):
    epoch = epoch or secrets.token_hex(32)
    _write_private(disk_path, epoch)
    _write_private(tmpfs_path, epoch)
    return epoch


def purge(root, exclude=()):
    """Empty `root` (keep it), verifying every item is gone. Refuses symlinked or shallow roots."""
    if os.path.islink(root) or len(os.path.normpath(root).strip(os.sep).split(os.sep)) < 2:
        raise ContainerError("unsafe_purge_root", root)
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
        return []
    left = []
    for name in os.listdir(root):
        if name in exclude:
            continue
        p = os.path.join(root, name)
        if os.path.islink(p) or os.path.isfile(p):
            os.unlink(p)
        else:
            shutil.rmtree(p, ignore_errors=True)
        if os.path.lexists(p):
            left.append(name)
    if left:
        raise ContainerError("purge_incomplete", ",".join(left))
    return []


def ensure_dirs(root):
    for sub, mode in RUN_DIRS.items():
        d = os.path.join(root, sub)
        os.makedirs(d, exist_ok=True)
        os.chmod(d, mode)


def ensure(tmpfs_path, disk_path, root):
    """Self-heal: if the lease is invalid, purge the disk side and mint a new one."""
    if valid(tmpfs_path, disk_path):
        ensure_dirs(root)
        return {"lease": "valid", "healed": False}
    purge(root, exclude=())
    ensure_dirs(root)
    mint(tmpfs_path, disk_path)
    return {"lease": "minted", "healed": True}


# ---- staging references: who is still using a staging directory ----------------------

@contextlib.contextmanager
def _lock(manifest, timeout=10.0):
    try:
        import fcntl
    except ImportError:
        raise ContainerError("unsupported_platform", "staging locks need POSIX fcntl") from None
    d = os.path.dirname(os.path.abspath(manifest))
    lock_dir = os.path.join(os.path.dirname(d), ".extscope-staging-locks")
    os.makedirs(lock_dir, exist_ok=True)
    key = hashlib.sha256(os.path.realpath(d).encode()).hexdigest()
    fd = os.open(os.path.join(lock_dir, key + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    end, got = time.time() + timeout, False
    try:
        while time.time() < end:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); got = True; break
            except OSError:
                time.sleep(0.05)
        if not got:
            raise ContainerError("lock_timeout", manifest)
        yield
    finally:
        os.close(fd)


def _load(manifest):
    try:
        with open(manifest, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ContainerError("manifest_unreadable", f"{manifest}: {exc}") from None


def _store(manifest, data):
    tmp = f"{manifest}.tmp.{uuid.uuid4().hex[:8]}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest)


def register(manifest, ref):
    with _lock(manifest):
        data = _load(manifest)
        refs = list(data.get("references") or [])
        key = (ref.get("kind", "unknown"), ref.get("target") or ref.get("container_id") or "")
        for i, r in enumerate(refs):
            if (r.get("kind", ""), r.get("target") or r.get("container_id") or "") == key:
                refs[i] = dict(ref); break
        else:
            refs.append(dict(ref))
        data["references"] = refs
        _store(manifest, data)
        return data


def release(manifest, key):
    with _lock(manifest):
        data = _load(manifest)
        refs = list(data.get("references") or [])
        keep = [r for r in refs if key not in (r.get("target"), r.get("container_id"), r.get("id"))]
        if len(keep) == len(refs):
            return False
        data["references"] = keep
        _store(manifest, data)
        return True


def references(manifest):
    return list(_load(manifest).get("references") or [])


def ref_active(ref, active_containers=None, staging_dir=None):
    kind = ref.get("kind", "")
    if kind == "host_symlink":
        t = ref.get("target", "")
        if not t or not os.path.lexists(t):
            return False, "TARGET_NOT_FOUND"
        if not os.path.islink(t):
            return False, "TARGET_NOT_SYMLINK"
        dest = os.path.realpath(t)
        want = ref.get("source_path")
        if want and dest != os.path.realpath(want):
            return False, "TARGET_MISMATCH"
        if not want and staging_dir:
            s = os.path.realpath(staging_dir)
            if os.path.commonpath((s, dest)) != s:
                return False, "TARGET_MISMATCH"
        return True, "ACTIVE"
    if kind == "container_lease":
        cid = ref.get("container_id", "")
        if not cid:
            return False, "CONTAINER_ID_MISSING"
        if active_containers is None:
            return True, "CONTAINER_ASSUMED_ACTIVE"
        return (cid in active_containers), ("CONTAINER_RUNNING" if cid in active_containers else "CONTAINER_STOPPED")
    if kind == "host_registration":
        return (not (ref.get("status") == "inactive" or ref.get("released"))), "REGISTERED"
    return True, f"UNKNOWN_KIND_RETAINED ({kind})"      # unknown references keep the directory alive


def status(staging_dir, active_containers=None):
    manifest = os.path.join(staging_dir, "composition_manifest.json")
    try:
        refs = references(manifest)
    except ContainerError as exc:
        return {"staging_dir": staging_dir, "status": "corrupted", "can_gc": False, "diagnostics": [str(exc)]}
    act, inact = [], []
    for r in refs:
        ok, why = ref_active(r, active_containers, staging_dir)
        (act if ok else inact).append({**r, "active_status": why})
    return {"staging_dir": staging_dir, "status": "in_use" if act else "orphaned", "can_gc": not act,
            "active_references": act, "inactive_references": inact}
