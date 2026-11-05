"""Bounded explicit UTF-8 file reads; no search paths or implicit schema loading."""
import os
import stat


def read_text_file(path: str | os.PathLike, *, max_bytes: int = 4 * 1024 * 1024) -> str:
    """Read one explicit regular file, bounded and without following its final link.

    Ancestor authorization remains the caller's responsibility, as with digest.
    """
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('max_bytes must be a positive integer')
    path = os.fspath(path)
    if (not isinstance(path, str) or not os.path.isabs(path) or
            os.path.normpath(path) != path or path.startswith('//')):
        raise ValueError('scope path must be absolute and normalized')
    def fingerprint(st):
        return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError('scope must be a bounded regular file')
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(fd, min(65536, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > max_bytes:
            raise ValueError('scope document too large')
        if (fingerprint(os.fstat(fd)) != fingerprint(before) or
                fingerprint(os.stat(path, follow_symlinks=False)) != fingerprint(before)):
            raise ValueError('scope changed while reading')
    finally:
        os.close(fd)
    try:
        source = data.decode('utf-8')
    except UnicodeError:
        raise ValueError('scope is not UTF-8') from None
    return source

