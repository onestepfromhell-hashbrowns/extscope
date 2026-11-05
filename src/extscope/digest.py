"""Content-based tree identity, independent of legacy metadata shortcuts.

Symlinks are recorded rather than followed. Relative names and their link text
are identity; mtimes and ownership are only used to detect observed changes.
This is not an atomic filesystem snapshot: callers must arrange quiescence or
immutable generation publication. Root ancestor aliases and authorization are
caller I/O-boundary concerns; this module is not a sandbox permission check.
"""

import hashlib
import json
import os
import stat


def _fingerprint(observed):
    return (observed.st_dev, observed.st_ino, observed.st_mode,
            observed.st_size, observed.st_mtime_ns, observed.st_ctime_ns)


def _read_file(parent_fd: int, name: str, observed: os.stat_result) -> str:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or _fingerprint(st) != _fingerprint(observed):
            raise RuntimeError("File changed or not a regular file")
        hasher = hashlib.sha256()
        while chunk := os.read(fd, 65536):
            hasher.update(chunk)
        if (_fingerprint(os.fstat(fd)) != _fingerprint(observed) or 
                _fingerprint(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != _fingerprint(observed)):
            raise RuntimeError("File changed during read")
        return hasher.hexdigest()
    finally:
        os.close(fd)


def hash_tree_fd(fd: int) -> str:
    """Hash a caller-owned directory descriptor without closing it.

    No path lookup is performed for the root. Callers retain root authorization
    and namespace/immutable-generation responsibilities.
    """
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        raise ValueError("tree descriptor must be a directory")
    records = []

    def unchanged(actual, observed):
        if _fingerprint(actual) != _fingerprint(observed):
            raise RuntimeError("tree changed while reading")

    def walk(fd, relative, observed):
        records.append(["d", relative, stat.S_IMODE(observed.st_mode)])
        for name in sorted(os.listdir(fd)):
            child = os.stat(name, dir_fd=fd, follow_symlinks=False)
            rel = relative + "/" + name if relative else name
            if stat.S_ISREG(child.st_mode):
                records.append(["f", rel, stat.S_IMODE(child.st_mode),
                                _read_file(fd, name, child)])
            elif stat.S_ISLNK(child.st_mode):
                target = os.readlink(name, dir_fd=fd)
                unchanged(os.stat(name, dir_fd=fd, follow_symlinks=False), child)
                records.append(["l", rel, target])
            elif stat.S_ISDIR(child.st_mode):
                child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=fd)
                try:
                    unchanged(os.fstat(child_fd), child)
                    walk(child_fd, rel, child)
                    unchanged(os.stat(name, dir_fd=fd, follow_symlinks=False), child)
                finally:
                    os.close(child_fd)
            else:
                raise ValueError("unsupported tree entry type")
        unchanged(os.fstat(fd), observed)

    observed = os.fstat(fd)
    walk(fd, "", observed)
    records.sort(key=lambda record: record[1])
    encoded = json.dumps(["extscope.tree.v1", records], ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def hash_file_fd(fd: int) -> str:
    """Hash a caller-owned regular file descriptor; preserve its original offset."""
    observed = os.fstat(fd)
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError("source descriptor must be a regular file")
    original = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while chunk := os.read(fd, 65536):
            digest.update(chunk)
        if _fingerprint(os.fstat(fd)) != _fingerprint(observed):
            raise RuntimeError("file changed during read")
        return digest.hexdigest()
    finally:
        os.lseek(fd, original, os.SEEK_SET)


def hash_tree(root: str | os.PathLike) -> str:
    """Hash all tree entries; failures propagate and special files are rejected."""
    path = os.fspath(root)
    if (not isinstance(path, str) or not os.path.isabs(path) or
            os.path.normpath(path) != path or path.startswith("//")):
        raise ValueError("root must be an absolute normalized directory path")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        observed = os.fstat(fd)
        result = hash_tree_fd(fd)
        if (_fingerprint(os.fstat(fd)) != _fingerprint(observed) or
                _fingerprint(os.stat(path, follow_symlinks=False)) != _fingerprint(observed)):
            raise RuntimeError("tree changed while reading")
        return result
    finally:
        os.close(fd)
