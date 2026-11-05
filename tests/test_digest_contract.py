"""Content identity contract with an independent record oracle and real I/O."""
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys

import pytest

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / "src"))
import extscope.digest as digest


def oracle(records):
    payload = ["extscope.tree.v1", sorted(records, key=lambda r: r[1])]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True,
                                    separators=(",", ":")).encode("ascii")).hexdigest()


def test_exact_oracle_includes_empty_dirs_names_modes_and_links(tmp_path):
    tmp_path.chmod(0o755)
    (tmp_path / 'a:\n☃').write_bytes(b"hello\x00world")
    (tmp_path / 'a:\n☃').chmod(0o640)
    (tmp_path / "empty").mkdir(mode=0o750)
    (tmp_path / "empty").chmod(0o750)
    (tmp_path / "dangling").symlink_to("missing")
    expected = oracle([["d", "", 0o755], ["d", "empty", 0o750],
        ["f", 'a:\n☃', 0o640, hashlib.sha256(b"hello\x00world").hexdigest()],
        ["l", "dangling", "missing"]])
    assert digest.hash_tree(tmp_path) == expected


def test_mtime_is_not_content_identity(tmp_path):
    f = tmp_path / "asset"; f.write_text("content")
    before = digest.hash_tree(tmp_path)
    os.utime(f, ns=(10_000_000_000, 20_000_000_000))
    os.utime(tmp_path, ns=(30_000_000_000, 40_000_000_000))
    assert digest.hash_tree(tmp_path) == before


@pytest.mark.parametrize("change", ["bytes", "name", "file_mode", "dir_mode", "empty_dir"])
def test_content_structure_and_permissions_change_identity(tmp_path, change):
    f = tmp_path / "asset"; f.write_bytes(b"same-size"); f.chmod(0o644)
    before = digest.hash_tree(tmp_path)
    if change == "bytes": f.write_bytes(b"new-bytes")
    elif change == "name": f.rename(tmp_path / "renamed")
    elif change == "file_mode": f.chmod(0o744)
    elif change == "dir_mode": tmp_path.chmod(0o750)
    else: (tmp_path / "empty").mkdir()
    assert digest.hash_tree(tmp_path) != before


@pytest.mark.parametrize("name,contents", [
    (".meta.json", '{"content_digest":"' + "0" * 64 + '"}'),
    ("package.json", '{"name":"unchanged-metadata","version":"1"}'),
    (".extscope-managed.json", '{"managed":true}')])
def test_metadata_cannot_hide_changed_file_bytes(tmp_path, name, contents):
    (tmp_path / name).write_text(contents)
    f = tmp_path / "asset"; f.write_text("one")
    before = digest.hash_tree(tmp_path)
    f.write_text("two")
    assert digest.hash_tree(tmp_path) != before


def test_metadata_bytes_are_themselves_covered(tmp_path):
    f = tmp_path / ".meta.json"; f.write_text('{}')
    before = digest.hash_tree(tmp_path)
    f.write_text('{"content_digest":"claimed"}')
    assert digest.hash_tree(tmp_path) != before


def test_external_directory_link_is_not_followed(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    external = tmp_path / "external"; external.mkdir()
    (external / "secret").write_text("first")
    (root / "link").symlink_to(external, target_is_directory=True)
    before = digest.hash_tree(root)
    (external / "secret").write_text("changed outside root")
    assert digest.hash_tree(root) == before
    (root / "link").unlink(); (root / "link").symlink_to("other")
    assert digest.hash_tree(root) != before


@pytest.mark.parametrize("kind", ["relative", "file", "symlink", "missing", "fifo_root"])
def test_invalid_roots_fail(tmp_path, monkeypatch, kind):
    root = tmp_path
    if kind == "relative":
        monkeypatch.chdir(tmp_path); root = Path(".")
    elif kind == "file": root = tmp_path / "file"; root.write_text("file")
    elif kind == "symlink": root = tmp_path / "link"; root.symlink_to(tmp_path)
    elif kind == "missing": root = tmp_path / "missing"
    else: root = tmp_path / "fifo"; os.mkfifo(root)
    with pytest.raises((ValueError, OSError)):
        digest.hash_tree(root)


@pytest.mark.parametrize("kind", ["fifo", "socket"])
def test_special_files_are_rejected_without_opening_them(tmp_path, kind, monkeypatch):
    server = None
    try:
        if kind == "fifo": os.mkfifo(tmp_path / "special")
        else:
            monkeypatch.chdir(tmp_path)  # AF_UNIX sun_path is limited, including fixture prefixes.
            server = socket.socket(socket.AF_UNIX); server.bind("special")
        with pytest.raises(ValueError): digest.hash_tree(tmp_path)
    finally:
        if server: server.close()


def test_read_denied_is_not_silently_skipped(tmp_path, monkeypatch):
    (tmp_path / "asset").write_text("must be read")
    original = digest.os.open
    def denied(path, *args, **kwargs):
        if os.fspath(path) == "asset": raise PermissionError("fixture denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(digest.os, "open", denied)
    with pytest.raises(PermissionError): digest.hash_tree(tmp_path)


def test_entry_swap_to_external_symlink_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    (root / "asset").write_text("first")
    outside = tmp_path / "outside"; outside.write_text("not part of root")
    original = digest.os.open; changed = False
    def swapped(path, *args, **kwargs):
        nonlocal changed
        if os.fspath(path) == "asset" and not changed:
            changed = True; (root / "asset").unlink(); (root / "asset").symlink_to(outside)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(digest.os, "open", swapped)
    with pytest.raises((OSError, RuntimeError)): digest.hash_tree(root)
    assert changed


def test_observed_directory_mutation_is_not_accepted(tmp_path, monkeypatch):
    (tmp_path / "asset").write_text("first")
    original = digest.os.listdir; changed = False
    def mutated(fd):
        nonlocal changed
        result = original(fd)
        if not changed:
            changed = True; (tmp_path / "new-after-list").write_text("not listed")
        return result
    monkeypatch.setattr(digest.os, "listdir", mutated)
    with pytest.raises(RuntimeError): digest.hash_tree(tmp_path)


def test_file_descriptors_close_on_success_and_failure(tmp_path):
    (tmp_path / "asset").write_bytes(b"bytes" * 1000)
    before = len(os.listdir('/proc/self/fd'))
    for _ in range(25): digest.hash_tree(tmp_path)
    assert len(os.listdir('/proc/self/fd')) == before
    os.mkfifo(tmp_path / "unsupported")
    for _ in range(25):
        with pytest.raises(ValueError): digest.hash_tree(tmp_path)
    assert len(os.listdir('/proc/self/fd')) == before


@pytest.mark.parametrize("suffix", ["/", "/.", "/../link"])
def test_root_symlink_cannot_bypass_nofollow_with_spelling(tmp_path, suffix):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real", target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        digest.hash_tree(str(tmp_path / "link") + suffix)


def test_relative_symlink_text_is_identity(tmp_path):
    (tmp_path / "target").write_text("same bytes")
    link = tmp_path / "link"; link.symlink_to("target")
    before = digest.hash_tree(tmp_path)
    link.unlink(); link.symlink_to("./target")
    assert digest.hash_tree(tmp_path) != before
