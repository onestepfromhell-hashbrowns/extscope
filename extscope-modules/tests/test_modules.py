import ast, getpass, json, os, pathlib, stat, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "extscope-dist/src"))
from extscope_dist import config, engine
from extscope_dist.errors import DistError
from extscope_dist.plan import load_plan, NATIVE

HOOK_OK = "#!/bin/sh\necho ran > \"$1\"\n"


def sha(p):
    import hashlib; return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


@pytest.fixture
def ctx(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (tmp_path / "secrets").mkdir(); (tmp_path / "hooks").mkdir()
    cfg = config.Config(str(tmp_path / "state"), {}, [], [str(ws), str(tmp_path / "secrets"), str(tmp_path / "hooks")], [],
                        ["extscope_modules"], {
        "key_inject": {"allowed_dst_roots": [str(tmp_path / "secrets")]},
        "env_inject": {"allowed_dirs": [str(ws / "env")], "default_dir": str(ws / "env")},
        "chown": {"allowed_owners": [getpass.getuser()], "allowed_roots": [str(ws)]},
        "custom_hook": {"script_roots": [str(tmp_path / "hooks")]}})
    return tmp_path, ws, cfg


def mk(tmp, items):
    f = tmp / "plan.json"; f.write_text(json.dumps({"schema": NATIVE, "items": items})); return load_plan(str(f), {})


def test_chmod_chown_roundtrip(ctx):
    tmp, ws, cfg = ctx
    f = ws / "f"; f.write_text("x"); f.chmod(0o644)
    plan = mk(tmp, [{"method": "chmod", "config": {"mode": "0600", "target": str(f)}},
                    {"method": "chown", "config": {"owner": getpass.getuser(), "target": str(f)}}])
    res = engine.apply(plan, cfg, allow_all=True)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert engine.verify(res["manifest"], cfg)["ok"]
    f.chmod(0o666)
    assert engine.verify(res["manifest"], cfg)["ok"] is False
    f.chmod(0o600)
    engine.rollback(res["manifest"], cfg)
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_chmod_refuses_special_bits_and_symlink(ctx):
    tmp, ws, cfg = ctx
    f = ws / "f"; f.write_text("x")
    with pytest.raises(DistError) as e: engine.resolve(mk(tmp, [{"method": "chmod", "config": {"mode": "4755", "target": str(f)}}]), cfg)
    assert e.value.code == "module_refused"
    os.symlink(f, ws / "l")
    with pytest.raises(DistError) as e: engine.apply(mk(tmp, [{"method": "chmod", "config": {"mode": "0600", "target": str(ws / "l")}}]), cfg, allow_all=True)
    assert e.value.code == "module_refused"   # policy refusal, not a conflict


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="環境:本測試用 owner='root' 代表「不在 allowed_owners 的使用者」,而 fixture 的 allowed_owners 是當前使用者;以 root 執行時兩者相同,需非 root 使用者",
)
def test_chown_requires_policy(ctx):
    tmp, ws, cfg = ctx
    f = ws / "f"; f.write_text("x")
    plan = mk(tmp, [{"method": "chown", "config": {"owner": "root", "target": str(f)}}])
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert "owner_not_allowed" in e.value.detail
    cfg.module_policy["chown"] = {}
    with pytest.raises(DistError) as e: engine.resolve(plan, cfg)
    assert "policy_missing" in e.value.detail


def test_key_inject_mode_atomic_and_no_secret_in_manifest(ctx):
    tmp, ws, cfg = ctx
    src = tmp / "src.key"; src.write_text("TOPSECRET-VALUE")
    dst = tmp / "secrets" / "api.key"
    plan = mk(tmp, [{"method": "key_inject", "config": {"src": str(src), "dst": str(dst)}}])
    res = engine.apply(plan, cfg, allow_all=True)
    assert dst.read_text() == "TOPSECRET-VALUE" and stat.S_IMODE(dst.stat().st_mode) == 0o600
    assert "TOPSECRET" not in pathlib.Path(res["manifest"]).read_text()
    assert "TOPSECRET" not in json.dumps(engine.preview(plan, cfg))
    assert engine.apply(plan, cfg, allow_all=True)["applied"] == 0
    src.write_text("NEW"); engine.apply(plan, cfg, allow_all=True)
    assert dst.read_text() == "NEW"


