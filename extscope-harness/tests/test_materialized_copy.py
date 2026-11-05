#!/usr/bin/env python3
"""T-01 / S-04 — 實體解引用拷貝 (materialized_copy) 單元測試。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import importlib

project_harness = importlib.import_module("project-harness")


class TestMaterializedCopy(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_matcopy_")
        self.test_path = Path(self.test_dir)

        self.src = self.test_path / "src-skill"
        (self.src / "nested").mkdir(parents=True)
        (self.src / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
        (self.src / "nested" / "helper.md").write_text("helper content\n", encoding="utf-8")

        # src 內含一個軟連結，驗證 copytree(symlinks=False) 會解引用它。
        real_target = self.test_path / "real-file.txt"
        real_target.write_text("real content\n", encoding="utf-8")
        os.symlink(real_target, self.src / "linked.txt")

        self.dst = self.test_path / "dst-skill"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_copy_produces_zero_symlinks(self):
        changed = project_harness.ensure_materialized_copy(self.src, self.dst, verbose=False)
        self.assertTrue(changed)

        for root, _dirs, files in os.walk(self.dst):
            root_path = Path(root)
            self.assertFalse(root_path.is_symlink())
            for name in files:
                self.assertFalse((root_path / name).is_symlink())

        self.assertEqual((self.dst / "linked.txt").read_text(encoding="utf-8"), "real content\n")
        self.assertEqual((self.dst / "nested" / "helper.md").read_text(encoding="utf-8"), "helper content\n")

    def test_copy_preserves_content_byte_for_byte(self):
        project_harness.ensure_materialized_copy(self.src, self.dst, verbose=False)
        self.assertEqual(
            (self.dst / "SKILL.md").read_text(encoding="utf-8"),
            (self.src / "SKILL.md").read_text(encoding="utf-8"),
        )

    def test_copy_normalizes_permissions(self):
        os.chmod(self.src / "SKILL.md", 0o400)
        project_harness.ensure_materialized_copy(self.src, self.dst, verbose=False)
        mode = (self.dst / "SKILL.md").stat().st_mode & 0o777
        self.assertEqual(mode & 0o644, 0o644)

    def test_copy_is_idempotent(self):
        first = project_harness.ensure_materialized_copy(self.src, self.dst, verbose=False)
        second = project_harness.ensure_materialized_copy(self.src, self.dst, verbose=False)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_replaces_existing_symlink_target(self):
        self.dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(self.src, self.dst)
        self.assertTrue(self.dst.is_symlink())

        project_harness.ensure_materialized_copy(self.src, self.dst, verbose=False)
        self.assertFalse(self.dst.is_symlink())
        self.assertTrue((self.dst / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
