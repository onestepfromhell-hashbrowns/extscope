import os

from ._util import need_roots, within


def _ids(owner):
    import grp
    import pwd
    user, _, group = owner.partition(":")
    uid = pwd.getpwnam(user).pw_uid if not user.isdigit() else int(user)
    gid = -1
    if group:
        gid = grp.getgrnam(group).gr_gid if not group.isdigit() else int(group)
    return uid, gid


class Chown:
    path_fields = ("target",)
    irreversible = False

    def validate(self, config, policy):
        if set(config) - {"owner", "target"}:
            raise ValueError("unknown_config_field")
        if not hasattr(os, "chown"):
            raise ValueError("unsupported_platform: chown needs POSIX")
        allowed = policy.get("allowed_owners")
        if not isinstance(allowed, list) or not allowed:
            raise ValueError("policy_missing: chown requires modules.policy.chown.allowed_owners")
        if config.get("owner") not in allowed:
            raise ValueError(f"owner_not_allowed: {config.get('owner')!r}")
        need_roots(policy, "allowed_roots", "chown")
        if not within(config["target"], policy["allowed_roots"]):
            raise ValueError("target_outside_chown_roots")

    def describe(self, config):
        return f"chown {config['owner']} {config['target']}"

    def plan(self, config, policy):
        t = config["target"]
        if os.path.islink(t) or not os.path.exists(t):
            raise ValueError("target must be an existing non-symlink path")
        uid, gid = _ids(config["owner"])
        st = os.lstat(t)
        return "noop" if st.st_uid == uid and (gid == -1 or st.st_gid == gid) else "run"

    def apply(self, config, policy, ctx):
        t = config["target"]
        self.plan(config, policy)
        uid, gid = _ids(config["owner"])
        fd = os.open(t, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if hasattr(os, "fchown"):
                os.fchown(fd, uid, gid)
            else:
                os.chown(t, uid, gid)
        finally:
            os.close(fd)
        return {"target": t, "prev": [st.st_uid, st.st_gid], "new": [uid, gid if gid != -1 else st.st_gid]}

    def undo(self, rec, force):
        st = os.lstat(rec["target"])
        if not force and [st.st_uid, st.st_gid] != rec["new"]:
            raise ValueError("ownership changed since apply")
        os.chown(rec["target"], *rec["prev"])

    def verify(self, rec):
        if not os.path.lexists(rec["target"]):
            return ["missing"]
        st = os.lstat(rec["target"])
        return [] if [st.st_uid, st.st_gid] == rec["new"] else ["owner_drift"]