def test_key_inject_rollback_restores_previous(ctx):
    tmp, ws, cfg = ctx
    dst = tmp / "secrets" / "k"; dst.write_text("old"); dst.chmod(0o600)
    src = tmp / "s"; src.write_text("new")
    plan = mk(tmp, [{"method": "key_inject", "config": {"src": str(src), "dst": str(dst)}}])
    res = engine.apply(plan, cfg, allow_all=True)
    assert dst.read_text() == "new"
    engine.rollback(res["manifest"], cfg)
    assert dst.read_text() == "old"


def test_key_inject_refusals(ctx):
    tmp, ws, cfg = ctx
    src = tmp / "s"; src.write_text("v")
    wide = mk(tmp, [{"method": "key_inject", "config": {"src": str(src), "dst": str(tmp / "secrets/k"), "chmod": "0644"}}])
    with pytest.raises(DistError) as e: engine.resolve(wide, cfg)
    assert "mode_too_wide" in e.value.detail
    outside = mk(tmp, [{"method": "key_inject", "config": {"src": str(src), "dst": str(ws / "k")}}])
    with pytest.raises(DistError) as e: engine.resolve(outside, cfg)
    assert "dst_outside_key_roots" in e.value.detail
    good = mk(tmp, [{"method": "key_inject", "config": {"src": str(src), "dst": str(tmp / "secrets/k")}}])
    engine.resolve(good, cfg)
    cfg.module_policy["key_inject"] = {}
    with pytest.raises(DistError) as e: engine.resolve(good, cfg)
    assert "policy_missing" in e.value.detail


def test_env_inject_quotes_hides_value_and_rolls_back(ctx):
    tmp, ws, cfg = ctx
    evil = "x'; touch /tmp/pwned; echo '"
    plan = mk(tmp, [{"method": "env_inject", "config": {"key": "TOKEN", "value": evil}}])   # dir from policy default
    res = engine.apply(plan, cfg, allow_all=True)
    body = (ws / "env/TOKEN.sh").read_text()
    assert body.startswith("export TOKEN='") and "'\\''" in body
    import subprocess
    out = subprocess.run(["sh", "-c", f". {ws}/env/TOKEN.sh; printf %s \"$TOKEN\""], capture_output=True, text=True).stdout
    assert out == evil
    assert "pwned" not in pathlib.Path(res["manifest"]).read_text()
    engine.rollback(res["manifest"], cfg)
    assert not (ws / "env/TOKEN.sh").exists()
    for bad in ({"key": "A B", "value": "v"}, {"key": "K", "value": "a\nb"}):
        with pytest.raises(DistError): engine.resolve(mk(tmp, [{"method": "env_inject", "config": bad}]), cfg)


def test_custom_hook_gates(ctx):
    tmp, ws, cfg = ctx
    h = tmp / "hooks/h.sh"; h.write_text(HOOK_OK); h.chmod(0o755)
    out = ws / "out"
    item = lambda **kw: mk(tmp, [{"method": "custom_hook", "config": {"script_path": str(h), "args": [str(out)], **kw}}])
    with pytest.raises(DistError) as e: engine.resolve(item(), cfg)           # no pin
    assert "sha256_pin_required" in e.value.detail
    with pytest.raises(DistError) as e: engine.resolve(item(sha256="0" * 64), cfg)
    assert "script_digest_mismatch" in e.value.detail
    pre = engine.preview(item(sha256=sha(h)), cfg)
    assert pre["actions"][0]["irreversible"] and not out.exists()             # dry run does not execute
    res = engine.apply(item(sha256=sha(h)), cfg, allow_all=True)
    assert out.read_text().strip() == "ran" and res["irreversible"] == ["custom_hook"]
    assert engine.rollback(res["manifest"], cfg)["not_undoable"] == ["custom_hook"]
    h.chmod(0o777)
    with pytest.raises(DistError) as e: engine.resolve(item(sha256=sha(h)), cfg)
    assert "writable" in e.value.detail
    other = tmp / "elsewhere.sh"; other.write_text(HOOK_OK); other.chmod(0o755)
    with pytest.raises(DistError) as e: engine.resolve(mk(tmp, [{"method": "custom_hook", "config": {"script_path": str(other), "sha256": sha(other)}}]), cfg)
    assert "script_outside_script_roots" in e.value.detail


