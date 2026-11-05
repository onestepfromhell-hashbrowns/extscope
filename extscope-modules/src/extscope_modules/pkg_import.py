import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid

from ._util import need_roots, within

CATEGORY = {"external": ("upstream_external", "external"), "native": ("inhouse_native", "native")}
CATEGORY_DIRS = ("external", "native", "overrides")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SHA = re.compile(r"([0-9a-f]{40}|[0-9a-f]{64})\Z")
CRED = re.compile(r"://[^/\s:@]+:[^@\s/]+@|(?i:token=|password=|secret=)|ghp_[A-Za-z0-9]{10,}")
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file:https:ssh"}


def content_digest(pkg_dir):
    """Same `package content` algorithm as `extscope pkg doctor` (kept in sync by tests)."""
    base = os.path.realpath(pkg_dir)
    recs = []
    for cur, dirs, files in os.walk(base, followlinks=False):
        if ".git" in dirs:
            dirs.remove(".git")
        for d in dirs:
            if os.path.islink(os.path.join(cur, d)):
                raise ValueError("symlink_rejected: " + os.path.join(cur, d))
        for f in files:
            full = os.path.join(cur, f)
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("special_file_rejected: " + full)
            h = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            recs.append([rel, bool(st.st_mode & 0o111), h.hexdigest()])
    recs.sort(key=lambda r: r[0].encode("utf-8"))
    return hashlib.sha256(json.dumps(recs, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _targets(c):
    root = c["root"]
    structured = any(os.path.isdir(os.path.join(root, d)) for d in CATEGORY_DIRS)
    sub = CATEGORY[c["as_type"]][1]
    pkg = os.path.join(root, sub, c["package_id"]) if structured else os.path.join(root, c["package_id"])
    return pkg, os.path.join(root, ".meta", c["package_id"] + ".meta.json"), structured


def _git(cwd, *args):
    r = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.file.allow=always", *args],
                       cwd=cwd, capture_output=True, text=True,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": os.devnull, **GIT_ENV})
    if r.returncode:
        raise RuntimeError("git " + args[0] + " failed: " + r.stderr.strip()[-200:])
    return r.stdout.strip()


class PkgImport:
    """Atomically import a package into an explicit package root and register its identity.
    Fetches from a local directory/repo (or, only when policy.allow_remote_git, a URL).
    Never runs source scripts or git hooks. Refuses to overwrite. Rollback removes both parts."""
    path_fields = ("root",)
    irreversible = False

    def validate(self, config, policy):
        allowed = {"root", "locator", "as_type", "package_id", "commit", "exports"}
        if set(config) - allowed:
            raise ValueError("unknown_config_field")
        if config.get("as_type") not in CATEGORY:
            raise ValueError("bad_as_type: external|native")
        if not ID.match(str(config.get("package_id", ""))):
            raise ValueError("bad_package_id")
        commit = config.get("commit")
        if commit and not SHA.match(commit):
            raise ValueError("bad_commit")
        if config["as_type"] == "external" and not commit:
            raise ValueError("commit_required_for_external")
        loc = config.get("locator", "")
        if not loc or CRED.search(loc):
            raise ValueError("locator_missing_or_contains_credentials")
        if "://" in loc and not loc.startswith("file://"):
            if not policy.get("allow_remote_git"):
                raise ValueError("remote_locator_refused: set policy.allow_remote_git")
        else:
            src = loc[len("file://"):] if loc.startswith("file://") else loc
            roots = need_roots(policy, "allowed_source_roots", "pkg_import")
            if not os.path.isabs(src) or not os.path.exists(src):
                raise ValueError("source_missing")
            if not within(src, roots):
                raise ValueError("source_outside_allowed_source_roots")
        for i, e in enumerate(config.get("exports", [])):
            p = str(e.get("path", ""))
            if not e.get("id") or p.startswith("/") or "\\" in p or any(s in ("", ".", "..") for s in p.split("/")):
                raise ValueError(f"bad_export[{i}]")

    def describe(self, config):
        return f"pkg_import {config['package_id']} ({config['as_type']}) from {config['locator']} -> {config['root']}"

    def plan(self, config, policy):
        pkg, meta, _ = _targets(config)
        if os.path.lexists(pkg) or os.path.lexists(meta):
            raise ValueError(f"already_exists: refusing to overwrite {config['package_id']}")
        return "run"

    def apply(self, config, policy, ctx):
        self.plan(config, policy)
        pkg, meta_path, structured = _targets(config)
        root = config["root"]
        stage = os.path.join(root, ".staging", "import_" + uuid.uuid4().hex[:12])
        os.makedirs(os.path.dirname(stage), exist_ok=True)
        loc = config["locator"]
        src = loc[len("file://"):] if loc.startswith("file://") else loc
        is_url = "://" in loc and not loc.startswith("file://")
        try:
            is_git = is_url or os.path.isdir(os.path.join(src, ".git"))
            if is_git:
                _git(os.path.dirname(stage), "clone", "--no-checkout", "-q", loc if is_url else src, stage)
                commit = config.get("commit") or _git(stage, "rev-parse", "HEAD")
                _git(stage, "checkout", "-q", "--detach", commit)
                if _git(stage, "rev-parse", "HEAD") != commit and config.get("commit"):
                    raise RuntimeError("commit_mismatch")
                shutil.rmtree(os.path.join(stage, ".git"), ignore_errors=True)
            else:
                if config["as_type"] == "external":
                    raise RuntimeError("external_source_must_be_git")
                shutil.copytree(src, stage, symlinks=True,
                                ignore=shutil.ignore_patterns(".git"))
                commit = config.get("commit")
            digest = content_digest(stage)
            for e in config.get("exports", []):
                if not os.path.exists(os.path.join(stage, e["path"])):
                    raise RuntimeError("export_not_found: " + e["path"])
            meta = {"schema_version": 1, "package_id": config["package_id"],
                    "source_type": CATEGORY[config["as_type"]][0],
                    "source": {"kind": "git" if is_git else "directory",
                               "locator": loc if is_url else
                               "local://" + os.path.relpath(pkg, root).replace(os.sep, "/")},
                    "content_digest": digest, "exports": config.get("exports", [])}
            if commit:
                meta["source"]["commit"] = commit
            os.makedirs(os.path.dirname(pkg), exist_ok=True)
            os.makedirs(os.path.dirname(meta_path), exist_ok=True)
            os.rename(stage, pkg)                    # same filesystem: atomic publish of the tree
            tmp = meta_path + ".tmp." + uuid.uuid4().hex[:6]
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, sort_keys=True)
            os.replace(tmp, meta_path)               # identity last: a half import is never "managed"
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            if os.path.isdir(pkg) and not os.path.exists(meta_path):
                shutil.rmtree(pkg, ignore_errors=True)
            raise
        return {"package_dir": pkg, "meta": meta_path, "content_digest": digest}

    def undo(self, rec, force):
        if not force and os.path.isdir(rec["package_dir"]) and content_digest(rec["package_dir"]) != rec["content_digest"]:
            raise ValueError("package changed since import")
        shutil.rmtree(rec["package_dir"], ignore_errors=True)
        if os.path.lexists(rec["meta"]):
            os.unlink(rec["meta"])

    def verify(self, rec):
        if not os.path.isdir(rec["package_dir"]) or not os.path.isfile(rec["meta"]):
            return ["missing"]
        return [] if content_digest(rec["package_dir"]) == rec["content_digest"] else ["content_drift"]
