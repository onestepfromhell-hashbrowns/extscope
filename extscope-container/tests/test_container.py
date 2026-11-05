import ast, json, os, pathlib, stat, sys, time
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from extscope_container import creation, ephemeral, lease, plan as planmod, policy as pol, reinjection, run
from extscope_container.errors import ContainerError
from extscope_container.transport import DirTransport
from extscope_container.cli import main


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ctr"; root.mkdir()
    p = pol.Policy(allowed_roots=["/opt/app", "/etc/profile.d", "/run/secrets"], state_dir=str(tmp_path / "state"),
                   env_dir="/etc/profile.d", hooks_allowed=False)
    return tmp_path, DirTransport(str(root), ["/mnt/data"]), p, root


def preview(tmp, items):
    f = tmp / "plan.json"
    f.write_text(json.dumps({"schema": planmod.PREVIEW, "items": [{"item": i} for i in items]}))
    return planmod.parse(str(f), {})


def test_run_copy_symlink_md_block_env_key_chmod(env):
    tmp, tr, p, root = env
    skill = tmp / "skill"; skill.mkdir(); (skill / "SKILL.md").write_text("hi"); (skill / "sub").mkdir(); (skill / "sub/x").write_text("2")
    frag = tmp / "frag.md"; frag.write_text("rule")
    key = tmp / "k"; key.write_text("SECRET")
    (root / "opt/app").mkdir(parents=True); (root / "opt/app/AGENTS.md").write_text("# top\n")
    ops = preview(tmp, [{"src": str(skill), "dst_fhs": "/x", "type": "copy_inject", "mode": "ro",
                         "pipeline": [{"step": 1, "module": "copy_inject", "config": {"dst": "/opt/app/skill"}},
                                      {"step": 2, "module": "symlink", "config": {"src": "/opt/app/skill", "target": "/opt/app/link"}},
                                      {"step": 3, "module": "chmod", "config": {"mode": "0700", "target": "/opt/app/skill/sub"}},
                                      {"step": 4, "module": "env_inject", "config": {"key": "TOK", "value": "a'b"}},
                                      {"step": 5, "module": "key_inject", "config": {"src": str(key), "dst": "/run/secrets/k", "chmod": "0600"}},
                                      {"step": 6, "module": "md_block_inject", "config": {"src": str(frag), "dst": "/opt/app/AGENTS.md", "block_id": "rules"}}]}])
    dry = run.execute(ops, tr, "c1", p, dry_run=True)
    assert dry["dry_run"] and not (root / "opt/app/skill").exists()
    res = run.execute(ops, tr, "c1", p)
    assert res["applied"] == 6
    assert (root / "opt/app/skill/sub/x").read_text() == "2" and os.path.islink(root / "opt/app/link")
    assert stat.S_IMODE((root / "opt/app/skill/sub").stat().st_mode) == 0o700
    assert (root / "etc/profile.d/TOK.sh").read_text() == "export TOK='a'\\''b'\n"
    assert stat.S_IMODE((root / "run/secrets/k").stat().st_mode) == 0o600
    text = (root / "opt/app/AGENTS.md").read_text()
    assert "# top" in text and "<!-- block: rules -->" in text
    assert run.execute(ops, tr, "c1", p)["unchanged"] is True            # state hash
    assert "SECRET" not in "".join(f.read_text() for f in (tmp / "state").iterdir())
    frag.write_text("rule2")
    assert run.execute(ops, tr, "c1", p)["applied"] == 6                  # source change -> re-apply
    assert (root / "opt/app/AGENTS.md").read_text().count("<!-- block: rules -->") == 1


def test_run_refusals(env):
    tmp, tr, p, root = env
    f = tmp / "f"; f.write_text("x")
    item = lambda dst: preview(tmp, [{"src": str(f), "dst_fhs": dst, "type": "copy_inject", "mode": "ro"}])
    with pytest.raises(ContainerError) as e: run.execute(item("/etc/passwd"), tr, "c", p)
    assert e.value.code == "target_outside_allowed_roots"
    p.denied_roots = ["/opt/app/secret"]
    with pytest.raises(ContainerError) as e: run.execute(item("/opt/app/secret/x"), tr, "c", p)
    assert e.value.code == "target_denied"
    p.allowed_roots = []
    with pytest.raises(ContainerError) as e: run.execute(item("/opt/app/x"), tr, "c", p)
    assert e.value.code == "no_allowed_roots"
    with pytest.raises(ContainerError): pol.norm_posix("/opt/../etc")
    hook = preview(tmp, [{"src": "", "dst_fhs": "/x", "type": "copy_inject", "mode": "ro",
                          "pipeline": [{"step": 1, "module": "custom_hook", "config": {"script_path": "/opt/app/h.sh"}}]}])
    p.allowed_roots = ["/opt/app"]
    with pytest.raises(ContainerError) as e: run.execute(hook, tr, "c", p, allow_hooks=True)   # policy off
    assert e.value.code == "hooks_not_allowed"


