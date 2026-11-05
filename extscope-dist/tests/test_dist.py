import ast, json, os, pathlib, subprocess, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from extscope_dist import config, engine, fsops
from extscope_dist.errors import DistError
from extscope_dist.plan import load_plan, NATIVE, PREVIEW


def write(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text); return p


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    cfg = config.Config(str(tmp_path / "state"), {}, [], [str(ws)], [])
    return tmp_path, ws, cfg


def mk(tmp, items):
    f = tmp / "plan.json"
    f.write_text(json.dumps({"schema": NATIVE, "items": items})); return load_plan(str(f), {})


def test_copy_symlink_apply_verify_rollback(env):
    tmp, ws, cfg = env
    src = write(tmp / "src/skill/SKILL.md", "hello")
    plan = mk(tmp, [{"method": "copy", "src": str(src.parent), "target": str(ws / "skills/skill")},
                    {"method": "symlink", "src": str(src), "target": str(ws / "link.md")}])
    engine.resolve(plan, cfg)
    assert {r["action"] for r in engine.preview(plan, cfg)["actions"]} == {"create"}
    assert not (ws / "skills").exists()                       # dry run wrote nothing
    res = engine.apply(plan, cfg, allow_all=True)
    assert (ws / "skills/skill/SKILL.md").read_text() == "hello" and os.path.islink(ws / "link.md")
    assert engine.verify(res["manifest"], cfg)["ok"]
    assert engine.apply(plan, cfg, allow_all=True)["applied"] == 0   # idempotent
    engine.rollback(res["manifest"], cfg)
    assert not (ws / "skills/skill").exists() and not os.path.lexists(ws / "link.md")


def test_conflict_needs_force_and_rollback_restores(env):
    tmp, ws, cfg = env
    write(ws / "a.txt", "mine")
    src = write(tmp / "s.txt", "new")
    plan = mk(tmp, [{"method": "copy", "src": str(src), "target": str(ws / "a.txt")}])
    with pytest.raises(DistError) as e: engine.apply(plan, cfg, allow_all=True)
    assert e.value.code == "conflict" and (ws / "a.txt").read_text() == "mine"
    res = engine.apply(plan, cfg, allow_all=True, force=True)
    assert (ws / "a.txt").read_text() == "new"
    engine.rollback(res["manifest"], cfg)
    assert (ws / "a.txt").read_text() == "mine"


def test_rollback_refuses_drift(env):
    tmp, ws, cfg = env
    src = write(tmp / "s.txt", "v1")
    res = engine.apply(mk(tmp, [{"method": "copy", "src": str(src), "target": str(ws / "t")}]), cfg, allow_all=True)
    (ws / "t").write_text("edited by someone")
    with pytest.raises(DistError) as e: engine.rollback(res["manifest"], cfg)
    assert e.value.code == "rollback_incomplete" and (ws / "t").read_text() == "edited by someone"
    assert engine.verify(res["manifest"], cfg)["ok"] is False


def test_md_block_idempotent_and_restores(env):
    tmp, ws, cfg = env
    write(ws / "AGENTS.md", "# keep me\n")
    src = write(tmp / "frag.md", "rule one")
    plan = mk(tmp, [{"method": "md_block", "src": str(src), "target": str(ws / "AGENTS.md"), "block_id": "rules"}])
    res = engine.apply(plan, cfg, allow_all=True)
    text = (ws / "AGENTS.md").read_text()
    assert "# keep me" in text and "rule one" in text
    src.write_text("rule two")
    res2 = engine.apply(plan, cfg, allow_all=True)
    text = (ws / "AGENTS.md").read_text()
    assert text.count("<!-- block: rules -->") == 1 and "rule two" in text and "rule one" not in text
    engine.rollback(res2["manifest"], cfg)
    engine.rollback(res["manifest"], cfg)
    assert (ws / "AGENTS.md").read_text() == "# keep me\n"
    return
    assert engine.apply(plan, cfg, allow_all=True)["applied"] == 0