def test_failed_hook_rolls_back_earlier_items(ctx):
    tmp, ws, cfg = ctx
    f = ws / "f"; f.write_text("x"); f.chmod(0o644)
    h = tmp / "hooks/bad.sh"; h.write_text("#!/bin/sh\nexit 3\n"); h.chmod(0o755)
    plan = mk(tmp, [{"method": "chmod", "config": {"mode": "0600", "target": str(f)}},
                    {"method": "custom_hook", "config": {"script_path": str(h), "sha256": sha(h)}}])
    with pytest.raises(DistError) as e: engine.apply(plan, cfg, allow_all=True)
    assert e.value.code == "apply_failed_rolled_back" and stat.S_IMODE(f.stat().st_mode) == 0o644


def test_not_enabled_by_default(ctx):
    tmp, ws, cfg = ctx
    cfg.module_imports = []
    f = ws / "f"; f.write_text("x")
    with pytest.raises(DistError) as e: engine.resolve(mk(tmp, [{"method": "chmod", "config": {"mode": "0600", "target": str(f)}}]), cfg)
    assert e.value.code == "module_not_enabled"


def test_modules_are_independent_of_dist_and_core():
    for f in (ROOT / "src/extscope_modules").glob("*.py"):
        text = f.read_text()
        for n in ast.walk(ast.parse(text)):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
            assert not any(x.split(".")[0] in ("extscope", "extscope_dist") for x in names), f
        assert "/etc/profile.d" not in text and "/run/secrets" not in text, f


def _git(cwd, *a):
    import subprocess
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_pkg_import_native_dir_roundtrip_and_matches_core_digest(ctx):
    tmp, ws, cfg = ctx
    src = tmp / "srcpkg"; src.mkdir(); (src / "a.txt").write_text("1"); (src / "bin").mkdir(); (src / "bin/x").write_text("#!/bin/sh\n"); (src / "bin/x").chmod(0o755)
    root = ws / "pkgs"; root.mkdir()
    cfg.module_policy["pkg_import"] = {"allowed_source_roots": [str(tmp)]}
    plan = mk(tmp, [{"method": "pkg_import", "config": {"root": str(root), "locator": str(src), "as_type": "native", "package_id": "demo",
                                                        "exports": [{"id": "main", "kind": "asset", "path": "a.txt"}]}}])
    res = engine.apply(plan, cfg, allow_all=True)
    assert (root / "demo/a.txt").read_text() == "1" and (root / ".meta/demo.meta.json").is_file()
    assert not (root / ".staging" / "x").exists()
    sys.path.insert(0, str(ROOT.parent / "src"))
    from extscope import pkgs                               # the core's doctor must accept what import wrote
    assert pkgs.validate(str(root), "demo")["status"] == "ok"
    with pytest.raises(DistError) as e: engine.apply(plan, cfg, allow_all=True)
    assert e.value.code == "module_refused" and "already_exists" in e.value.detail
    assert engine.verify(res["manifest"], cfg)["ok"]
    engine.rollback(res["manifest"], cfg)
    assert not (root / "demo").exists() and not (root / ".meta/demo.meta.json").exists()


def test_pkg_import_git_pins_commit_and_refuses_unsafe(ctx):
    tmp, ws, cfg = ctx
    up = tmp / "up"; up.mkdir(); _git(up, "init", "-q"); (up / "f").write_text("v1"); _git(up, "add", "."); _git(up, "commit", "-qm", "1")
    c1 = _git(up, "rev-parse", "HEAD"); (up / "f").write_text("v2"); _git(up, "commit", "-qam", "2")
    (up / ".git/hooks/post-checkout").write_text("#!/bin/sh\ntouch /tmp/HOOK_RAN_extscope\n"); (up / ".git/hooks/post-checkout").chmod(0o755)
    root = ws / "pkgs"; root.mkdir()
    cfg.module_policy["pkg_import"] = {"allowed_source_roots": [str(tmp)]}
    item = lambda **kw: mk(tmp, [{"method": "pkg_import", "config": {"root": str(root), "locator": str(up), "as_type": "external", "package_id": "up", **kw}}])
    with pytest.raises(DistError) as e: engine.resolve(item(), cfg)
    assert "commit_required_for_external" in e.value.detail
    engine.apply(item(commit=c1), cfg, allow_all=True)
    assert (root / "external/up/f").read_text() == "v1" if (root / "external").exists() else (root / "up/f").read_text() == "v1"
    assert not os.path.exists("/tmp/HOOK_RAN_extscope")
    for bad in ({"locator": "https://u:pw@h/x.git"}, {"locator": "https://h/x.git"}, {"locator": "/etc"}):
        with pytest.raises(DistError): engine.resolve(item(commit=c1, **bad), cfg)


