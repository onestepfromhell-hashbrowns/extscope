import os
import shutil

from ._util import (file_bytes, lstat_mode, mode_of, need_roots, no_special_bits, salted,
                    within, write_private)


def _on_fs(path, types):
    """True if the mount holding `path` has one of `types` (Linux /proc/mounts)."""
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            rows = [l.split() for l in fh]
    except OSError:
        return False
    best, kind = "", ""
    full = os.path.realpath(path)
    for r in rows:
        m = r[1].replace("\\040", " ")
        if (full == m or full.startswith(m.rstrip("/") + "/")) and len(m) >= len(best):
            best, kind = m, r[2]
    return kind in types


class KeyInject:
    """Copy a secret file to dst with a restrictive mode set before it becomes visible.
    The manifest keeps only a salted digest; never the content."""
    path_fields = ("dst",)
    irreversible = False

    def validate(self, config, policy):
        if set(config) - {"src", "dst", "chmod", "chown", "sensitivity"}:
            raise ValueError("unknown_config_field")
        if "chown" in config:
            raise ValueError("chown_not_supported: add a separate chown item")
        if not isinstance(config.get("src"), str) or not os.path.isabs(config["src"]):
            raise ValueError("bad_path: src")
        mode = mode_of(config.get("chmod", "0600"))
        no_special_bits(mode)
        if mode & 0o077 and not policy.get("allow_group_other_access"):
            raise ValueError("mode_too_wide: group/other bits need policy.allow_group_other_access")
        roots = need_roots(policy, "allowed_dst_roots", "key_inject")
        if not within(config["dst"], roots):
            raise ValueError("dst_outside_key_roots")
        fs = policy.get("require_fs_types")
        if fs and not _on_fs(os.path.dirname(config["dst"]) if os.path.exists(os.path.dirname(config["dst"])) else _nearest(config["dst"]), fs):
            raise ValueError(f"dst_filesystem_not_in {fs}")
        if not os.path.isfile(config["src"]):
            raise ValueError("source_missing")

    def describe(self, config):
        return f"key_inject -> {config['dst']} (mode {config.get('chmod', '0600')}, content hidden)"

    def plan(self, config, policy):
        d = config["dst"]
        if os.path.isfile(d) and not os.path.islink(d) and \
                file_bytes(d) == file_bytes(config["src"]) and \
                lstat_mode(d) == mode_of(config.get("chmod", "0600")):
            return "noop"
        return "run"

    def apply(self, config, policy, ctx):
        d, mode = config["dst"], mode_of(config.get("chmod", "0600"))
        data = file_bytes(config["src"])
        backup = ""
        tmp = write_private(d, data, mode)
        try:
            if os.path.lexists(d):
                os.makedirs(ctx["backup_dir"], mode=0o700, exist_ok=True)
                backup = os.path.join(ctx["backup_dir"], "prev")
                shutil.move(d, backup)
            os.replace(tmp, d)
        except BaseException:
            if os.path.lexists(tmp):
                os.unlink(tmp)
            if backup and os.path.lexists(backup) and not os.path.lexists(d):
                shutil.move(backup, d)
            raise
        salt, h = salted(data)
        return {"dst": d, "backup": backup, "mode": mode, "salt": salt, "salted_sha256": h}

    def undo(self, rec, force):
        d = rec["dst"]
        if not force and os.path.isfile(d) and salted(file_bytes(d), rec["salt"])[1] != rec["salted_sha256"]:
            raise ValueError("secret changed since apply")
        if os.path.lexists(d):
            os.unlink(d)
        if rec["backup"]:
            shutil.move(rec["backup"], d)

    def verify(self, rec):
        d = rec["dst"]
        if not os.path.isfile(d):
            return ["missing"]
        out = []
        if salted(file_bytes(d), rec["salt"])[1] != rec["salted_sha256"]:
            out.append("content_drift")
        if lstat_mode(d) != rec["mode"]:
            out.append("mode_drift")
        return out


def _nearest(path):
    p = os.path.dirname(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p