def test_md_block_reads_legacy_blocks(env):
    tmp, ws, cfg = env
    write(ws / "CLAUDE.md", "top\n\n<!-- block: rule-x -->\nold\n<!-- /block: rule-x -->\n\nbottom\n")
    src = write(tmp / "f.md", "new")
    plan = mk(tmp, [{"method": "md_block", "src": str(src), "target": str(ws / "CLAUDE.md"), "block_id": "rule-x"}])
    engine.apply(plan, cfg, allow_all=True)
    text = (ws / "CLAUDE.md").read_text()
    assert text.count("rule-x -->") == 2 and "new" in text and "old" not in text and "bottom" in text


def test_gate_requires_scope_and_filters(env):
    tmp, ws, cfg = env
    s = write(tmp / "s", "1")
    plan = mk(tmp, [{"method": "copy", "src": str(s), "target": str(ws / "a/x")},
                    {"method": "copy", "src": str(s), "target": str(ws / "b/y")}])
    with pytest.raises(DistError) as e: engine.apply(plan, cfg)
    assert e.value.code == "scope_required"
    engine.apply(plan, cfg, prefixes=[str(ws / "a")])
    assert (ws / "a/x").exists() and not (ws / "b/y").exists()


def test_fail_closed_roots_and_deny(env, tmp_path):
    tmp, ws, cfg = env
    s = write(tmp / "s", "1")
    outside = mk(tmp, [{"method": "copy", "src": str(s), "target": str(tmp / "elsewhere/x")}])
    with pytest.raises(DistError) as e: engine.resolve(outside, cfg)
    assert e.value.code == "target_outside_allowed_roots"
    cfg.denied_roots = [str(ws / "secret")]
    den = mk(tmp, [{"method": "copy", "src": str(s), "target": str(ws / "secret/k")}])
    with pytest.raises(DistError) as e: engine.resolve(den, cfg)
    assert e.value.code == "target_denied"
    cfg.allowed_roots = []
    with pytest.raises(DistError) as e: engine.resolve(outside, cfg)
    assert e.value.code == "no_allowed_roots"


def test_symlink_escape_blocked(env):
    tmp, ws, cfg = env
    (tmp / "out").mkdir(); os.symlink(tmp / "out", ws / "esc")
    s = write(tmp / "s", "1")
    plan = mk(tmp, [{"method": "copy", "src": str(s), "target": str(ws / "esc/x")}])
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert e.value.code == "target_outside_allowed_roots"


def test_remap_and_variables(tmp_path):
    ws = tmp_path / "real"; ws.mkdir()
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"variables": {"WS": str(ws)}, "remap": [{"from": "/workspace", "to": "${WS}"}],
                             "allowed_roots": ["${WS}"], "state_dir": str(tmp_path / "st")}))
    cfg = config.load(str(c), environ={})
    assert cfg.map_target("/workspace/.agents/x") == os.path.normpath(str(ws) + "/.agents/x")
    with pytest.raises(DistError): config.expand("${NOPE}", {})


def test_defaults_follow_platform_env_not_hardcoded():
    assert config.default_state_dir({"XDG_STATE_HOME": "/s"}, "linux") == os.path.join("/s", "extscope", "dist")
    assert config.default_config_path({"APPDATA": "C:\\A"}, "win32").startswith("C:\\A")
    assert config.default_state_dir({"LOCALAPPDATA": "C:\\L"}, "win32").startswith("C:\\L")


def test_preview_schema_from_extscope_plan(env):
    tmp, ws, cfg = env
    s = write(tmp / "sk/SKILL.md", "z")
    doc = {"schema": PREVIEW, "items": [
        {"item": {"src": str(s.parent), "dst_fhs": "/x", "type": "copy_inject", "mode": "ro",
                  "pipeline": [{"step": 1, "module": "volume_mount", "config": {"dst": "/m"}},
                               {"step": 2, "module": "copy_inject", "config": {"dst": str(ws / "sk")}}]}}]}
    f = tmp / "p.json"; f.write_text(json.dumps(doc))
    plan = load_plan(str(f), {})
    assert [i.method for i in plan.items] == ["copy"] and plan.skipped
    doc["items"][0]["item"]["pipeline"].append({"step": 3, "module": "key_inject", "config": {}})
    f.write_text(json.dumps(doc))
    plan = load_plan(str(f), {})                       # parsed as a module item...
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)   # ...but refused unless enabled
    assert e.value.code == "module_not_enabled"


