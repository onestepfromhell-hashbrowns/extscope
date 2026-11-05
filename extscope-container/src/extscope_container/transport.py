"""Transports. Podman is the real one; DirTransport treats a host directory as a container
root (tests, offline staging). Both expose the same small surface."""
import io
import json
import os
import shutil
import subprocess
import tarfile

from .errors import ContainerError


class PodmanTransport:
    def __init__(self, binary="podman", timeout=120):
        self.bin, self.timeout = binary, timeout

    def _run(self, *args, stdin=None):
        try:
            return subprocess.run([self.bin, *args], input=stdin, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerError("transport_failed", f"{self.bin}: {exc}") from None

    def running(self, cid):
        r = self._run("inspect", "-f", "{{.State.Running}}", cid)
        return r.returncode == 0 and r.stdout.decode().strip() == "true"

    def running_names(self):
        r = self._run("ps", "--format", "{{.Names}}")
        if r.returncode:
            raise ContainerError("transport_failed", "podman ps: " + r.stderr.decode()[-200:])
        return {l.strip() for l in r.stdout.decode().splitlines() if l.strip()}

    def mounts(self, cid):
        r = self._run("inspect", "-f", "{{json .Mounts}}", cid)
        if r.returncode:
            raise ContainerError("transport_failed", "podman inspect: " + r.stderr.decode()[-200:])
        return {m["Destination"]: m for m in json.loads(r.stdout or b"[]")}

    def read_file(self, cid, path):
        r = self._run("exec", cid, "cat", "--", path)
        return r.stdout if r.returncode == 0 else None

    def put_tar(self, cid, data):
        r = self._run("cp", "-", f"{cid}:/", stdin=data)
        if r.returncode:
            raise ContainerError("transport_failed", "podman cp: " + r.stderr.decode()[-300:])

    def exec(self, cid, argv):
        r = self._run("exec", cid, *argv)
        return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")

    # --- snapshot / undo primitives used by run.execute
    def exists(self, cid, path):
        return self.exec(cid, ["sh", "-c", 'test -e "$1" || test -L "$1"', "sh", path])[0] == 0

    def read_tree(self, cid, path):
        """Archive of path (modes, owners and symlinks kept), or None when path is absent."""
        if not self.exists(cid, path):
            return None
        r = self._run("cp", "-a", f"{cid}:{path}", "-")
        if r.returncode:
            raise ContainerError("transport_failed", "podman cp: " + r.stderr.decode(errors="replace")[-300:])
        return r.stdout

    def remove(self, cid, path):
        rc, _, err = self.exec(cid, ["rm", "-rf", "--", path])
        if rc:
            raise ContainerError("transport_failed", f"rm {path}: {err.strip()[-200:]}")

    def mode_owner(self, cid, path):
        """(octal mode, owner) of path, or None when it cannot be read."""
        rc, out, _ = self.exec(cid, ["stat", "-c", "%a %U:%G", path])
        parts = out.split()
        return (parts[0], parts[1]) if rc == 0 and len(parts) == 2 else None


class DirTransport:
    """Directory-as-root. exec supports only chmod/chown (path-prefixed); anything else is refused."""

    def __init__(self, root, mounts=()):
        self.root, self._mounts = os.path.realpath(root), list(mounts)

    def _p(self, path):
        full = os.path.normpath(os.path.join(self.root, path.lstrip("/")))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ContainerError("path_escape", path)
        return full

    def running(self, cid):
        return True

    def running_names(self):
        return set()

    def mounts(self, cid):
        return {m: {"Destination": m} for m in self._mounts}

    def read_file(self, cid, path):
        p = self._p(path)
        if os.path.isfile(p) and not os.path.islink(p):
            with open(p, "rb") as fh:
                return fh.read()
        return None

    def put_tar(self, cid, data):
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            for m in tf.getmembers():
                dest = self._p(m.name)
                if os.path.lexists(dest) and not m.isdir():
                    if os.path.isdir(dest) and not os.path.islink(dest):
                        shutil.rmtree(dest)
                    else:
                        os.unlink(dest)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if m.isdir():
                    os.makedirs(dest, exist_ok=True)
                    os.chmod(dest, m.mode)
                elif m.issym():
                    os.symlink(m.linkname, dest)
                else:
                    with open(dest, "wb") as fh:
                        fh.write(tf.extractfile(m).read())
                    os.chmod(dest, m.mode)

    def exec(self, cid, argv):
        if argv[0] == "chmod" and len(argv) == 3:
            os.chmod(self._p(argv[2]), int(argv[1], 8)); return 0, "", ""
        if argv[0] == "chown" and len(argv) == 3:
            return 0, "", ""          # ownership is not meaningful in a plain directory
        return 126, "", f"DirTransport cannot exec {argv[0]!r}"

    def exists(self, cid, path):
        return os.path.lexists(self._p(path))

    def read_tree(self, cid, path):
        full = self._p(path)
        if not os.path.lexists(full):
            return None
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            tf.add(full, arcname=os.path.basename(full), recursive=True)   # symlinks stored as links
        return buf.getvalue()

    def remove(self, cid, path):
        full = self._p(path)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full)
        elif os.path.lexists(full):
            os.unlink(full)

    def mode_owner(self, cid, path):
        full = self._p(path)
        if not os.path.lexists(full):
            return None
        return format(os.stat(full).st_mode & 0o7777, "o"), "-"     # no owners in a plain directory
