"""Re-inject non-persistent secrets: a symlink at a stable path pointing at a tmpfs file,
a probe to check it, and a Quadlet snippet that declares the secret."""
import os
import re
import shlex

from .errors import ContainerError


def establish_link(link, tmpfs_source):
    """True if the link now points at tmpfs_source; False if the source is absent.
    A directory at `link` is refused, never deleted."""
    if not os.path.exists(tmpfs_source):
        return False
    d = os.path.dirname(link)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.islink(link) or os.path.isfile(link):
        os.remove(link)
    elif os.path.isdir(link):
        return False
    os.symlink(tmpfs_source, link)
    return True


def verify_link(link, tmpfs_source, expected_mode=0o600):
    if not os.path.exists(tmpfs_source):
        return False, f"secret file missing: {tmpfs_source}"
    mode = os.stat(tmpfs_source).st_mode & 0o777
    if mode != expected_mode:
        return False, f"secret mode {oct(mode)} != {oct(expected_mode)}"
    if not os.path.lexists(link):
        return False, f"link missing: {link}"
    if not os.path.islink(link):
        return False, f"not a symlink: {link}"
    if os.path.abspath(os.readlink(link)) != os.path.abspath(tmpfs_source):
        return False, f"link points to {os.readlink(link)}"
    return True, "OK"


def _field(value, name):
    if not value or not re.fullmatch(r"[A-Za-z0-9_./@+-]+", value):      # safe inside sh -c '...'
        raise ContainerError("bad_quadlet_field", name)


def quadlet_snippet(secret_name, target, link_path=None, mode="0600"):
    _field(secret_name, "secret_name"); _field(target, "target")
    if link_path:
        _field(link_path, "link_path")
    if not re.fullmatch(r"[0-7]{3,4}", mode):
        raise ContainerError("bad_mode", mode)
    q = shlex.quote(target)
    lines = ["[Container]", f"Secret={secret_name},target={target},mode={mode}", "", "[Service]",
             f"ExecStartPre=/bin/sh -c '[ -f {q} ] || {{ echo \"FATAL: secret {secret_name} missing\"; exit 1; }}'"]
    if link_path:
        lines.append(f"ExecStartPre=/bin/sh -c 'ln -sf {q} {shlex.quote(link_path)}'")
    return "\n".join(lines) + "\n"
