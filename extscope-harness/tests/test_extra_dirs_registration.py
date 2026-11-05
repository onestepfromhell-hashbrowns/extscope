#!/usr/bin/env python3
"""C-03 / T-02 方案 3 — 設定檔主動登記 (extra_dirs) 單元測試。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import importlib

project_harness = importlib.import_module("project-harness")


class TestExtraDirsRegistration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_extradirs_")
        self.test_path = Path(self.test_dir)
        self.config_file = self.test_path / "config.toml"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_creates_extra_dirs_when_file_absent(self):
        changed = project_harness.ensure_extra_dirs_registration(
            self.config_file, ["/workspace/.agents/skills"], verbose=False
        )
        self.assertTrue(changed)
        content = self.config_file.read_text(encoding="utf-8")
        self.assertIn('extra_dirs = ["/workspace/.agents/skills"]', content)

    def test_incremental_injection_preserves_existing_keys(self):
        self.config_file.write_text(
            '[projects."/workspace"]\ntrust_level = "trusted"\n', encoding="utf-8"
        )
        project_harness.ensure_extra_dirs_registration(
            self.config_file, ["/workspace/.agents/skills"], verbose=False
        )
        content = self.config_file.read_text(encoding="utf-8")
        self.assertIn('trust_level = "trusted"', content)
        self.assertIn("extra_dirs", content)

    def test_idempotent_does_not_duplicate(self):
        project_harness.ensure_extra_dirs_registration(
            self.config_file, ["/workspace/.agents/skills"], verbose=False
        )
        changed_again = project_harness.ensure_extra_dirs_registration(
            self.config_file, ["/workspace/.agents/skills"], verbose=False
        )
        self.assertFalse(changed_again)
        content = self.config_file.read_text(encoding="utf-8")
        self.assertEqual(content.count("/workspace/.agents/skills"), 1)

    def test_merges_new_dir_into_existing_array(self):
        project_harness.ensure_extra_dirs_registration(self.config_file, ["/a"], verbose=False)
        project_harness.ensure_extra_dirs_registration(self.config_file, ["/a", "/b"], verbose=False)
        content = self.config_file.read_text(encoding="utf-8")
        self.assertIn('"/a"', content)
        self.assertIn('"/b"', content)

    def test_result_is_valid_toml(self):
        self.config_file.write_text(
            '[projects."/workspace"]\ntrust_level = "trusted"\n', encoding="utf-8"
        )
        project_harness.ensure_extra_dirs_registration(self.config_file, ["/a", "/b"], verbose=False)

        import tomllib

        with open(self.config_file, "rb") as f:
            parsed = tomllib.load(f)
        self.assertEqual(parsed["extra_dirs"], ["/a", "/b"])
        self.assertEqual(parsed["projects"]["/workspace"]["trust_level"], "trusted")

    def test_wired_into_apply_harness_on_tier2_downgrade(self):
        """整合：manifest 宣告 extra_dirs_registration 時，Tier 2 降級應觸發設定檔登記。"""
        canonical_root = self.test_path / "canonical"
        (canonical_root / "skills" / "demo-skill").mkdir(parents=True)
        (canonical_root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        workspace = self.test_path / "workspace"
        user_home = self.test_path / "root"

        manifest = {
            "harness_id": "test-tiered",
            "tiered": True,
            "projections": {
                "skills": {
                    "mode": "symlink_children",
                    "target_dir": "./.agents/skills",
                    "extra_dirs_registration": {"config_file": "~/.codex/config.toml"},
                }
            },
        }

        project_harness.apply_harness("test-tiered", manifest, canonical_root, workspace, user_home, verbose=False)

        config_file = user_home / ".codex" / "config.toml"
        self.assertTrue(config_file.exists())
        content = config_file.read_text(encoding="utf-8")
        self.assertIn("extra_dirs", content)
        self.assertIn(str(workspace / ".agents" / "skills"), content)


if __name__ == "__main__":
    unittest.main()
