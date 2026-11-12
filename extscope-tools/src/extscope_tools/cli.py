# SPDX-License-Identifier: MIT
import argparse
import json
import os
import sys

from . import __version__, anomaly, authority, gate

ENV_DB = "EXTSCOPE_ANOMALY_DB"


def _db(a):
    db = a.db or os.environ.get(ENV_DB)
    if not db:
        print(json.dumps({"error": "db_required", "detail": f"--db or ${ENV_DB}"}), file=sys.stderr)
        raise SystemExit(2)
    return db


def main(argv=None):
    ap = argparse.ArgumentParser(prog="extscope-tools")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="static secret / secret-mount scan (exit 1 on violations)")
    g.add_argument("project", help="directory to scan")
    g.add_argument("--allowlist", help="JSON list of non-sensitive variable names")
    g.add_argument("--waivers", help="JSON list of {path, rule, reason, expires}")
    g.add_argument("--secret-dir", action="append", default=None, help="directory that must not be tracked (default .secrets)")
    d = sub.add_parser("anomaly", help="anomaly ledger")
    d.add_argument("--db")
    ds = d.add_subparsers(dest="sub", required=True)
    r = ds.add_parser("record"); r.add_argument("--project", required=True); r.add_argument("--phase", required=True)
    r.add_argument("--module", required=True); r.add_argument("--level", default="ERROR"); r.add_argument("--message", default="")
    r.add_argument("--scope", default=""); r.add_argument("--source", default=""); r.add_argument("--target", default="")
    l = ds.add_parser("list"); l.add_argument("--limit", type=int, default=20); l.add_argument("--project"); l.add_argument("--level")
    ar = ds.add_parser("archive"); ar.add_argument("--project", required=True); ar.add_argument("file", help="JSON Lines file, or - for stdin")
    ds.add_parser("check", help="can the database be written?")
    au = sub.add_parser("authority", help="layer-dependency check over explicit scope files")
    au.add_argument("--manifest")
    au.add_argument("scopes", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "gate":
        allow = json.load(open(a.allowlist, encoding="utf-8")) if a.allowlist else []
        try:
            waivers = gate.load_waivers(a.waivers)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": "bad_waivers", "detail": str(exc)}), file=sys.stderr); return 2
        res = gate.run(a.project, allow, waivers, tuple(a.secret_dir or (".secrets",)))
        print(json.dumps(res, indent=2, sort_keys=True)); return 0 if res["ok"] else 1
    if a.cmd == "anomaly":
        db = _db(a)
        if a.sub == "record":
            res = anomaly.record(db, a.project, a.phase, a.module, a.level, a.message, a.scope, a.source, a.target)
            print(json.dumps(res)); return 0 if res["stored"] != "none" else 1
        if a.sub == "list":
            print(json.dumps(anomaly.recent(db, a.limit, a.project, a.level), indent=2)); return 0
        if a.sub == "archive":
            lines = sys.stdin if a.file == "-" else open(a.file, encoding="utf-8")
            res = anomaly.archive(db, lines, a.project); print(json.dumps(res)); return 0 if res["status"] != "UNAVAILABLE" else 1
        ok, why = anomaly.check_writable(db); print(json.dumps({"writable": ok, "reason": why})); return 0 if ok else 1
    try:
        manifest = authority.load_manifest(a.manifest)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": "bad_manifest", "detail": str(exc)}), file=sys.stderr); return 2
    rows, problems = authority.read_scopes(a.scopes)
    found = authority.check(rows, manifest)
    print(json.dumps({"violations": found, "problems": problems, "ok": not found and not problems}, indent=2))
    return 0 if not found and not problems else 1
