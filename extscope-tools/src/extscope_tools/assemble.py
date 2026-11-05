"""Canonical release assembly: copy a base release, overlay new content, rewrite DELIVERY.json in one
fixed format, check the result with `canonical.check_release`, then publish under a content-derived name.
Never touches `current` or `skills` (switching is the operator's step). The output is a new directory;
the base and every source are only read."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from . import canonical

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git")
PREFIX = "host-release-"


class AssembleError(Exception):
    pass


def _rel_ok(rel):
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and ".." not in parts and "" not in parts and rel != "DELIVERY.json"


def source_label(src):
    """`<repo>@<short sha>`, with `+dirty` when the repo has uncommitted changes under `src`."""
    def git(*args):
        return subprocess.run(["git", "-C", src if os.path.isdir(src) else os.path.dirname(src), *args],
                              capture_output=True, text=True, check=False)
    top = git("rev-parse", "--show-toplevel")
    sha = git("rev-parse", "--short=7", "HEAD")
    if top.returncode or sha.returncode:
        raise AssembleError(f"{src}: not inside a git repository; pass --source LABEL=VALUE instead")
    dirty = git("status", "--porcelain", "--", os.path.abspath(src)).stdout.strip()
    return os.path.basename(top.stdout.strip()), sha.stdout.strip() + ("+dirty" if dirty else "")


def _tree_id(path):
    """Content id for a source that has no git history: sha256 over its file digests."""
    files = {}
    for base, dirs, names in os.walk(path):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(base, name)
            files[os.path.relpath(full, path)] = canonical._sha(full)
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]


def _digest_files(release):
    files = {}
    for base, dirs, names in os.walk(release):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, release)
            if rel == "DELIVERY.json" or os.path.islink(full):
                continue
            files[rel] = canonical._sha(full)
    return files


def assemble(base, out_root, adds, sources=None, allow_dirty=False, allow_untracked=False):
    """adds: [(src path, release-relative dest)]. Returns the DELIVERY dict; raises AssembleError."""
    base, out_root = os.path.realpath(base), os.path.realpath(out_root)
    try:
        with open(os.path.join(base, "DELIVERY.json"), encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssembleError(f"base DELIVERY.json unreadable: {type(exc).__name__}")
    if canonical.check_release(base):
        raise AssembleError("base release fails the canonical check; fix or pick another base first")
    labels = {"base": base}
    for src, dest in adds:
        if not _rel_ok(dest):
            raise AssembleError(f"bad destination {dest!r}: relative, no '..', not DELIVERY.json")
        if not os.path.exists(src):
            raise AssembleError(f"{src}: no such source")
        if canonical._inside(src, base) or canonical._inside(src, out_root):
            raise AssembleError(f"{src}: a source must not be inside the base release or the output root")
    os.makedirs(out_root, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".assemble-", dir=out_root)
    try:
        tree = os.path.join(stage, "release")
        shutil.copytree(base, tree, symlinks=True)
        os.unlink(os.path.join(tree, "DELIVERY.json"))
        updated = []
        for src, dest in adds:
            target = os.path.join(tree, dest)
            if os.path.lexists(target):
                shutil.rmtree(target) if os.path.isdir(target) and not os.path.islink(target) else os.unlink(target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, target, ignore=IGNORE)
            else:
                shutil.copy2(src, target)
            try:
                repo, ref = source_label(src)
            except AssembleError:
                if not allow_untracked:
                    raise
                repo, ref = os.path.basename(os.path.realpath(src)), "untracked:" + _tree_id(target)
            if ref.endswith("+dirty") and not allow_dirty:
                raise AssembleError(f"{src}: uncommitted changes in {repo}; commit first or pass --allow-dirty")
            labels[repo] = ref
            if dest.startswith("skills/") and dest.count("/") == 1:
                updated.append(dest.split("/", 1)[1])
        labels.update(sources or {})
        files = _digest_files(tree)
        gen = PREFIX + hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
        final = os.path.join(out_root, gen)
        if os.path.exists(final):
            raise AssembleError(f"{final} already exists (same content produces the same name)")
        skills_dir = os.path.join(tree, "skills")
        delivery = {k: v for k, v in prev.items() if k not in ("files", "sources", "updated_skills", "generation", "release", "base_generation", "skills")}
        delivery.update(
            base_generation=prev.get("generation") or os.path.basename(base),
            generation=gen,
            release=final,
            files=files,
            sources=labels,
            skills=sorted(d for d in os.listdir(skills_dir) if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md"))) if os.path.isdir(skills_dir) else [],
            updated_skills=sorted(set(updated)),
        )
        with open(os.path.join(tree, "DELIVERY.json"), "w", encoding="utf-8") as fh:
            json.dump(delivery, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        bad = canonical.check_release(tree)
        if bad:
            raise AssembleError("assembled release fails the canonical check: " + json.dumps(bad, ensure_ascii=False))
        os.rename(tree, final)
        return delivery
    finally:
        shutil.rmtree(stage, ignore_errors=True)
