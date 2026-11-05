import hashlib
import os
import stat
import subprocess

from ._util import need_roots, within


class CustomHook:
    """Run an allow-listed script. This is arbitrary code execution, so:
    the script must live under policy.script_roots, be a regular file that is not
    group/world writable, and match a pinned sha256 (required unless policy.require_sha256
    is false). It runs with a minimal environment, a timeout, and no shell string.
    It cannot be rolled back; the plan and manifest mark it irreversible."""
    path_fields = ()
    irreversible = True

    def validate(self, config, policy):
        if set(config) - {"script_path", "sha256", "args"}:
            raise ValueError("unknown_config_field")
        s = config.get("script_path")
        if not isinstance(s, str) or not os.path.isabs(s):
            raise ValueError("bad_path: script_path")
        roots = need_roots(policy, "script_roots", "custom_hook")
        if not within(s, roots) or not within(os.path.realpath(s), roots):
            raise ValueError("script_outside_script_roots")
        if not os.path.isfile(s):
            raise ValueError("script_missing")
        if os.stat(s).st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("script_writable_by_group_or_other")
        args = config.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError("bad_args")
        pin = config.get("sha256")
        if policy.get("require_sha256", True) and not pin:
            raise ValueError("sha256_pin_required")
        if pin and hashlib.sha256(open(s, "rb").read()).hexdigest() != pin:
            raise ValueError("script_digest_mismatch")
        if not isinstance(policy.get("interpreter", "/bin/sh"), str):
            raise ValueError("bad_policy: interpreter")

    def describe(self, config):
        return f"custom_hook {config['script_path']} (irreversible)"

    def plan(self, config, policy):
        return "run"            # hooks are not idempotent by definition

    def apply(self, config, policy, ctx):
        s = config["script_path"]
        timeout = int(policy.get("timeout_seconds", 60))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "EXTSCOPE_OP": ctx["op"]}
        r = subprocess.run([policy.get("interpreter", "/bin/sh"), s, *config.get("args", [])],
                           cwd=os.path.dirname(s), env=env, timeout=timeout,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"hook exited {r.returncode}: {r.stderr.strip()[-300:]}")
        return {"script": s, "exit": r.returncode}

    def undo(self, rec, force):
        raise ValueError("hooks cannot be rolled back")

    def verify(self, rec):
        return []