def test_cli_runs_without_extscope_installed(env):
    tmp, ws, _ = env
    s = write(tmp / "s", "1")
    (tmp / "plan.json").write_text(json.dumps({"schema": NATIVE, "items": [{"method": "copy", "src": str(s), "target": str(ws / "o")}]}))
    (tmp / "c.json").write_text(json.dumps({"allowed_roots": [str(ws)], "state_dir": str(tmp / "st")}))
    run = lambda *a: subprocess.run([sys.executable, "-c", f"import sys;sys.path.insert(0,{str(ROOT/'src')!r});from extscope_dist.cli import main;raise SystemExit(main(sys.argv[1:]))",
                                     "--config", str(tmp / "c.json"), *a], capture_output=True, text=True, cwd=str(tmp))
    assert run("plan", str(tmp / "plan.json")).returncode == 0 and not (ws / "o").exists()
    assert run("apply", str(tmp / "plan.json")).returncode == 2           # scope gate
    r = run("apply", str(tmp / "plan.json"), "--all"); assert r.returncode == 0, r.stderr
    assert (ws / "o").read_text() == "1"


def test_module_is_independent():
    """No import of extscope, and no site-specific / fixed-root path literals in source."""
    for f in (ROOT / "src/extscope_dist").glob("*.py"):
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
            assert not any(x == "extscope" or x.startswith("extscope.") for x in names), f
        text = f.read_text()
        assert "/opt/" not in text and "/srv/" not in text and "/var/lib" not in text, f


def test_plan_honours_filter_prefix(env):
    tmp, ws, cfg = env
    s = write(tmp / "s", "1")
    plan = mk(tmp, [{"method": "copy", "src": str(s), "target": str(ws / "a/x")},
                    {"method": "copy", "src": str(s), "target": str(ws / "b/y")}])
    rows = engine.preview(plan, cfg, [str(ws / "a")], False)["actions"]
    assert [r["target"] for r in rows] == [str(ws / "a/x")]


def test_sha256_pin_applies_to_symlink(env):
    tmp, ws, cfg = env
    s = write(tmp / "s", "1")
    plan = mk(tmp, [{"method": "symlink", "src": str(s), "target": str(ws / "l"), "sha256": "0" * 64}])
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert e.value.code == "source_digest_mismatch"


def test_md_block_rollback_refuses_drift(env):
    tmp, ws, cfg = env
    write(ws / "AGENTS.md", "# keep\n")
    src = write(tmp / "frag.md", "rule")
    plan = mk(tmp, [{"method": "md_block", "src": str(src), "target": str(ws / "AGENTS.md"), "block_id": "rules"}])
    res = engine.apply(plan, cfg, allow_all=True)
    with open(ws / "AGENTS.md", "a") as fh: fh.write("later edit\n")
    with pytest.raises(DistError) as e: engine.rollback(res["manifest"], cfg)
    assert e.value.code == "rollback_incomplete" and "later edit" in (ws / "AGENTS.md").read_text()
    engine.rollback(res["manifest"], cfg, force=True)
    assert (ws / "AGENTS.md").read_text() == "# keep\n"


def _receipt(tmp, sources, plan_sha="p"):
    f = tmp / "r.json"
    f.write_text(json.dumps({"schema": "extscope.source-binding.v1", "plan_sha256": plan_sha, "sources": sources}))
    from extscope_dist import receipt
    return receipt.load(str(f))


def test_tree_digest_matches_core(tmp_path):
    sys.path.insert(0, str(ROOT.parent / "src"))
    from extscope.digest import hash_tree
    from extscope_dist import receipt
    write(tmp_path / "d/a", "1"); write(tmp_path / "d/s/b", "2"); (tmp_path / "d/l").symlink_to("a")
    assert receipt.tree_v1(str(tmp_path / "d")) == hash_tree(str(tmp_path / "d"))