def test_missing_mounts_are_reported(env):
    tmp, tr, p, _ = env
    ops = preview(tmp, [{"src": str(tmp), "dst_fhs": "/mnt/data", "type": "mount", "mode": "ro"},
                        {"src": str(tmp), "dst_fhs": "/mnt/other", "type": "mount", "mode": "rw"}])
    p.allowed_roots = ["/mnt"]
    with pytest.raises(ContainerError) as e: run.execute(ops, tr, "c", p)
    assert e.value.code == "mounts_missing" and "/mnt/other" in e.value.detail and "/mnt/data" not in e.value.detail


def test_creation_plan_formats_and_rejects_unsafe(env):
    tmp, *_ = env
    ops = preview(tmp, [{"src": "/h/skills", "dst_fhs": "/c/skills", "type": "mount", "mode": "ro"}])
    ms = creation.mounts(ops, selinux=True)
    assert creation.render(ms) == "-v /h/skills:/c/skills:ro,Z" and json.loads(creation.render(ms, "json"))[0]["dst"] == "/c/skills"
    bad = preview(tmp, [{"src": "/h/a:b", "dst_fhs": "/c", "type": "mount", "mode": "ro"}])
    with pytest.raises(ContainerError): creation.mounts(bad)


def test_lease_canary_and_self_heal(tmp_path):
    t, d, r = str(tmp_path / "tmpfs/epoch"), str(tmp_path / "disk/epoch"), str(tmp_path / "var/run/ext")
    assert not lease.valid(t, d)
    out = lease.ensure(t, d, r)
    assert out["healed"] and lease.valid(t, d) and stat.S_IMODE(os.stat(t).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.join(r, "tokens")).st_mode) == 0o700
    (pathlib.Path(r) / "tokens/leftover").write_text("old")
    assert lease.ensure(t, d, r) == {"lease": "valid", "healed": False}
    os.unlink(t)                                                   # tmpfs lost (reboot)
    assert not lease.valid(t, d)
    assert lease.ensure(t, d, r)["healed"] and not (pathlib.Path(r) / "tokens/leftover").exists()
    with pytest.raises(ContainerError): lease.purge("/tmp")      # too shallow
    os.symlink(tmp_path, tmp_path / "ln")
    with pytest.raises(ContainerError): lease.purge(str(tmp_path / "ln"))


def test_staging_references_lifecycle(tmp_path):
    st = tmp_path / "staging" / "s1"; st.mkdir(parents=True)
    m = st / "composition_manifest.json"; m.write_text(json.dumps({"references": []}))
    lease.register(str(m), {"kind": "container_lease", "container_id": "c1"})
    lease.register(str(m), {"kind": "container_lease", "container_id": "c1", "note": "renewed"})
    lease.register(str(m), {"kind": "host_registration", "id": "h1"})
    assert len(lease.references(str(m))) == 2
    assert lease.status(str(st), {"c1"})["can_gc"] is False
    assert lease.status(str(st), set())["status"] == "in_use"        # host_registration still active
    assert lease.release(str(m), "h1") and not lease.release(str(m), "nope")
    s = lease.status(str(st), set())
    assert s["status"] == "orphaned" and s["can_gc"] is True
    lease.register(str(m), {"kind": "mystery"})
    assert lease.status(str(st), set())["can_gc"] is False           # unknown kinds are retained
    (st / "composition_manifest.json").write_text("{bad")
    assert lease.status(str(st))["status"] == "corrupted"


def test_gc_and_purge_are_verified_and_bounded(tmp_path):
    base = tmp_path / "work" / "eph"; base.mkdir(parents=True)
    for n in ("alive", "dead", "_shared"):
        (base / n).mkdir(); (base / n / "f").write_text("x" * 100)
    (base / "bad name").mkdir()
    rep = ephemeral.gc(str(base), {"alive"}, dry_run=True)
    assert rep["planned"] == ["dead"] and (base / "dead").exists() and rep["errors"][0]["project"] == "bad name"
    rep = ephemeral.gc(str(base), {"alive"})
    assert rep["cleaned"] == ["dead"] and rep["skipped_active"] == ["alive"] and rep["skipped_reserved"] == ["_shared"] and rep["reclaimed_bytes"] == 100
    assert not (base / "dead").exists() and (base / "_shared/f").exists()
    (base / "alive/ext").mkdir(); (base / "alive/ext/z").write_text("zz")
    assert ephemeral.purge(str(base), "alive") == 2 and (base / "alive/f").exists()
    for bad in ("../x", "_shared", "a/b"):
        with pytest.raises(ContainerError): ephemeral.purge(str(base), bad)
    with pytest.raises(ContainerError): ephemeral.gc("/tmp", set())
    with pytest.raises(ContainerError): ephemeral.gc("relative", set())


def test_gc_cli_requires_an_active_set(tmp_path, capsys):
    base = tmp_path / "w" / "e"; base.mkdir(parents=True); (base / "p").mkdir()
    assert main(["--config", str(tmp_path / "none.json"), "gc", "--base", str(base)]) == 2
    (tmp_path / "c.json").write_text("{}")
    assert main(["--config", str(tmp_path / "c.json"), "gc", "--base", str(base)]) == 2     # refuses to guess "nothing is running"
    assert (base / "p").exists()


