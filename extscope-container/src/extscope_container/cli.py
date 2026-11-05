import argparse
import json
import sys

from . import __version__, creation, ephemeral, lease, plan as planmod, policy as pol, reinjection, run
from .errors import ContainerError
from .transport import DirTransport, PodmanTransport


def _vars(values):
    out = {}
    for v in values or []:
        k, sep, val = v.partition("=")
        if not sep or k in out:
            raise ContainerError("bad_var", v)
        out[k] = val
    return out


def _transport(a, p):
    if a.transport == "dir":
        if not a.dir_root:
            raise ContainerError("dir_root_required", "--dir-root")
        return DirTransport(a.dir_root, a.assume_mount)
    return PodmanTransport(p.podman)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="extscope-container")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config")
    ap.add_argument("--state-dir")
    ap.add_argument("--var", action="append")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("creation-plan", help="bind mounts needed when the container is created")
    c.add_argument("plan"); c.add_argument("--format", choices=("podman", "json"), default="podman"); c.add_argument("--selinux", action="store_true")
    r = sub.add_parser("run", help="apply a plan inside a running container")
    r.add_argument("plan"); r.add_argument("--container", required=True)
    r.add_argument("--dry-run", action="store_true"); r.add_argument("--force", action="store_true")
    r.add_argument("--allow-hooks", action="store_true")
    r.add_argument("--transport", choices=("podman", "dir"), default="podman")
    r.add_argument("--dir-root"); r.add_argument("--assume-mount", action="append", default=[])
    le = sub.add_parser("lease", help="canary lease")
    le.add_argument("action", choices=("mint", "check", "ensure", "purge"))
    le.add_argument("--tmpfs-path", required=True); le.add_argument("--disk-path"); le.add_argument("--ext-root")
    rf = sub.add_parser("refs", help="staging references")
    rf.add_argument("action", choices=("register", "release", "list", "status"))
    rf.add_argument("path", help="staging directory or composition_manifest.json")
    rf.add_argument("--ref", help="JSON object (register) or target/id (release)")
    rf.add_argument("--active", action="append", help="running container name (status)")
    g = sub.add_parser("gc", help="remove orphan project directories")
    g.add_argument("--base", required=True); g.add_argument("--dry-run", action="store_true")
    g.add_argument("--active", action="append", default=[]); g.add_argument("--podman-active", action="store_true")
    pg = sub.add_parser("purge", help="remove one project's directory")
    pg.add_argument("--base", required=True); pg.add_argument("project")
    q = sub.add_parser("quadlet-secret", help="print a Quadlet secret snippet")
    q.add_argument("secret_name"); q.add_argument("target"); q.add_argument("--link"); q.add_argument("--mode", default="0600")
    sl = sub.add_parser("secret-link", help="establish or verify the secret symlink")
    sl.add_argument("action", choices=("establish", "verify")); sl.add_argument("link"); sl.add_argument("source")
    a = ap.parse_args(argv)
    try:
        p = pol.load(a.config, _vars(a.var), a.state_dir)
        if a.cmd == "creation-plan":
            ms = creation.mounts(planmod.parse(a.plan, p.variables), a.selinux)
            print(creation.render(ms, a.format)); return 0
        if a.cmd == "run":
            out = run.execute(planmod.parse(a.plan, p.variables), _transport(a, p), a.container, p,
                              dry_run=a.dry_run, force=a.force, allow_hooks=a.allow_hooks)
        elif a.cmd == "lease":
            if a.action == "check":
                ok = lease.valid(a.tmpfs_path, a.disk_path or "")
                print(json.dumps({"valid": ok})); return 0 if ok else 1
            if a.action == "mint":
                lease.mint(a.tmpfs_path, a.disk_path); out = {"minted": True}
            elif a.action == "ensure":
                out = lease.ensure(a.tmpfs_path, a.disk_path, a.ext_root)
            else:
                lease.purge(a.ext_root); out = {"purged": a.ext_root}
        elif a.cmd == "refs":
            import os
            manifest = a.path if a.path.endswith(".json") else os.path.join(a.path, "composition_manifest.json")
            if a.action == "register":
                out = lease.register(manifest, json.loads(a.ref))
            elif a.action == "release":
                out = {"released": lease.release(manifest, a.ref)}
            elif a.action == "list":
                out = {"references": lease.references(manifest)}
            else:
                out = lease.status(os.path.dirname(manifest), set(a.active) if a.active else None)
        elif a.cmd == "gc":
            active = set(a.active)
            if a.podman_active:
                active |= PodmanTransport(p.podman).running_names()
            elif not a.active and not a.dry_run:
                raise ContainerError("active_set_required", "pass --active NAME... or --podman-active (or --dry-run)")
            out = ephemeral.gc(a.base, active, a.dry_run)
        elif a.cmd == "purge":
            out = {"reclaimed_bytes": ephemeral.purge(a.base, a.project)}
        elif a.cmd == "quadlet-secret":
            sys.stdout.write(reinjection.quadlet_snippet(a.secret_name, a.target, a.link, a.mode)); return 0
        else:
            if a.action == "establish":
                out = {"established": reinjection.establish_link(a.link, a.source)}
            else:
                ok, msg = reinjection.verify_link(a.link, a.source); out = {"ok": ok, "message": msg}
                print(json.dumps(out)); return 0 if ok else 1
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except ContainerError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 2
