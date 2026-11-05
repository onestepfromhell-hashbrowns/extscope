import os
import re
import shutil

from ._util import file_bytes, mode_of, need_roots, salted, within, write_private

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


class EnvInject:
    """Write `export KEY='value'` into <dir>/<KEY>.sh. The value is single-quoted so
    it cannot inject shell syntax; the manifest and plan never show it."""
    path_fields = ("dir",)
    irreversible = False

    def validate(self, config, policy):
        if set(config) - {"key", "value", "dir", "mode"}:
            raise ValueError("unknown_config_field")
        if not isinstance(config.get("key"), str) or not _KEY.match(config["key"]):
            raise ValueError("bad_env_key")
        if not isinstance(config.get("value"), str) or "\x00" in config["value"] or "\n" in config["value"]:
            raise ValueError("bad_env_value: no NUL or newline")
        mode = mode_of(config.get("mode", "0600"))
        if mode & 0o7000:
            raise ValueError("special_mode_bits_refused")
        roots = need_roots(policy, "allowed_dirs", "env_inject")
        if not within(os.path.join(config["dir"], config["key"] + ".sh"), roots):
            raise ValueError("dir_outside_env_roots")

    def _path(self, c):
        return os.path.join(c["dir"], c["key"] + ".sh")

    def _content(self, c):
        return f"export {c['key']}={_quote(c['value'])}\n".encode()

    def describe(self, config):
        return f"env_inject {config['key']} -> {self._path(config)} (value hidden)"

    def plan(self, config, policy):
        p = self._path(config)
        if os.path.isfile(p) and not os.path.islink(p) and file_bytes(p) == self._content(config):
            return "noop"
        return "run"

    def apply(self, config, policy, ctx):
        p, data = self._path(config), self._content(config)
        tmp = write_private(p, data, mode_of(config.get("mode", "0600")))
        backup = ""
        try:
            if os.path.lexists(p):
                os.makedirs(ctx["backup_dir"], mode=0o700, exist_ok=True)
                backup = os.path.join(ctx["backup_dir"], "prev")
                shutil.move(p, backup)
            os.replace(tmp, p)
        except BaseException:
            if os.path.lexists(tmp):
                os.unlink(tmp)
            if backup and not os.path.lexists(p):
                shutil.move(backup, p)
            raise
        salt, h = salted(data)
        return {"path": p, "backup": backup, "salt": salt, "salted_sha256": h}

    def undo(self, rec, force):
        p = rec["path"]
        if not force and os.path.isfile(p) and salted(file_bytes(p), rec["salt"])[1] != rec["salted_sha256"]:
            raise ValueError("env file changed since apply")
        if os.path.lexists(p):
            os.unlink(p)
        if rec["backup"]:
            shutil.move(rec["backup"], p)

    def verify(self, rec):
        p = rec["path"]
        if not os.path.isfile(p):
            return ["missing"]
        return [] if salted(file_bytes(p), rec["salt"])[1] == rec["salted_sha256"] else ["content_drift"]
