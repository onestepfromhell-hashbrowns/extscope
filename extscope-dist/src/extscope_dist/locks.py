"""Cross-process target locks. Two writers on the same target (two md_block ids in one
AGENTS.md, a harness projection racing a manual apply) would otherwise read the same old
bytes, and the second write silently drops the first.

The lock is an flock on the target's parent directory: no lock files, and it is shared by
every process that writes there, whatever its config, state_dir or environment. Parents are
taken in sorted order and released together; a lock not obtained within the timeout is a
refusal. Local filesystems only (flock is not reliable over NFS)."""
import fcntl
import os
import time

from .errors import DistError

TIMEOUT = 30.0
POLL = 0.05


class TargetLocks:
    def __init__(self, targets, timeout=None):
        self.parents = sorted({os.path.dirname(os.path.normpath(t)) for t in targets if t})
        self.timeout = TIMEOUT if timeout is None else timeout
        self._fds = []

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        try:
            for parent in self.parents:
                os.makedirs(parent, exist_ok=True)       # already checked to be inside allowed_roots
                fd = os.open(os.path.realpath(parent), os.O_RDONLY | os.O_DIRECTORY)
                self._fds.append(fd)
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise DistError("lock_timeout", f"another extscope-dist run holds {parent}") from None
                        time.sleep(POLL)
        except BaseException:
            self._release()
            raise
        return self

    def _release(self):
        while self._fds:                      # last taken, first released
            fd = self._fds.pop()
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __exit__(self, *exc):
        self._release()
        return False
