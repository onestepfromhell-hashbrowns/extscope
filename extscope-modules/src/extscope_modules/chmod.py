import os

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
        prev = lstat_mode(t)
        os.chmod(t, mode_of(config["mode"]))
        return {"target": t, "prev_mode": prev, "new_mode": mode_of(config["mode"])}

    def undo(self, rec, force):
        if not force and lstat_mode(rec["target"]) != rec["new_mode"]:
            raise ValueError("mode changed since apply")
        os.chmod(rec["target"], rec["prev_mode"])

    def verify(self, rec):
        if not os.path.lexists(rec["target"]):
            return ["missing"]
        return [] if lstat_mode(rec["target"]) == rec["new_mode"] else ["mode_drift"]