def test_module_precondition_is_refused_not_conflict_even_with_force(ctx):
    tmp, ws, cfg = ctx
    src = tmp / "s.txt"; src.write_text("new")
    # chmod targets a path that only the copy in the same plan would create: refused up front, no writes
    plan = mk(tmp, [{"method": "copy", "src": str(src), "target": str(ws / "made.txt")},
                    {"method": "chmod", "config": {"mode": "0600", "target": str(ws / "made.txt")}}])
    for force in (False, True):
        with pytest.raises(DistError) as e: engine.apply(plan, cfg, allow_all=True, force=force)
        assert e.value.code == "module_refused" and "chmod" in str(e.value)
        assert not (ws / "made.txt").exists()


def test_config_merge_json_toml_roundtrip(ctx):
    tmp, ws, cfg = ctx
    j, t = ws / "mcp.json", ws / "config.toml"
    j.write_text('{"other": 1, "mcpServers": {"old": {"command": "o"}}}'); j.chmod(0o640)
    t.write_text('model = "x"\n\n[projects."/a"]\ntrust_level = "trusted"\n')
    plan = mk(tmp, [
        {"method": "config_merge", "config": {"target": str(j), "format": "json", "key": "mcpServers",
                                              "entries": {"new": {"command": "n", "args": ["-y"]}}}},
        {"method": "config_merge", "config": {"target": str(t), "format": "toml", "table": "mcp_servers",
                                              "entries": {"new": {"command": "n", "args": ["-y"], "env": {"K": "v"}}}}}])
    before = (j.read_bytes(), t.read_bytes())
    res = engine.apply(plan, cfg, allow_all=True)
    import tomllib
    d = json.loads(j.read_text()); assert d["other"] == 1 and set(d["mcpServers"]) == {"old", "new"}
    assert stat.S_IMODE(j.stat().st_mode) == 0o640
    tt = tomllib.loads(t.read_text()); assert tt["model"] == "x" and tt["projects"]["/a"]["trust_level"] == "trusted"
    assert tt["mcp_servers"]["new"]["env"] == {"K": "v"}
    assert engine.apply(plan, cfg, allow_all=True)["applied"] == 0
    assert engine.verify(res["manifest"], cfg)["ok"]
    engine.rollback(res["manifest"], cfg)
    assert (j.read_bytes(), t.read_bytes()) == before


def test_config_merge_refuses_bad_targets_and_drift(ctx):
    tmp, ws, cfg = ctx
    bad = ws / "bad.json"; bad.write_text("{nope")
    p = mk(tmp, [{"method": "config_merge", "config": {"target": str(bad), "format": "json", "key": "k", "entries": {"a": {"x": 1}}}}])
    with pytest.raises(Exception) as e: engine.apply(p, cfg, allow_all=True)
    assert getattr(e.value, "code", "") == "module_refused" or "bad_target" in str(e.value)
    assert bad.read_text() == "{nope"
    (ws / "real.json").write_text("{}"); (ws / "ln.json").symlink_to(ws / "real.json")
    p = mk(tmp, [{"method": "config_merge", "config": {"target": str(ws / "ln.json"), "format": "json", "key": "k", "entries": {"a": {"x": 1}}}}])
    with pytest.raises(Exception): engine.apply(p, cfg, allow_all=True)
    ok = ws / "ok.json"
    p = mk(tmp, [{"method": "config_merge", "config": {"target": str(ok), "format": "json", "key": "k", "entries": {"a": {"x": 1}}}}])
    res = engine.apply(p, cfg, allow_all=True)
    ok.write_text("{}")
    with pytest.raises(Exception): engine.rollback(res["manifest"], cfg)
