import os
import stat

from ._util import lstat_mode, mode_of, no_special_bits


class Chmod:
    path_fields = ("target",)
    irreversible = False

    def validate(self, config, policy):
        if set(config) - {"mode", "target"}:
            raise ValueError("unknown_config_field")
        no_special_bits(mode_of(config.get("mode")))

    def describe(self, config):
        return f"chmod {config['mode']} {config['target']}"

    def plan(self, config, policy):
        t = config["target"]
        if os.path.islink(t) or not os.path.exists(t):
            raise ValueError("target must be an existing non-symlink path")
        return "noop" if lstat_mode(t) == mode_of(config["mode"]) else "run"

    def apply(self, config, policy, ctx):
        t = config["target"]
        self.plan(config, policy)
        fd = os.open(t, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            prev = stat.S_IMODE(st.st_mode)
            os.fchmod(fd, mode_of(config["mode"]))
        finally:
            os.close(fd)
        return {"target": t, "prev_mode": prev, "new_mode": mode_of(config["mode"])}

    def undo(self, rec, force):
        if not force and lstat_mode(rec["target"]) != rec["new_mode"]:
            raise ValueError("mode changed since apply")
        fd = os.open(rec["target"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fchmod(fd, rec["prev_mode"])
        finally:
            os.close(fd)

    def verify(self, rec):
        if not os.path.lexists(rec["target"]):
            return ["missing"]
        return [] if lstat_mode(rec["target"]) == rec["new_mode"] else ["mode_drift"]
