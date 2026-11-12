#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""T-02 / T-04 - tiered dispatcher and fail-closed gate integration tests (S-02, S-03, S-05)."""

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

Tier = project_harness.Tier  # project-harness.py contains a hyphen and cannot be imported directly; take it from the module attribute


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
        """S-02: generic default first - when the probe passes, keep the Tier 1 symlink and do not downgrade.

        The probe's allowed_prefixes must reflect what the sandbox can actually see; here we directly specify
        that `src.parent` is also within the allowed range, simulating a sandbox with no boundary restriction for this path.
        """
        tier = project_harness.dispatch_tiered(
            self.src, self.dst, [str(self.workspace), str(self.src.parent)], verbose=False
        )
        self.assertEqual(tier, Tier.TIER1_SYMLINK)
        self.assertTrue(self.dst.is_symlink())

    def test_s03_falls_back_to_tier2_when_probe_blocked(self):
        """S-03: symlink-escape sandbox simulation - when the probe reports a block, automatically downgrade to a physical dereferenced copy; the path is unchanged."""
        blocked = ProbeResult(ok=False, reason=ProbeReason.PATH_TRAVERSAL_BLOCKED, path=str(self.dst))
        with mock.patch.object(project_harness, "probe_sandbox_access", return_value=blocked):
            tier = project_harness.dispatch_tiered(self.src, self.dst, [str(self.workspace)], verbose=False)

        self.assertEqual(tier, Tier.TIER2_MATERIALIZED)
        self.assertFalse(self.dst.is_symlink())
        self.assertTrue((self.dst / "SKILL.md").exists())
        self.assertEqual(self.dst, self.workspace / ".agents" / "skills" / "demo-skill")

    def test_apply_harness_tiered_manifest_end_to_end_downgrades_when_canonical_outside_workspace(self):
        """Integration: when canonical_root is outside the workspace (the normal real deployment), the probe must detect the escape and downgrade to Tier 2.

        Defect before the fix: allowed_prefixes wrongly listed canonical_root as an allowed prefix,
        so this escape, which should have been detected, was judged "visible" and Tier 2 never triggered.
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
        self.assertFalse(target_skill.is_symlink(), "canonical_root is outside the workspace; the escape should be detected and downgraded to a physical dereferenced copy")
        self.assertTrue((target_skill / "SKILL.md").exists())

    def test_dispatch_tiered_scheme2_fallback_target_dir(self):
        """C-02 option 2: when the probe is blocked and the manifest provides fallback_target_dir, additionally copy to that dedicated directory."""
        fallback_dir = self.test_path / "codex-fallback" / "skills"
        blocked = ProbeResult(ok=False, reason=ProbeReason.PATH_TRAVERSAL_BLOCKED, path=str(self.dst))
        with mock.patch.object(project_harness, "probe_sandbox_access", return_value=blocked):
            tier = project_harness.dispatch_tiered(
                self.src, self.dst, [str(self.workspace)], verbose=False, fallback_target_dir=fallback_dir
            )

        self.assertEqual(tier, Tier.TIER2_MATERIALIZED)
        self.assertTrue((fallback_dir / self.src.name / "SKILL.md").exists())

    def test_legacy_manifest_unaffected_by_tiered_logic(self):
        """AC-1 zero breakage: an existing manifest that does not declare tiered still takes the original ensure_symlink path."""
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
    """T-04 / S-05: fail-closed gate integration negative tests."""

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
        """Standards fix: a missing target_dir must be reported as a failure, never silently treated as "nothing to verify"."""
        manifest = {
            "tiered": True,
            "projections": {"skills": {"mode": "symlink_children", "target_dir": "./.agents/skills"}},
        }
        # Deliberately do not call apply_harness; target_dir is never created.
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

        # canonical_root is outside the workspace -> Tier 2 already made a physical dereferenced copy inside apply_harness,
        # and the target file is decoupled from the source, so tampering must be applied to the "projected target", not the source.
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

        # Tamper with the source so the projected SKILL.md is empty (the Tier 2 materialized copy will copy this empty content).
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
