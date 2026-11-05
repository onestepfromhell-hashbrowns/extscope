import hashlib
import os
import re
import secrets
import stat


def mode_of(text, what="mode"):
    if not isinstance(text, str) or not re.fullmatch(r"0?[0-7]{3,4}", text):
        raise ValueError(f"bad_mode: {what}={text!r}")
    return int(text, 8)


def no_special_bits(mode):
    if mode & 0o7000:
        raise ValueError("special_mode_bits_refused: setuid/setgid/sticky")


def require_abs(path, what):
    if not isinstance(path, str) or not os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
        raise ValueError(f"bad_path: {what}={path!r}")


def within(path, roots):
    p = os.path.realpath(os.path.dirname(path))
    full = os.path.join(p, os.path.basename(path))
    return any(full == r or full.startswith(os.path.realpath(r).rstrip(os.sep) + os.sep)
               for r in roots)


def need_roots(policy, key, what):
    roots = policy.get(key)
    if not isinstance(roots, list) or not roots:
        raise ValueError(f"policy_missing: {what} requires modules.policy.{key}")
    for r in roots:
        require_abs(r, key)
    return roots


def salted(data, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt, hashlib.sha256(salt.encode() + data).hexdigest()


def file_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def lstat_mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def write_private(path, data, mode):
    """Create path with `mode` from the first byte (no wider window), atomically."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.extscope-tmp-{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        return tmp
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
