#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit and integration tests - Agent Harness Adapter."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Load the module under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import importlib
project_harness = importlib.import_module("project-harness")


class TestAgentHarnessAdapter(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_harness_")
        self.test_path = Path(self.test_dir)

        self.canonical_root = self.test_path / "canonical"
        self.workspace = self.test_path / "workspace"
        self.user_home = self.test_path / "root"
        self.manifests_dir = Path(__file__).resolve().parent.parent / "manifests"

        # Create neutral capability assets
        skills_dir = self.canonical_root / "skills" / "demo-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# Demo Skill\n")

        rules_dir = self.canonical_root / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "demo-rule.md").write_text("# Demo Rule\nMust follow guidelines.\n")

        frags_dir = self.canonical_root / "fragments"
        frags_dir.mkdir(parents=True)
        (frags_dir / "agent-constitution.md").write_text("# Agent Constitution\nAct responsibly.\n")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_antigravity_projection(self):
        """Verify the projection contract of the Antigravity harness"""
        manifest_file = self.manifests_dir / "harness-antigravity.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        project_harness.apply_harness(
            "antigravity", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        # Verify the skill symlink
        target_skill = self.workspace / ".agents" / "skills" / "demo-skill"
        self.assertTrue(target_skill.is_symlink())
        self.assertTrue((target_skill / "SKILL.md").exists())

        # Verify the rule symlink
        target_rule = self.workspace / ".agents" / "rules" / "demo-rule.md"
        self.assertTrue(target_rule.is_symlink())
        self.assertTrue(target_rule.exists())

        # Verify fragment Markdown injection
        target_md = self.workspace / ".agents" / "AGENTS.md"
        self.assertTrue(target_md.exists())
        content = target_md.read_text(encoding="utf-8")
        self.assertIn("<!-- block: agent-constitution -->", content)
        self.assertIn("Act responsibly.", content)

    def test_codex_projection(self):
        """Verify the projection contract and auto-trust mechanism of the OpenAI Codex harness"""
        manifest_file = self.manifests_dir / "harness-codex.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        project_harness.apply_harness(
            "codex", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        # Verify the skill symlink (Codex natively reads .agents/skills)
        target_skill = self.workspace / ".agents" / "skills" / "demo-skill"
        self.assertTrue(target_skill.is_symlink())

        # Verify the rule symlink (.codex/rules)
        target_rule = self.workspace / ".codex" / "rules" / "demo-rule.md"
        self.assertTrue(target_rule.is_symlink())

        # Verify fragment Markdown injection (AGENTS.md at the workspace root)
        target_md = self.workspace / "AGENTS.md"
        self.assertTrue(target_md.exists())
        content = target_md.read_text(encoding="utf-8")
        self.assertIn("<!-- block: agent-constitution -->", content)

        # Verify the /workspace auto-trust entry in Codex config.toml
        config_toml = self.user_home / ".codex" / "config.toml"
        self.assertTrue(config_toml.exists())
        toml_content = config_toml.read_text(encoding="utf-8")
        self.assertIn(f'[projects."{self.workspace}"]', toml_content)
        self.assertIn('trust_level = "trusted"', toml_content)

    def test_codex_host_projection(self):
        """Host Codex projects skills/rules into CODEX_HOME, not workspace paths."""
        manifest_file = self.manifests_dir / "harness-codex-host.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        project_harness.apply_harness(
            "codex-host", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        self.assertTrue((self.user_home / "skills" / "demo-skill").is_symlink())
        self.assertTrue((self.user_home / "rules" / "demo-rule.md").is_symlink())
        self.assertFalse((self.workspace / ".agents" / "skills" / "demo-skill").exists())
        config_toml = self.user_home / "config.toml"
        toml_content = config_toml.read_text(encoding="utf-8")
        for tp in manifest.get("runtime_bootstrap", {}).get("ensure_workspace_trust", {}).get("trusted_paths", []):
            self.assertIn(f'[projects."{project_harness.expand_path(tp, self.workspace, self.user_home)}"]', toml_content)

    def test_multi_harness_coexistence(self):
        """Verify side-by-side projection for multiple agents (Antigravity + Codex)"""
        for h_id in ["antigravity", "codex"]:
            manifest_file = self.manifests_dir / f"harness-{h_id}.json"
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            project_harness.apply_harness(
                h_id, manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
            )

        # Both paths were created successfully
        self.assertTrue((self.workspace / ".agents" / "AGENTS.md").exists())
        self.assertTrue((self.workspace / "AGENTS.md").exists())
        self.assertTrue((self.workspace / ".agents" / "rules" / "demo-rule.md").exists())
        self.assertTrue((self.workspace / ".codex" / "rules" / "demo-rule.md").exists())

    def test_idempotency(self):
        """Verify that repeated runs create no duplicate blocks or errors"""
        manifest_file = self.manifests_dir / "harness-antigravity.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        # Run twice
        for _ in range(2):
            project_harness.apply_harness(
                "antigravity", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
            )

        target_md = self.workspace / ".agents" / "AGENTS.md"
        content = target_md.read_text(encoding="utf-8")
        # The tag must appear exactly once
        self.assertEqual(content.count("<!-- block: agent-constitution -->"), 1)

    def test_antigravity_mcp_projection(self):
        """Verify the MCP (json_merge) projection contract and placeholder expansion for the Antigravity harness"""
        mcp_dir = self.canonical_root / "mcp"
        mcp_dir.mkdir(parents=True)
        # Test format A: contains mcpServers
        (mcp_dir / "agentbrain.json").write_text(json.dumps({
            "mcpServers": {
                "agentbrain": {
                    "command": "python3",
                    "args": ["/workspace/bin/guard.py"],
                    "env": {"HINDSIGHT_URL": "http://127.0.0.1:8888"}
                }
            }
        }), encoding="utf-8")
        # Test format B: single server definition (top level has command and name)
        (mcp_dir / "playwright.json").write_text(json.dumps({
            "name": "playwright",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"]
        }), encoding="utf-8")

        manifest_file = self.manifests_dir / "harness-antigravity.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        project_harness.apply_harness(
            "antigravity", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        mcp_config = self.workspace / ".agents" / "mcp_config.json"
        self.assertTrue(mcp_config.exists())
        data = json.loads(mcp_config.read_text(encoding="utf-8"))
        self.assertIn("mcpServers", data)
        self.assertIn("agentbrain", data["mcpServers"])
        self.assertIn("playwright", data["mcpServers"])
        # Verify the /workspace placeholder was replaced
        self.assertEqual(data["mcpServers"]["agentbrain"]["args"], [f"{self.workspace}/bin/guard.py"])

    def test_claude_mcp_projection(self):
        """Verify the MCP (json_merge) projection contract for the Claude Code harness"""
        mcp_dir = self.canonical_root / "mcp"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "demo-tool.json").write_text(json.dumps({
            "command": "node",
            "args": ["/root/server.js"]
        }), encoding="utf-8")

        manifest_file = self.manifests_dir / "harness-claude.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        project_harness.apply_harness(
            "claude", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        mcp_config = self.user_home / ".claude" / "mcp.json"
        self.assertTrue(mcp_config.exists())
        data = json.loads(mcp_config.read_text(encoding="utf-8"))
        self.assertIn("mcpServers", data)
        # When no name is given, the stem 'demo-tool' is used as the server key
        self.assertIn("demo-tool", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["demo-tool"]["args"], [f"{self.user_home}/server.js"])

    def test_codex_mcp_projection(self):
        """Verify the MCP (toml_mcp_merge) projection contract for the OpenAI Codex harness"""
        import tomllib

        mcp_dir = self.canonical_root / "mcp"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "brain.json").write_text(json.dumps({
            "mcpServers": {
                "brain": {
                    "command": "python3",
                    "args": ["-m", "brain"],
                    "env": {"PORT": "8888"}
                }
            }
        }), encoding="utf-8")

        # Pre-write existing settings into config.toml to verify non-destructive merging
        codex_config = self.user_home / ".codex" / "config.toml"
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        codex_config.write_text('model = "gpt-5"\n\n[projects."/workspace"]\ntrust_level = "trusted"\n', encoding="utf-8")

        manifest_file = self.manifests_dir / "harness-codex.json"
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        project_harness.apply_harness(
            "codex", manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
        )

        self.assertTrue(codex_config.exists())
        content = codex_config.read_text(encoding="utf-8")
        parsed = tomllib.loads(content)

        # Existing settings are preserved
        self.assertEqual(parsed.get("model"), "gpt-5")
        self.assertEqual(parsed.get("projects", {}).get("/workspace", {}).get("trust_level"), "trusted")
        # The MCP server was merged successfully
        self.assertIn("mcp_servers", parsed)
        self.assertIn("brain", parsed["mcp_servers"])
        self.assertEqual(parsed["mcp_servers"]["brain"]["command"], "python3")
        self.assertEqual(parsed["mcp_servers"]["brain"]["args"], ["-m", "brain"])
        self.assertEqual(parsed["mcp_servers"]["brain"]["env"]["PORT"], "8888")

    def test_mcp_idempotency_and_preservation(self):
        """Verify MCP projection is idempotent and existing servers are not overwritten or removed"""
        import tomllib

        mcp_dir = self.canonical_root / "mcp"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "srv-a.json").write_text(json.dumps({
            "command": "python3",
            "args": ["srv_a.py"]
        }), encoding="utf-8")

        # Pre-place existing servers for Antigravity and Codex
        ag_config = self.workspace / ".agents" / "mcp_config.json"
        ag_config.parent.mkdir(parents=True, exist_ok=True)
        ag_config.write_text(json.dumps({
            "mcpServers": {
                "pre-existing": {"command": "echo", "args": ["hello"]}
            }
        }), encoding="utf-8")

        codex_config = self.user_home / ".codex" / "config.toml"
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        codex_config.write_text('[mcp_servers."pre-existing"]\ncommand = "echo"\nargs = ["hello"]\n', encoding="utf-8")

        for h_id in ["antigravity", "codex"]:
            manifest_file = self.manifests_dir / f"harness-{h_id}.json"
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            # Run twice
            for _ in range(2):
                project_harness.apply_harness(
                    h_id, manifest, self.canonical_root, self.workspace, self.user_home, verbose=False
                )

        # Verify existing and new servers coexist for Antigravity
        ag_data = json.loads(ag_config.read_text(encoding="utf-8"))
        self.assertIn("pre-existing", ag_data["mcpServers"])
        self.assertIn("srv-a", ag_data["mcpServers"])

        # Verify existing and new servers coexist for Codex
        codex_data = tomllib.loads(codex_config.read_text(encoding="utf-8"))
        self.assertIn("pre-existing", codex_data["mcp_servers"])
        self.assertIn("srv-a", codex_data["mcp_servers"])


if __name__ == "__main__":
    unittest.main()
