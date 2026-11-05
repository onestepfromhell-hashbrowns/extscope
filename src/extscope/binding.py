"""Read-only plan/source receipts under explicit descriptor-anchored grants.

Unsigned receipts are consistency evidence, not publication approval. Grants,
owner/mount policy and stable generations are caller responsibilities. An open
root is a pinned directory capability: namespace replacement or hostile mutation
of granted trees is outside an atomic-snapshot guarantee.
"""
import hashlib
import json
import os
from pathlib import PurePosixPath
import stat
from .config.paths import _path
from .digest import hash_file_fd, hash_tree_fd
from .plan import FlattenPlan


class BindingMismatch(ValueError):
    """Current declarations, grants or payload bytes differ from the receipt."""


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(',', ':')).encode('ascii')


def _open_beneath(root_fd: int, parts: tuple[str, ...]) -> int:
    for p in parts:
        if not isinstance(p, str) or not p or p in ('.', '..') or ('/' in p) or ('\x00' in p):
            raise ValueError('invalid path component')
    if not parts:
        return os.dup(root_fd)
    cur_fd = os.dup(root_fd)
    try:
        base_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        for i, part in enumerate(parts):
            flags = base_flags if i == len(parts) - 1 else base_flags | os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=cur_fd)
            try:
                os.close(cur_fd)
            finally:
                cur_fd = next_fd
        st = os.fstat(cur_fd)
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            raise ValueError('file must be a regular file or directory')
        fd, cur_fd = (cur_fd, -1)
        return fd
    finally:
        if cur_fd >= 0:
            os.close(cur_fd)


class SourceRoots:
    """Explicit no-follow roots held open throughout source binding."""
    def __init__(self, paths):
        if isinstance(paths, (str, bytes, os.PathLike)):
            raise ValueError('allowed roots must be an explicit iterable')
        roots = tuple(str(_path(os.fspath(path))) for path in paths)
        if not roots or len(set(roots)) != len(roots):
            raise ValueError('allowed roots must be nonempty and unique')
        self.paths = tuple(sorted(roots))
        self._fds = {}

    def __enter__(self):
        if self._fds:
            raise ValueError('root grants are already open')
        try:
            for path in self.paths:
                base = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    self._fds[path] = _open_beneath(base, PurePosixPath(path).parts[1:])
                finally:
                    os.close(base)
                if not stat.S_ISDIR(os.fstat(self._fds[path]).st_mode):
                    raise ValueError('allowed root must be a real directory')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *ignored):
        first_error = None
        for fd in self._fds.values():
            try:
                os.close(fd)
            except OSError as error:
                if first_error is None:
                    first_error = error
        self._fds.clear()
        if first_error is not None:
            raise first_error

    def open_source(self, path):
        path = PurePosixPath(str(_path(os.fspath(path))))
        for root in sorted(self.paths, key=len, reverse=True):
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                continue
            if root not in self._fds:
                raise ValueError('root grants are not open')
            return _open_beneath(self._fds[root], parts)
        raise ValueError('source is outside allowed roots')


def bind_sources(plan: FlattenPlan, *, allowed_roots) -> dict:
    """Bind a genuine immutable plan to fresh source bytes; never grants execution."""
    if not isinstance(plan, FlattenPlan) or not plan.items:
        raise ValueError('binding requires a nonempty plan')
    manifest = plan.as_dict()
    claimed = manifest.pop('plan_sha256')
    if manifest.pop('schema') != 'extscope.plan.v1' or claimed != hashlib.sha256(
            canonical(['extscope.plan.v1', manifest])).hexdigest():
        raise ValueError('plan identity is inconsistent')
    sources = []
    with SourceRoots(allowed_roots) as roots:
        for path in sorted({item.item['src'] for item in plan.items}):
            fd = roots.open_source(path)
            try:
                observed = os.fstat(fd)
                if stat.S_ISDIR(observed.st_mode):
                    kind, algorithm, digest = 'directory', 'extscope.tree.v1', hash_tree_fd(fd)
                elif stat.S_ISREG(observed.st_mode):
                    kind, algorithm, digest = 'file', 'sha256', hash_file_fd(fd)
                else:
                    raise ValueError('source must be regular file or directory')
                if os.fstat(fd).st_mode != observed.st_mode:
                    raise RuntimeError('source mode changed while binding')
                sources.append({'source': path, 'kind': kind, 'algorithm': algorithm,
                                'mode': stat.S_IMODE(observed.st_mode), 'sha256': digest})
            finally:
                os.close(fd)
        receipt = {'schema': 'extscope.source-binding.v1', 'plan_sha256': plan.plan_sha256,
                   'allowed_roots': list(roots.paths), 'sources': sources}
    return {**receipt, 'binding_sha256': hashlib.sha256(canonical(receipt)).hexdigest()}


def verify_sources(plan: FlattenPlan, receipt: dict, *, allowed_roots) -> dict:
    """Rebind with caller-supplied grants; never derive authorization from receipt."""
    if not isinstance(receipt, dict):
        raise BindingMismatch('binding receipt must be an object')
    current = bind_sources(plan, allowed_roots=allowed_roots)
    if canonical(receipt) != canonical(current):
        raise BindingMismatch('binding receipt does not match current plan, grants or sources')
    return current