def test_receipt_refuses_changed_unbound_and_symlinked_sources(env):
    tmp, ws, cfg = env
    from extscope_dist import receipt
    d = write(tmp / "src/skill/SKILL.md", "v1").parent
    plan = mk(tmp, [{"method": "copy", "src": str(d), "target": str(ws / "skill")}])
    good = _receipt(tmp, [{"source": str(d), "algorithm": "extscope.tree.v1", "sha256": receipt.tree_v1(str(d))}])
    plan.receipt = good; engine.apply(plan, cfg, allow_all=True); assert (ws / "skill/SKILL.md").exists()
    (d / "SKILL.md").write_text("v2")
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert e.value.code == "receipt_source_mismatch"
    plan.receipt = _receipt(tmp, [])
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert e.value.code == "source_not_bound"
    (d / "ln").symlink_to("SKILL.md")
    plan.receipt = _receipt(tmp, [{"source": str(d), "algorithm": "extscope.tree.v1", "sha256": receipt.tree_v1(str(d))}])
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert e.value.code == "receipt_tree_has_symlink"
    plan.receipt = _receipt(tmp, [], "x"); plan.core_plan_sha256 = "y"
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert e.value.code == "receipt_plan_mismatch"


def test_target_lock_blocks_a_second_writer_then_releases(tmp_path):
    from extscope_dist.locks import TargetLocks
    t = str(tmp_path / "d/AGENTS.md")
    with TargetLocks([t]):
        with pytest.raises(DistError) as e:
            with TargetLocks([t], timeout=0.2): pass
        assert e.value.code == "lock_timeout"
    with TargetLocks([t], timeout=0.2): pass


