#!/usr/bin/env python3
"""T-02 / T-04 — 階梯式分發調度器與 Fail-Closed 門禁整合測試 (S-02, S-03, S-05)。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import importlib

project_harness = importlib.import_module("project-harness")
from sandbox_probe import ProbeReason, ProbeResult  # noqa: E402

Tier = project_harness.Tier  # project-harness.py 含連字號，無法直接 import，改由模組屬性取得


class TestTieredDispatch(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_tiered_")
        self.test_path = Path(self.test_dir)

        self.src = self.test_path / "src-skill"
        self.src.mkdir()
        (self.src / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        self.workspace = self.test_path / "workspace"
        self.workspace.mkdir()
        self.dst = self.workspace / ".agents" / "skills" / "demo-skill"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_s02_tier1_used_when_probe_passes(self):
        """S-02: 通用預設優先 — 探針通過時維持 Tier 1 symlink，不觸發降級。

        探針的 allowed_prefixes 必須反映沙箱實際可見範圍；此處直接指定
        `src.parent` 亦在允許範圍內，模擬「該沙箱對此路徑確實無邊界限制」的情境。
        """
        tier = project_harness.dispatch_tiered(
            self.src, self.dst, [str(self.workspace), str(self.src.parent)], verbose=False
        )
        self.assertEqual(tier, Tier.TIER1_SYMLINK)
        self.assertTrue(self.dst.is_symlink())

    def test_s03_falls_back_to_tier2_when_probe_blocked(self):
        """S-03: 軟連結越界沙箱模擬 — 探針回報阻斷時自動降級為實體解引用，路徑不變。"""
        blocked = ProbeResult(ok=False, reason=ProbeReason.PATH_TRAVERSAL_BLOCKED, path=str(self.dst))
        with mock.patch.object(project_harness, "probe_sandbox_access", return_value=blocked):
            tier = project_harness.dispatch_tiered(self.src, self.dst, [str(self.workspace)], verbose=False)

        self.assertEqual(tier, Tier.TIER2_MATERIALIZED)
        self.assertFalse(self.dst.is_symlink())
        self.assertTrue((self.dst / "SKILL.md").exists())
        self.assertEqual(self.dst, self.workspace / ".agents" / "skills" / "demo-skill")

    def test_apply_harness_tiered_manifest_end_to_end_downgrades_when_canonical_outside_workspace(self):
        """整合：canonical_root 位於工作區以外時 (真實部署常態)，探針必須偵測越界並降級為 Tier 2。

        修正前的缺陷：allowed_prefixes 誤將 canonical_root 一併列為允許前綴，
        導致這個本應偵測到的越界情境被錯誤判定為「可視」，Tier 2 永遠不會觸發。
        """
        canonical_root = self.test_path / "canonical"
        (canonical_root / "skills" / "demo-skill").mkdir(parents=True)
        (canonical_root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        manifest = {
            "harness_id": "test-tiered",
            "name": "Test Tiered Harness",
            "tiered": True,
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}},
        }

        project_harness.apply_harness(
            "test-tiered", manifest, canonical_root, self.workspace, self.test_path / "root", verbose=False
        )

        target_skill = self.workspace / ".agents" / "skills" / "demo-skill"
        self.assertTrue(target_skill.exists())
        self.assertFalse(target_skill.is_symlink(), "canonical_root 在工作區以外，應偵測越界並降級為實體解引用")
        self.assertTrue((target_skill / "SKILL.md").exists())

    def test_dispatch_tiered_scheme2_fallback_target_dir(self):
        """C-02 方案 2：探針受阻時，若 manifest 提供 fallback_target_dir，額外複製一份至該專屬目錄。"""
        fallback_dir = self.test_path / "codex-fallback" / "skills"
        blocked = ProbeResult(ok=False, reason=ProbeReason.PATH_TRAVERSAL_BLOCKED, path=str(self.dst))
        with mock.patch.object(project_harness, "probe_sandbox_access", return_value=blocked):
            tier = project_harness.dispatch_tiered(
                self.src, self.dst, [str(self.workspace)], verbose=False, fallback_target_dir=fallback_dir
            )

        self.assertEqual(tier, Tier.TIER2_MATERIALIZED)
        self.assertTrue((fallback_dir / self.src.name / "SKILL.md").exists())

    def test_legacy_manifest_unaffected_by_tiered_logic(self):
        """AC-1 零破壞：未宣告 tiered 的既有 manifest 仍走原始 ensure_symlink 路徑。"""
        canonical_root = self.test_path / "canonical"
        (canonical_root / "skills" / "demo-skill").mkdir(parents=True)
        (canonical_root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        manifest = {
            "harness_id": "legacy",
            "name": "Legacy Harness",
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}},
        }

        with mock.patch.object(project_harness, "probe_sandbox_access") as mocked_probe:
            project_harness.apply_harness(
                "legacy", manifest, canonical_root, self.workspace, self.test_path / "root", verbose=False
            )
            mocked_probe.assert_not_called()

        target_skill = self.workspace / ".agents" / "skills" / "demo-skill"
        self.assertTrue(target_skill.is_symlink())


class TestFailClosedGate(unittest.TestCase):
    """T-04 / S-05: Fail-Closed 門禁整合負面測試。"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_failclosed_")
        self.test_path = Path(self.test_dir)

        self.canonical_root = self.test_path / "canonical"
        (self.canonical_root / "skills" / "demo-skill").mkdir(parents=True)
        (self.canonical_root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        self.workspace = self.test_path / "workspace"
        self.user_home = self.test_path / "root"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_verify_harness_returns_empty_for_legacy_manifest(self):
        manifest = {
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}}
        }
        project_harness.apply_harness(
            "legacy", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )
        failures = project_harness.verify_harness(manifest, self.workspace, self.user_home, verbose=False)
        self.assertEqual(failures, [])

    def test_verify_harness_fail_closed_on_missing_target_dir(self):
        """Standards 修正：target_dir 不存在時必須回報失敗，不可靜默視為「無需驗證」。"""
        manifest = {
            "tiered": True,
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}},
        }
        # 故意不呼叫 apply_harness，target_dir 從未被建立。
        failures = project_harness.verify_harness(
            manifest, self.workspace, self.user_home, verbose=False
        )
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, ProbeReason.TARGET_NOT_FOUND)

    def test_verify_harness_detects_broken_skill_md(self):
        manifest = {
            "tiered": True,
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}},
        }
        project_harness.apply_harness(
            "tiered", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        # canonical_root 在工作區以外 → Tier 2 已於 apply_harness 內完成實體解引用拷貝，
        # 目標檔案已與來源脫鉤，因此人為破壞必須施加於「已投影的目標」而非來源。
        target_skill_md = self.workspace / ".agents" / "skills" / "demo-skill" / "SKILL.md"
        self.assertTrue(target_skill_md.exists())
        self.assertFalse((self.workspace / ".agents" / "skills" / "demo-skill").is_symlink())
        target_skill_md.write_text("", encoding="utf-8")

        failures = project_harness.verify_harness(
            manifest, self.workspace, self.user_home, verbose=False
        )
        self.assertTrue(len(failures) >= 1)
        self.assertEqual(failures[0].reason, ProbeReason.SKILL_MD_EMPTY)

    def test_main_exits_2_on_fail_closed(self):
        manifests_dir = self.test_path / "manifests"
        manifests_dir.mkdir()
        manifest = {
            "harness_id": "broken",
            "tiered": True,
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}},
        }
        (manifests_dir / "harness-broken.json").write_text(json.dumps(manifest), encoding="utf-8")

        # 人為破壞來源，使投影後的 SKILL.md 為空 (Tier 2 材化拷貝會複製此空內容)。
        (self.canonical_root / "skills" / "demo-skill" / "SKILL.md").write_text("", encoding="utf-8")

        argv_backup = sys.argv
        sys.argv = [
            "project-harness.py",
            "--harness",
            "broken",
            "--canonical-root",
            str(self.canonical_root),
            "--manifests-dir",
            str(manifests_dir),
            "--workspace",
            str(self.workspace),
            "--user-home",
            str(self.user_home),
        ]
        try:
            exit_code = project_harness.main()
        finally:
            sys.argv = argv_backup

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
