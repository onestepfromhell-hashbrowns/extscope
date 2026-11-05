"""Bind mounts must exist when a container is created; this renders them from a plan."""
import json

from .errors import ContainerError
from .policy import norm_posix


def mounts(ops, selinux=False):
    out = []
    for o in ops:
        if o["op"] != "mount":
            continue
        src, dst, mode = o["src"], norm_posix(o["dst"], "mount dst"), o.get("mode", "ro")
        if mode not in ("ro", "rw"):
            raise ContainerError("bad_mount_mode", mode)
        if not src.startswith("/") and not (len(src) > 2 and src[1] == ":"):
            raise ContainerError("mount_src_not_absolute", src)
        if any(c in src + dst for c in ":,\n\x00"):
            if not (len(src) > 2 and src[1] == ":" and not any(c in src[2:] for c in ":,\n\x00")):
                raise ContainerError("mount_unsafe_characters", src)
        out.append({"src": src, "dst": dst, "mode": mode + (",Z" if selinux else "")})
    return out


def render(ms, fmt="podman"):
    if fmt == "json":
        return json.dumps(ms, indent=2)
    return "\n".join(f"-v {m['src']}:{m['dst']}:{m['mode']}" for m in ms)