def test_reinjection(tmp_path):
    src = tmp_path / "tmpfs/secret"; src.parent.mkdir(); src.write_text("s"); src.chmod(0o600)
    link = tmp_path / "etc/app/secret"
    assert reinjection.establish_link(str(link), str(src)) and reinjection.verify_link(str(link), str(src)) == (True, "OK")
    src.chmod(0o644)
    assert reinjection.verify_link(str(link), str(src))[0] is False
    assert reinjection.establish_link(str(tmp_path / "x"), str(tmp_path / "missing")) is False
    d = tmp_path / "isdir"; d.mkdir()
    assert reinjection.establish_link(str(d), str(src)) is False and d.is_dir()
    snip = reinjection.quadlet_snippet("api", "/run/secrets/api", "/etc/api")
    assert "Secret=api,target=/run/secrets/api,mode=0600" in snip
    for bad in ("a b", "x;y", "$(x)", "a'b"):
        with pytest.raises(ContainerError): reinjection.quadlet_snippet("api", bad)


def test_independent_and_no_builtin_paths():
    for f in (ROOT / "src/extscope_container").glob("*.py"):
        text = f.read_text()
        for n in ast.walk(ast.parse(text)):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
            assert not any(x.split(".")[0] in ("extscope", "extscope_dist", "extscope_modules") for x in names), f
        for bad in ("/var/opt", "/srv/", "deployments.db", "/run/.ext_epoch", "/run/ext", "/var/lib"):
            assert bad not in text, (f, bad)


def _rollback_env(tmp_path):
    root = tmp_path / "ctr"; root.mkdir()
    p = pol.Policy(allowed_roots=["/opt/app"], state_dir=str(tmp_path / "state"), hooks_allowed=True)
    return DirTransport(str(root), []), p, root


def test_failed_step_rolls_back_writes(tmp_path):
    tr, p, root = _rollback_env(tmp_path)
    frag = tmp_path / "frag.md"; frag.write_text("BLOCK")
    src = tmp_path / "skill"; src.mkdir(); (src / "a.md").write_text("new")
    (root / "opt/app/skill").mkdir(parents=True); (root / "opt/app/skill/old.md").write_text("old tree")
    (root / "opt/app/AGENTS.md").write_text("user line\n")
    ops = [
        {"op": "copy", "src": str(src), "dst": "/opt/app/skill"},
        {"op": "md_block", "src": str(frag), "dst": "/opt/app/AGENTS.md", "block_id": "rules"},
        {"op": "copy", "src": str(src), "dst": "/opt/app/brand-new"},
        {"op": "hook", "script": "/opt/app/missing.sh", "args": []},   # fails in DirTransport (126)
    ]
    with pytest.raises(ContainerError) as ei:
        run.execute(ops, tr, "c1", p, allow_hooks=True)
    assert ei.value.code == "step_failed"
    assert "rolled back" in ei.value.detail and "ROLLBACK INCOMPLETE" not in ei.value.detail
    assert (root / "opt/app/skill/old.md").read_text() == "old tree"          # overwritten tree restored
    assert not (root / "opt/app/skill/a.md").exists()                          # new content removed
    assert (root / "opt/app/AGENTS.md").read_text() == "user line\n"           # md_block undone
    assert not (root / "opt/app/brand-new").exists()                           # created path removed
    assert not (tmp_path / "state").exists() or not any((tmp_path / "state").iterdir())  # no plan hash recorded


def test_failed_step_restores_mode(tmp_path):
    tr, p, root = _rollback_env(tmp_path)
    f = root / "opt/app/x.txt"; f.parent.mkdir(parents=True); f.write_text("x"); os.chmod(f, 0o644)
    ops = [{"op": "chmod", "target": "/opt/app/x.txt", "mode": "0600"},
           {"op": "hook", "script": "/opt/app/missing.sh", "args": []}]
    with pytest.raises(ContainerError):
        run.execute(ops, tr, "c1", p, allow_hooks=True)
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_rollback_reports_hooks_that_cannot_be_undone(tmp_path):
    tr, p, root = _rollback_env(tmp_path)
    script = root / "opt/app/ok.sh"; script.parent.mkdir(parents=True); script.write_text("")
    # DirTransport cannot exec sh, so a successful hook is simulated by a transport that accepts it
    class OkHook(DirTransport):
        def exec(self, cid, argv):
            if argv[0] == "/bin/sh":
                return 0, "", ""
            return super().exec(cid, argv)
    tr = OkHook(str(root), [])
    ops = [{"op": "hook", "script": "/opt/app/ok.sh", "args": []},
           {"op": "chmod", "target": "/opt/app/missing-target", "mode": "0600"}]
    with pytest.raises(ContainerError) as ei:
        run.execute(ops, tr, "c1", p, allow_hooks=True)
    assert "hooks already run are not undone" in ei.value.detail and "ok.sh" in ei.value.detail
