import argparse
import json
import os
import sys

from . import __version__, anomaly, assemble, authority, canonical, gate

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
    cn = sub.add_parser("canonical", help="staging check: release digests, no unlisted links to sources, projected links go through canonical")
    cn.add_argument("release", help="release directory")
    cn.add_argument("--links", action="append", default=[], help="directory of projected links to check (repeatable)")
    cn.add_argument("--canonical-root", help="required with --links: the canonical root links must go through")
    asm = sub.add_parser("assemble", help="build a new canonical release from a base release plus sources (never switches current)")
    asm.add_argument("--base", required=True, help="existing release directory to copy")
    asm.add_argument("--out-root", required=True, help="directory that receives <generation>/; created if missing")
    asm.add_argument("--add", action="append", default=[], metavar="SRC=DEST", help="copy SRC to DEST inside the release (repeatable), e.g. /src/skill=skills/name")
    asm.add_argument("--source", action="append", default=[], metavar="KEY=VALUE", help="extra entry for DELIVERY.json sources (repeatable)")
    asm.add_argument("--allow-dirty", action="store_true", help="accept sources with uncommitted changes (recorded as +dirty)")
    asm.add_argument("--allow-untracked", action="store_true", help="accept sources outside any git repo (recorded as untracked:<content id>)")
    a = ap.parse_args(argv)
    if a.cmd == "assemble":
        try:
            adds = [tuple(x.split("=", 1)) for x in a.add]
            extra = dict(x.split("=", 1) for x in a.source)
            if any(len(t) != 2 for t in adds):
                raise ValueError("--add needs SRC=DEST")
            res = assemble.assemble(a.base, a.out_root, adds, extra, a.allow_dirty, a.allow_untracked)
        except (ValueError, assemble.AssembleError) as exc:
            print(json.dumps({"error": "assemble_failed", "detail": str(exc)}, ensure_ascii=False), file=sys.stderr); return 1
        print(json.dumps({"release": res["release"], "generation": res["generation"], "base_generation": res["base_generation"],
                          "updated_skills": res["updated_skills"], "sources": res["sources"], "ok": True}, indent=2, ensure_ascii=False)); return 0
    if a.cmd == "canonical":
        if a.links and not a.canonical_root:
            print(json.dumps({"error": "canonical_root_required"}), file=sys.stderr); return 2
        found = canonical.check_release(a.release)
        for d in a.links:
            found += canonical.check_links(d, a.canonical_root)
        print(json.dumps({"violations": found, "ok": not found}, indent=2)); return 0 if not found else 1
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