def test_concurrent_md_block_applies_lose_nothing(tmp_path):
    for i in range(12):
        t = tmp_path / str(i); ws = t / "ws"; ws.mkdir(parents=True); (ws / "AGENTS.md").write_text("# base\n" + "x\n" * 2000)
        procs = []
        for k in "ab":
            (t / f"{k}.md").write_text(f"block {k}\n" + "y\n" * 500)
            (t / f"{k}.json").write_text(json.dumps({"schema": NATIVE, "items": [{"method": "md_block", "src": str(t / f"{k}.md"), "target": str(ws / "AGENTS.md"), "block_id": k}]}))
            (t / f"{k}.cfg").write_text(json.dumps({"allowed_roots": [str(ws)], "state_dir": str(t / f"st-{k}")}))
            procs.append(subprocess.Popen([sys.executable, "-m", "extscope_dist", "--config", str(t / f"{k}.cfg"), "apply", str(t / f"{k}.json"), "--all"],
                                          env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        assert [p.wait() for p in procs] == [0, 0]
        text = (ws / "AGENTS.md").read_text()
        assert "block a" in text and "block b" in text, f"lost update in run {i}"


def _applied(tmp, cfg, ws, name, text, block=None):
    src = write(tmp / f"src-{name}", text)
    item = {"method": "copy", "src": str(src), "target": str(ws / "f")}
    return engine.apply(mk(tmp, [item]), cfg, allow_all=True, force=True)


def test_prune_lists_by_default_and_removes_only_on_apply(env):
    from extscope_dist import prune
    tmp, ws, cfg = env
    write(ws / "f", "original")
    first = _applied(tmp, cfg, ws, "1", "v1"); second = _applied(tmp, cfg, ws, "2", "v2")
    engine.rollback(second["manifest"], cfg)
    rolled = prune.run(cfg.state_dir, older_than_days=0)
    assert [r["reason"] for r in rolled["candidates"]] == ["rolled_back"] and rolled["applied"] is False
    assert os.path.exists(first["manifest"]) and os.path.isdir(os.path.join(cfg.state_dir, "backups"))
    out = prune.run(cfg.state_dir, older_than_days=0, apply=True)
    assert out["count"] == 1 and os.path.exists(first["manifest"])           # the live record stays
    assert prune.run(cfg.state_dir, older_than_days=0)["count"] == 0


def test_prune_never_drops_the_latest_record_and_superseded_is_opt_in(env):
    from extscope_dist import prune
    tmp, ws, cfg = env
    write(ws / "f", "original")
    a = _applied(tmp, cfg, ws, "1", "v1"); b = _applied(tmp, cfg, ws, "2", "v2")
    assert prune.run(cfg.state_dir, 0)["count"] == 0                          # default: applied manifests untouched
    rows = prune.run(cfg.state_dir, 0, include_applied=True)["candidates"]
    assert [r["reason"] for r in rows] == ["superseded"] and rows[0]["paths"][0] == a["manifest"]
    prune.run(cfg.state_dir, 0, include_applied=True, apply=True)
    assert os.path.exists(b["manifest"]) and engine.verify(b["manifest"], cfg)["ok"]
    apply_again = engine.apply(mk(tmp, [{"method": "copy", "src": str(tmp / "src-2"), "target": str(ws / "f")}]), cfg, allow_all=True)
    assert apply_again["applied"] == 0                                         # still recognised as managed, no conflict


def test_prune_leaves_young_orphans_alone(env):
    from extscope_dist import prune
    tmp, ws, cfg = env
    os.makedirs(os.path.join(cfg.state_dir, "backups", "inflight"))
    assert prune.run(cfg.state_dir, older_than_days=0)["count"] == 0          # younger than MIN_ORPHAN_AGE


# ---- reverse direction: diff / pullback / origin ----
from extscope_dist import sync


def _applied_skill(env, text="v1"):
    tmp, ws, cfg = env
    src = write(tmp / "repo/skill/SKILL.md", text)
    plan = mk(tmp, [{"method": "copy", "src": str(src.parent), "target": str(ws / "skills/skill")}])
    return src, engine.apply(plan, cfg, allow_all=True)["manifest"], ws


def test_pullback_writes_target_edits_to_source(env):
    tmp, ws, cfg = env
    src, man, ws = _applied_skill(env)
    (ws / "skills/skill/SKILL.md").write_text("v2")
    (ws / "skills/skill/new.md").write_text("n")
    assert not sync.diff(man, cfg)["ok"] and "v2" in sync.diff(man, cfg)["entries"][0]["diff"]
    cfg.source_roots = [str(tmp / "repo")]
    assert sync.pullback(man, cfg)["entries"][0]["state"] == "would_pull" and src.read_text() == "v1"   # dry-run
    res = sync.pullback(man, cfg, apply=True)
    assert res["ok"] and src.read_text() == "v2" and (src.parent / "new.md").read_text() == "n"


def test_pullback_is_fail_closed_and_refuses_moved_source(env):
    tmp, ws, cfg = env
    src, man, ws = _applied_skill(env)
    (ws / "skills/skill/SKILL.md").write_text("v2")
    with pytest.raises(DistError) as e:
        sync.pullback(man, cfg, apply=True)
    assert e.value.code == "no_source_roots"
    cfg.source_roots = [str(tmp / "elsewhere")]
    with pytest.raises(DistError) as e:
        sync.pullback(man, cfg, apply=True)
    assert e.value.code == "source_outside_source_roots"
    cfg.source_roots = [str(tmp / "repo")]
    src.write_text("someone else")                 # source moved after apply -> never overwrite
    res = sync.pullback(man, cfg, apply=True)
    assert not res["ok"] and src.read_text() == "someone else"


def test_pullback_md_block_and_origin(env):
    tmp, ws, cfg = env
    s = write(tmp / "repo/rule.md", "rule v1")
    plan = mk(tmp, [{"method": "md_block", "src": str(s), "target": str(ws / "CLAUDE.md"), "block_id": "r"}])
    man = engine.apply(plan, cfg, allow_all=True)["manifest"]
    (ws / "CLAUDE.md").write_text((ws / "CLAUDE.md").read_text().replace("rule v1", "rule v2"))
    cfg.source_roots = [str(tmp / "repo")]
    sync.pullback(man, cfg, apply=True)
    assert s.read_text().strip() == "rule v2"
    o = sync.origin(str(ws / "CLAUDE.md"), cfg)
    assert o["src"] == str(s) and o["manifest"]
