import json, subprocess, sys


def run(*a):
    return subprocess.run([sys.executable, "-m", "extscope", *a], capture_output=True, text=True)


def test_failure_is_opaque_by_default_and_detailed_only_with_debug(tmp_path):
    bad = str(tmp_path / "missing.extscope.json")
    plain = run("validate", "--scope", bad)
    assert plain.returncode == 2 and json.loads(plain.stderr) == {"error": "operation_failed"}
    dbg = run("--debug", "validate", "--scope", bad)
    out = json.loads(dbg.stderr)
    assert dbg.returncode == 2 and out["error"] == "operation_failed" and out["type"] and "detail" in out


def test_debug_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTSCOPE_DEBUG", "1")
    out = json.loads(run("validate", "--scope", str(tmp_path / "missing.extscope.json")).stderr)
    assert out["type"]


def test_symlinked_scope_gets_a_named_rejection(tmp_path):
    real = tmp_path / "real.extscope.json"
    real.write_text("{}")
    link = tmp_path / "link.extscope.json"
    link.symlink_to(real)
    out = json.loads(run("--debug", "validate", "--scope", str(link)).stderr)
    assert "SYMLINK_REJECTED" in out["detail"]
