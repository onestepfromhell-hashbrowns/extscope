#!/usr/bin/env python3
"""T-03 / S-05 — 沙箱存取完整性探針 (sandbox_probe) 單元與負面測試。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
from sandbox_probe import probe_sandbox_access  # noqa: E402


class TestSandboxProbe(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_probe_")
        self.test_path = Path(self.test_dir)
        self.workspace = self.test_path / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_ok_when_skill_md_present_and_readable(self):
        skill_dir = self.workspace / "demo-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        result = probe_sandbox_access(skill_dir, [str(self.workspace)])
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "ok")

    def test_fails_when_target_missing(self):
        missing = self.workspace / "does-not-exist"
        result = probe_sandbox_access(missing, [str(self.workspace)])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "target_not_found")

    def test_fails_on_empty_skill_md(self):
        skill_dir = self.workspace / "empty-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").touch()

        result = probe_sandbox_access(skill_dir, [str(self.workspace)])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "skill_md_empty")

    def test_fails_on_dangling_symlink(self):
        skill_dir = self.workspace / "dangling-skill"
        skill_dir.mkdir()
        os.symlink(self.workspace / "nonexistent-target.md", skill_dir / "SKILL.md")

        result = probe_sandbox_access(skill_dir, [str(self.workspace)])
        self.assertFalse(result.ok)

    def test_fails_on_out_of_bounds_symlink(self):
        outside_root = self.test_path / "outside"
        outside_root.mkdir()
        outside_target = outside_root / "SKILL.md"
        outside_target.write_text("# Outside\n", encoding="utf-8")

        skill_dir = self.workspace / "escape-skill"
        skill_dir.mkdir()
        os.symlink(outside_target, skill_dir / "SKILL.md")

        result = probe_sandbox_access(skill_dir, [str(self.workspace)])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "path_traversal_blocked")

    def test_fails_on_permission_denied(self):
        skill_dir = self.workspace / "locked-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Locked\n", encoding="utf-8")
        os.chmod(skill_md, 0o000)

        try:
            if os.geteuid() == 0:
                self.skipTest("running as root bypasses permission bits")
            result = probe_sandbox_access(skill_dir, [str(self.workspace)])
            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "permission_denied")
        finally:
            os.chmod(skill_md, 0o644)

    def test_only_checks_direct_child_not_nested_skill_md(self):
        """Standards 修正：呼叫契約為單一 Skill 目錄，只檢查直接底下的 SKILL.md。

        修正前以 `rglob` 遞迴搜尋，只要樹狀結構任一深處存在 SKILL.md 即回報通過，
        對於「該目錄本身缺少 SKILL.md，但巢狀子目錄剛好有一份」的情境會產生
        假性通過 (blind spot)。
        """
        skill_dir = self.workspace / "container-skill"
        nested = skill_dir / "some-other-nested-dir"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("# Nested\n", encoding="utf-8")
        # skill_dir 自己底下沒有 SKILL.md。

        result = probe_sandbox_access(skill_dir, [str(self.workspace)])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "skill_md_missing")

    def test_cli_exits_2_on_failure(self):
        import subprocess

        skill_dir = self.workspace / "cli-fail-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").touch()

        probe_script = str(Path(__file__).resolve().parent.parent / "adapters" / "sandbox_probe.py")
        proc = subprocess.run(
            [sys.executable, probe_script, "--target-dir", str(skill_dir), "--allowed-prefix", str(self.workspace)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("skill_md_empty", proc.stdout)


if __name__ == "__main__":
    unittest.main()
