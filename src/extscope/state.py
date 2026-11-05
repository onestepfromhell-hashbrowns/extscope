"""Explicit target-state read grants, opaque bytes and revalidation; no writes.

These are consistency snapshots, not filesystem transactions or write approval.
Later executors must reopen roots and repeat all CAS/ownership checks at commit.
"""
import hashlib
import os
from pathlib import PurePosixPath
import stat
from .binding import SourceRoots
from .plugin_api import TargetExpectation, TargetState, validate_path

MAX_BYTES = 4 * 1024 * 1024


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _capture(roots, target):
    try:
        fd = roots.open_source(target)
    except FileNotFoundError:
        return TargetState(TargetExpectation(target, False, None, None, None), None)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_size > MAX_BYTES:
            raise ValueError('invalid target state file')
        chunks, length = [], 0
        while True:
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > MAX_BYTES:
                raise ValueError('target state exceeds limit')
        if _identity(os.fstat(fd)) != _identity(before):
            raise ValueError('target state changed during read')
        content = b''.join(chunks)
        expected = TargetExpectation(target, True, hashlib.sha256(content).hexdigest(),
                                     stat.S_IMODE(before.st_mode), _identity(before))
        return TargetState(expected, content)
    finally:
        os.close(fd)


class TargetStates:
    """Independent explicit read-state capability; target prefixes do not grant reads."""
    def __init__(self, paths, targets):
        self._roots = SourceRoots(paths)
        self._targets = tuple(str(validate_path(p)) for p in targets)
        self._snapshots = {}
        self._open = False

    @property
    def paths(self):
        return self._roots.paths

    def __enter__(self):
        self._roots.__enter__()
        self._open = True
        return self

    def __exit__(self, *args):
        self._open = False
        self._snapshots.clear()
        return self._roots.__exit__(*args)

    def read(self, target):
        target = str(validate_path(target))
        if not self._open or not any(PurePosixPath(target).is_relative_to(root) and target != root
                                     for root in self._targets):
            raise ValueError('target is outside active state capability')
        if target not in self._snapshots:
            self._snapshots[target] = _capture(self._roots, target)
        return self._snapshots[target]

    def validate_expected(self, expected):
        if not isinstance(expected, TargetExpectation):
            raise ValueError('invalid target expectation')
        captured = self._snapshots.get(expected.target)
        if captured is None or expected != captured.expected:
            raise ValueError('expectation was not captured by this state capability')

    def revalidate(self):
        if not self._open:
            raise ValueError('state capability is closed')
        # Fresh grants detect a lexical root being renamed/replaced since capture.
        with SourceRoots(self.paths) as roots:
            for target, snapshot in self._snapshots.items():
                if _capture(roots, target) != snapshot:
                    raise ValueError('target state changed after capture')
