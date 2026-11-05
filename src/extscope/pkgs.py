"""Read-only package governance over an explicit package root: list, doctor,
override check. Package identity lives in <root>/.meta/<id>.meta.json; the package
tree is <root>/<id> or <root>/{external,native,overrides}/<id>. The content digest
is the repository's `package.content.v1` algorithm (sorted [path, executable, sha256]
records), kept as-is so existing .meta files keep verifying."""
import hashlib
import json
import os
import re
import stat
import subprocess

CATEGORY_DIRS = ("external", "native", "overrides")
SOURCE_TYPES = ("upstream_external", "inhouse_native", "override_patch")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?\Z")
_CRED = re.compile(r"://[^/\s:@]+:[^@\s/]+@|(?i:token=|password=|secret=)|ghp_[A-Za-z0-9]{10,}")


def diag(code, message, level="ERROR", path=""):
    return {"code": code, "level": level, "message": message, **({"path": path} if path else {})}


def meta_path(root, pid):
    return os.path.join(root, ".meta", pid + ".meta.json")


def find_dir(root, pid):
    for cand in [os.path.join(root, pid)] + [os.path.join(root, c, pid) for c in CATEGORY_DIRS]:
        if os.path.isdir(cand):
            return cand
    return os.path.join(root, pid)


def content_digest(pkg_dir):
    base = os.path.realpath(pkg_dir)
    if not os.path.isdir(base):
        return None, [diag("DIRECTORY_NOT_FOUND", pkg_dir)]
    recs, bad = [], []

    def add_file(real, rel):
        st = os.stat(real)
        if not stat.S_ISREG(st.st_mode):
            bad.append(diag("SPECIAL_FILE_REJECTED", rel))
            return
        h = hashlib.sha256()
        with open(real, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        recs.append([rel, bool(st.st_mode & 0o111), h.hexdigest()])

    # chain holds the real paths of directories on the current walk, so a link back to an
    # ancestor is reported as a cycle instead of recursing forever.
    def walk(cur, rel_root, chain):
        for name in sorted(os.listdir(cur)):
            if name == ".git":
                continue
            full = os.path.join(cur, name)
            rel = os.path.join(rel_root, name) if rel_root else name
            if os.path.islink(full):
                # A link is content, not identity: it is followed only when it resolves to
                # something inside this package. Escapes, dangling links and cycles are rejected.
                real = os.path.realpath(full)
                if not real.startswith(base + os.sep):
                    bad.append(diag("SYMLINK_ESCAPES_PACKAGE", rel))
                elif not os.path.exists(real):
                    bad.append(diag("SYMLINK_DANGLING", rel))
                elif os.path.isdir(real):
                    if real in chain:
                        bad.append(diag("SYMLINK_CYCLE", rel))
                    else:
                        walk(real, rel, chain + (real,))
                else:
                    add_file(real, rel)
            elif os.path.isdir(full):
                walk(full, rel, chain + (full,))
            else:
                add_file(full, rel)

    walk(base, "", (base,))
    if bad:
        return None, bad
    recs.sort(key=lambda r: r[0].encode("utf-8"))
    blob = json.dumps(recs, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), []


def _exports(exports, pkg_dir):
    out, seen, base = [], set(), os.path.realpath(pkg_dir)
    for i, e in enumerate(exports):
        ptr = f"/exports/{i}"
        eid, p = e.get("id", ""), e.get("path", "")
        if not eid:
            out.append(diag("EXPORT_INVALID", "missing id", path=ptr))
        elif eid in seen:
            out.append(diag("EXPORT_DUPLICATE_ID", eid, path=ptr))
        seen.add(eid)
        if not p:
            out.append(diag("EXPORT_INVALID", "missing path", path=ptr))
            continue
        segs = p.split("/")
        if p.startswith("/") or "\\" in p or any(s in ("", ".", "..") for s in segs):
            out.append(diag("PATH_TRAVERSAL", p, path=ptr))
            continue
        target = os.path.join(base, p)
        if not os.path.lexists(target):
            out.append(diag("EXPORT_NOT_FOUND", p, path=ptr))
        elif os.path.islink(target):
            out.append(diag("SYMLINK_REJECTED", p, path=ptr))
        elif not os.path.realpath(target).startswith(base + os.sep):
            out.append(diag("PATH_TRAVERSAL", p, path=ptr))
    return out


def _schema(meta):
    d = []
    for k in ("schema_version", "package_id", "source_type", "source", "content_digest"):
        if k not in meta:
            d.append(diag("SCHEMA_MISSING_FIELD", k, path="/" + k))
    if meta.get("source_type") not in SOURCE_TYPES:
        d.append(diag("SCHEMA_BAD_SOURCE_TYPE", str(meta.get("source_type")), path="/source_type"))
    if "package_id" in meta and not ID.match(str(meta["package_id"])):
        d.append(diag("SCHEMA_BAD_ID", str(meta["package_id"]), path="/package_id"))
    if meta.get("source_type") == "override_patch":
        for k in ("target_upstream", "base_commit", "overridden_files"):
            if k not in meta:
                d.append(diag("SCHEMA_MISSING_FIELD", k, path="/" + k))
    return d


def validate(root, pid):
    root = os.path.realpath(root)
    mp, pkg = meta_path(root, pid), find_dir(root, pid)
    has_meta, has_dir = os.path.isfile(mp), os.path.isdir(pkg)
    if has_dir and not has_meta:
        return {"package_id": pid, "status": "unknown", "diagnostics": [diag("UNREGISTERED_ENTITY", f"no metadata at {mp}")]}
    if has_meta and not has_dir:
        return {"package_id": pid, "status": "orphan_meta", "diagnostics": [diag("ORPHAN_METADATA", f"directory missing: {pkg}")]}
    if not has_meta:
        return {"package_id": pid, "status": "invalid", "diagnostics": [diag("NOT_FOUND", f"{pid} not under {root}")]}
    try:
        with open(mp, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"package_id": pid, "status": "invalid", "diagnostics": [diag("JSON_PARSE_ERROR", str(exc))]}
    ds = _schema(meta)
    if meta.get("package_id") != pid:
        ds.append(diag("IDENTITY_MISMATCH", f"{meta.get('package_id')!r} != {pid!r}", path="/package_id"))
    loc = (meta.get("source") or {}).get("locator", "")
    if loc and _CRED.search(loc):
        ds.append(diag("CREDENTIAL_LEAK", "locator contains credentials", path="/source/locator"))
    actual, dd = content_digest(pkg)
    ds += dd
    if actual and meta.get("content_digest") and actual != meta["content_digest"]:
        ds.append(diag("DIGEST_MISMATCH", f"actual {actual}, declared {meta['content_digest']}", path="/content_digest"))
    ds += _exports(meta.get("exports", []), pkg)
    status = "invalid" if any(x["level"] == "ERROR" for x in ds) else "ok"
    return {"package_id": pid, "status": status, "source_type": meta.get("source_type"),
            "package_dir": pkg, "diagnostics": ds}


def all_ids(root):
    ids = set()
    mdir = os.path.join(root, ".meta")
    if os.path.isdir(mdir):
        ids |= {n[:-10] for n in os.listdir(mdir) if n.endswith(".meta.json")}
    for base in [root] + [os.path.join(root, c) for c in CATEGORY_DIRS]:
        if os.path.isdir(base):
            ids |= {n for n in os.listdir(base) if not n.startswith(".") and n not in CATEGORY_DIRS and n != "node_modules"
                    and os.path.isdir(os.path.join(base, n)) and ID.match(n)}
    return sorted(ids)


def list_packages(root, source_type=None):
    rows = []
    for pid in all_ids(os.path.realpath(root)):
        r = validate(root, pid)
        if source_type and r.get("source_type") != source_type:
            continue
        rows.append({"package_id": pid, "status": r["status"], "source_type": r.get("source_type")})
    return {"root": os.path.realpath(root), "count": len(rows), "packages": rows}


def doctor(root, pid=None):
    ids = [pid] if pid else all_ids(os.path.realpath(root))
    reports = [validate(root, i) for i in ids]
    return {"ok": all(r["status"] == "ok" for r in reports),
            "summary": {s: sum(r["status"] == s for r in reports) for s in sorted({r["status"] for r in reports})},
            "reports": reports}


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          env={"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "HOME": os.devnull})


def override_check(root, override_id, candidate):
    """Three-way compare: base (pinned commit) / override (our patch) / candidate (new upstream)."""
    root = os.path.realpath(root)
    info = validate(root, override_id)
    res = {"package_id": override_id, "candidate_commit": candidate, "files": [], "info_files": [],
           "diagnostics": info["diagnostics"], "status": "ERROR"}
    if info["status"] in ("unknown", "orphan_meta", "invalid") and not os.path.isfile(meta_path(root, override_id)):
        return res
    with open(meta_path(root, override_id), encoding="utf-8") as fh:
        meta = json.load(fh)
    up, base = meta.get("target_upstream", ""), meta.get("base_commit", "")
    res.update(target_upstream=up, base_commit=base)
    up_dir = find_dir(root, up)
    if not os.path.isdir(os.path.join(up_dir, ".git")):
        res["diagnostics"].append(diag("UPSTREAM_NOT_GIT", up))
        return res
    if not SHA.match(candidate) or _git(up_dir, "rev-parse", "--verify", f"{candidate}^{{commit}}").returncode:
        res["diagnostics"].append(diag("CANDIDATE_COMMIT_NOT_FOUND", candidate))
        return res
    conflict = redundant = False
    for f in meta.get("overridden_files", []):
        p, b_sha, o_sha, o_mode = f["path"], f["base_sha256"], f["override_sha256"], f.get("mode", "0644")
        ls = _git(up_dir, "ls-tree", candidate, "--", p).stdout.decode().split()
        row = {"path": p, "base_sha256": b_sha, "override_sha256": o_sha}
        if len(ls) < 2 or ls[1] != "blob":
            row.update(status="CONFLICT", message="missing or not a file in candidate")
            conflict = True
        else:
            n_sha = hashlib.sha256(_git(up_dir, "cat-file", "blob", f"{candidate}:{p}").stdout).hexdigest()
            n_mode = "0755" if ls[0].endswith("755") else "0644"
            b_mode = "0755" if _git(up_dir, "ls-tree", base, "--", p).stdout.decode().startswith("100755") else "0644"
            row.update(candidate_sha256=n_sha, candidate_mode=n_mode)
            if n_sha == b_sha and n_mode == b_mode:
                row.update(status="SAFE", message="upstream unchanged; override still applies")
            elif n_sha == o_sha and n_mode == o_mode:
                row.update(status="REDUNDANT", message="candidate already contains this change; drop the override")
                redundant = True
            else:
                row.update(status="CONFLICT", message="candidate and override both changed this file")
                conflict = True
        res["files"].append(row)
    mine = {f["path"] for f in meta.get("overridden_files", [])}
    d = _git(up_dir, "diff-tree", "-r", "--name-status", "--no-commit-id", base, candidate).stdout.decode()
    for line in d.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1] not in mine:
            res["info_files"].append({"path": parts[1], "change_type": parts[0]})
    res["status"] = "CONFLICT" if conflict else "REDUNDANT" if redundant else "SAFE"
    return res
