"""Filesystem primitives: digests, atomic replace, sentinel markdown blocks."""
import hashlib
import os
import re
import shutil


def digest(path):
    """sha256 of a file; for a directory, of sorted relative names + file digests
    (symlinks hash their link text). Returns None if path is absent."""
    if os.path.islink(path):
        return "symlink:" + os.readlink(path)
    if os.path.isfile(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    if os.path.isdir(path):
        h = hashlib.sha256()
        for base, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files + [d for d in dirs if os.path.islink(os.path.join(base, d))]):
                p = os.path.join(base, name)
                h.update(os.path.relpath(p, path).replace(os.sep, "/").encode() + b"\0")
                h.update(digest(p).encode() + b"\0")
        return "tree:" + h.hexdigest()
    return None


def kind(path):
    if os.path.islink(path):
        return "symlink"
    if os.path.isdir(path):
        return "dir"
    if os.path.isfile(path):
        return "file"
    return "absent"


def remove(path):
    k = kind(path)
    if k == "dir":
        shutil.rmtree(path)
    elif k != "absent":
        os.unlink(path)


def tmp_sibling(path, tag):
    return os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.extscope-{tag}")


def stash(path, dest):
    """Move an existing target into the backup area (rename if same FS, else copy)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.islink(path):
        with open(dest + ".link", "w", encoding="utf-8") as fh:
            fh.write(os.readlink(path))
        os.unlink(path)
        return dest + ".link"
    try:
        os.replace(path, dest)
    except OSError:
        if os.path.isdir(path):
            shutil.copytree(path, dest, symlinks=True)
        else:
            shutil.copy2(path, dest)
        remove(path)
    return dest


def unstash(backup, path):
    if backup.endswith(".link"):
        with open(backup, encoding="utf-8") as fh:
            os.symlink(fh.read(), path)
        return
    try:
        os.replace(backup, path)
    except OSError:
        if os.path.isdir(backup):
            shutil.copytree(backup, path, symlinks=True)
        else:
            shutil.copy2(backup, path)


def _block_re(block_id):
    b = re.escape(block_id)
    return re.compile(rf"<!-- block: {b} -->.*?<!-- /block: {b} -->\n?", re.S)


def render_block(block_id, text):
    return f"<!-- block: {block_id} -->\n{text.strip()}\n<!-- /block: {block_id} -->\n"


def upsert_block(existing, block_id, text):
    block = render_block(block_id, text)
    rx = _block_re(block_id)
    if rx.search(existing):
        return rx.sub(lambda _m: block, existing, count=1)
    sep = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block


def block_text(content, block_id):
    m = _block_re(block_id).search(content)
    return m.group(0) if m else None
