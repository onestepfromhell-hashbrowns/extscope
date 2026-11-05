"""Adapter decides what/where; extscope-dist writes. Fixtures only."""
from pathlib import Path
import importlib, json, os, subprocess, sys
PKG = Path(__file__).resolve().parents[1]
DIST = PKG.parent / "extscope-dist/src"
sys.path.insert(0, str(PKG / "adapters"))
ph = importlib.import_module("project-harness")


def fixture(tmp, manifest):
    can = tmp / "canonical"
    (can / "skills/demo").mkdir(parents=True); (can / "skills/demo/SKILL.md").write_text("# demo")
    (can / "rules").mkdir(); (can / "rules/r1.md").write_text("rule one")
    (can / "fragments").mkdir(); (can / "fragments/f1.md").write_text("frag one")
    mdir = tmp / "manifests"; mdir.mkdir()
    (mdir / "harness-t.json").write_text(json.dumps({"harness_id": "t", "projections": manifest}))
    ws, home = tmp / "ws", tmp / "home"; ws.mkdir(); home.mkdir()
    return can, mdir, ws, home


def emit(tmp, manifest, *extra):
    can, mdir, ws, home = fixture(tmp, manifest)
    r = subprocess.run([sys.executable, str(PKG / "adapters/project-harness.py"), "--harness", "t",
                        "--canonical-root", str(can), "--manifests-dir", str(mdir), "--workspace", str(ws),
                        "--user-home", str(home), "--emit-dist-plan", *extra], capture_output=True, text=True)
    return r, ws, home


FULL = {"skills": {"mode": "symlink_children", "target_dir": "~/skills"},
        "rules": {"mode": "md_block_append", "target_file": "./CLAUDE.md"},
        "fragments": {"mode": "md_block_append", "target_file": "./AGENTS.md"}}


def test_emit_writes_nothing_and_dist_applies_it(tmp_path):
    r, ws, home = emit(tmp_path, FULL)
    assert r.returncode == 0, r.stderr
    assert not (home / "skills").exists() and not (ws / "AGENTS.md").exists()
    plan = json.loads(r.stdout); assert plan["schema"] == "extscope-dist.plan.v1"
    (tmp_path / "plan.json").write_text(r.stdout)
    cfg = tmp_path / "dist.json"
    cfg.write_text(json.dumps({"allowed_roots": [str(ws), str(home)], "state_dir": str(tmp_path / "st")}))
    env = {**os.environ, "PYTHONPATH": str(DIST)}
    run = lambda *a: subprocess.run([sys.executable, "-m", "extscope_dist", "--config", str(cfg), *a],
                                    capture_output=True, text=True, env=env)
    a = run("apply", str(tmp_path / "plan.json"), "--all"); assert a.returncode == 0, a.stderr
    assert (home / "skills/demo").is_symlink() and "rule one" in (ws / "CLAUDE.md").read_text()
    assert "frag one" in (ws / "AGENTS.md").read_text()
    assert json.loads(run("apply", str(tmp_path / "plan.json"), "--all").stdout)["applied"] == 0
    m = json.loads(a.stdout)["manifest"]
    assert run("rollback", m).returncode == 0 and not (home / "skills/demo").exists()


def test_residual_is_fail_closed_unless_allowed(tmp_path):
    m = {**FULL, "skills": {"mode": "symlink_children", "target_dir": "~/skills", "extra_dirs_registration": {"config_file": "~/c.toml"}}}
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    r, _, _ = emit(tmp_path / "a", m)
    assert r.returncode == 3 and "skills" in r.stderr
    r2, _, _ = emit(tmp_path / "b", m, "--allow-residual"); assert r2.returncode == 0
    assert all("skills" not in i.get("target", "") for i in json.loads(r2.stdout)["items"])


def test_mcp_and_trust_become_config_merge_and_apply(tmp_path):
    can, mdir, ws, home = fixture(tmp_path, {})
    (can / "mcp").mkdir(); (can / "mcp/s.json").write_text(json.dumps({"mcpServers": {"srv": {"command": "c", "args": ["/workspace/x"]}}}))
    man = {"projections": {"mcp": {"mode": "toml_mcp_merge", "target_file": "~/.codex/config.toml"}},
           "runtime_bootstrap": {"ensure_workspace_trust": {"enabled": True, "config_file": "~/.codex/config.toml", "trusted_paths": ["./"]}}}
    items, residual = ph.plan_harness(man, can, ws, home)
    assert residual == [] and [i["method"] for i in items] == ["config_merge", "config_merge"]
    (tmp_path / "plan.json").write_text(json.dumps({"schema": "extscope-dist.plan.v1", "items": items}))
    cfg = tmp_path / "dist.json"
    cfg.write_text(json.dumps({"allowed_roots": [str(home)], "state_dir": str(tmp_path / "st"),
                               "modules": {"enable": ["extscope_modules"]}}))
    env = {**os.environ, "PYTHONPATH": f"{DIST}:{PKG.parent}/extscope-modules/src"}
    a = subprocess.run([sys.executable, "-m", "extscope_dist", "--config", str(cfg), "apply", str(tmp_path / "plan.json"), "--all"],
                       capture_output=True, text=True, env=env)
    assert a.returncode == 0, a.stderr
    import tomllib
    doc = tomllib.loads((home / ".codex/config.toml").read_text())
    assert doc["mcp_servers"]["srv"]["args"] == [str(ws) + "/x"] and doc["projects"][str(ws)]["trust_level"] == "trusted"


def test_tiered_skills_not_silently_downgraded(tmp_path):
    can, mdir, ws, home = fixture(tmp_path, {})
    items, residual = ph.plan_harness({"tiered": True, "projections": {"skills": {"target_dir": "~/skills"}}},
                                      can, ws, home)
    assert items == [] and residual and "tiered" in residual[0]
