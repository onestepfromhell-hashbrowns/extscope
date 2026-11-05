import argparse
import json
import sys

from . import __version__, config, engine, prune, receipt, sync
from .errors import DistError
from .plan import load_plan


def _vars(values):
    out = {}
    for v in values or []:
        k, sep, val = v.partition("=")
        if not sep or k in out:
            raise DistError("bad_var", v)
        out[k] = val
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="extscope-dist", description="Config-driven distribution with backup and rollback.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help=f"config JSON (default: ${config.ENV_CONFIG}, then the per-user config dir)")
    p.add_argument("--state-dir", help="backups and manifests (default: config / per-user state dir)")
    p.add_argument("--var", action="append", help="NAME=VALUE for ${NAME} expansion (repeatable)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("plan", "dry-run: show what apply would do (no writes)"),
                           ("apply", "execute the plan")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("plan_file")
        s.add_argument("--filter-prefix", action="append", default=[], help="only targets under this path (repeatable)")
        s.add_argument("--receipt", help="extscope bind receipt: refuse any source that no longer matches it")
        if name == "apply":
            s.add_argument("--all", action="store_true", help="no prefix filter")
            s.add_argument("--force", action="store_true", help="back up and replace unmanaged targets")
        else:
            s.add_argument("--all", action="store_true", help="no prefix filter (default when no --filter-prefix)")
    r = sub.add_parser("rollback", help="restore the state before an apply")
    r.add_argument("manifest")
    r.add_argument("--force", action="store_true")
    v = sub.add_parser("verify", help="check that applied targets still match their manifest")
    v.add_argument("manifest")
    d = sub.add_parser("diff", help="show how applied targets differ from their sources (read-only)")
    d.add_argument("manifest")
    pb = sub.add_parser("pullback", help="write edited targets back to their sources (dry-run unless --apply)")
    pb.add_argument("manifest")
    pb.add_argument("--apply", action="store_true", help="actually write to the source; needs source_roots in config")
    o = sub.add_parser("origin", help="which source (and git repo) produced this deployed path")
    o.add_argument("path")
    pr = sub.add_parser("prune", help="list (default) or remove old backups and manifests in state_dir")
    pr.add_argument("--older-than", type=float, default=30.0, metavar="DAYS", help="only things at least this old (default 30)")
    pr.add_argument("--include-applied", action="store_true", help="also superseded applied manifests (loses restore-to-original)")
    pr.add_argument("--apply", action="store_true", help="actually delete; without it nothing is removed")
    sub.add_parser("show-config", help="print the effective configuration")
    a = p.parse_args(argv)
    try:
        cfg = config.load(a.config, _vars(a.var), a.state_dir)
        if a.cmd == "show-config":
            out = {"source": cfg.source or None, "state_dir": cfg.state_dir, "variables": cfg.variables,
                   "remap": cfg.remap, "allowed_roots": cfg.allowed_roots, "denied_roots": cfg.denied_roots,
                   "source_roots": cfg.source_roots}
        elif a.cmd == "prune":
            out = prune.run(cfg.state_dir, a.older_than, a.include_applied, a.apply)
        elif a.cmd in ("plan", "apply"):
            plan = load_plan(a.plan_file, cfg.variables)
            if a.receipt:
                plan.receipt = receipt.load(a.receipt)
            engine.resolve(plan, cfg)
            if a.cmd == "plan":
                out = engine.preview(plan, cfg, a.filter_prefix, not a.filter_prefix)
            else:
                out = engine.apply(plan, cfg, a.filter_prefix, a.all, a.force)
        elif a.cmd == "diff":
            out = sync.diff(a.manifest, cfg)
        elif a.cmd == "pullback":
            out = sync.pullback(a.manifest, cfg, a.apply)
        elif a.cmd == "origin":
            out = sync.origin(a.path, cfg)
        elif a.cmd == "rollback":
            out = engine.rollback(a.manifest, cfg, a.force)
        else:
            out = engine.verify(a.manifest, cfg)
        print(json.dumps(out, indent=2, sort_keys=True, default=list))
        return 0 if out.get("ok", True) else 4
    except DistError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 2
