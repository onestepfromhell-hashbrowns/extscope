# SPDX-License-Identifier: MIT
import ast, datetime, json, os, pathlib, subprocess, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from extscope_tools import anomaly, authority, gate
from extscope_tools.cli import main


def w(p, t):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(t); return p


def rules(res):
    return sorted({v["rule"] for v in res["violations"]})


def test_gate_flags_each_rule_and_never_prints_values(tmp_path):
    w(tmp_path / "a.env", "API_TOKEN=abc123realvalue\nDB_PASSWORD_FILE=/run/s/p\nNAME=ok\nSECRET=\nTOKEN=${FROM_ENV}\n")
    w(tmp_path / "svc.container", "Environment=\"DB_PASSWORD=hunter2\" \"MODE=prod\"\nVolume=/h/secrets:/s\nVolume=/h/secrets:/s2:ro,Z\n")
    w(tmp_path / "c.conf", "url = postgres://user:pw@host/db\n")
    w(tmp_path / "k.pem", "-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    w(tmp_path / "scope.json", json.dumps({"include_items": [{"dst_fhs": "/etc/secrets", "mode": "rw", "type": "mount"},
                                                              {"dst_fhs": "/etc/secrets2", "mode": "ro", "type": "mount"}]}))
    res = gate.run(str(tmp_path))
    assert rules(res) == ["private_key", "sensitive_assignment", "unsafe_secret_mount", "url_credentials"] and not res["ok"]
    dump = json.dumps(res)
    for secret in ("abc123realvalue", "hunter2", "pw@host"):
        assert secret not in dump
    names = " ".join(v["message"] for v in res["violations"] if v["rule"] == "sensitive_assignment")
    assert "API_TOKEN" in names and "DB_PASSWORD " in names and "_FILE" not in names and "SECRET " not in names
    assert sum(v["rule"] == "unsafe_secret_mount" for v in res["violations"]) == 2     # Volume without :ro + scope mount rw


def test_gate_ignores_json_counters_but_not_env_numbers(tmp_path):
    w(tmp_path / "usage.json", json.dumps({"inputTokens": 120, "ok": True, "maxToken": None, "apiKey": "abcdef123"}, indent=2))
    got = [v["message"] for v in gate.run(str(tmp_path))["violations"]]
    assert len(got) == 1 and "apiKey" in got[0]
    w(tmp_path / "a.env", "DB_PASSWORD=12345678\n")
    assert any("DB_PASSWORD" in v["message"] for v in gate.run(str(tmp_path))["violations"])


def test_gate_allowlist_waiver_and_expiry(tmp_path):
    w(tmp_path / "a.env", "PUBLIC_TOKEN_ID=abc\n")
    assert not gate.run(str(tmp_path))["ok"]
    assert gate.run(str(tmp_path), allowlist=["public_token_id"])["ok"]
    wv = tmp_path / "wv.json"
    wv.write_text(json.dumps([{"path": "a.env", "rule": "sensitive_assignment", "reason": "public id", "expires": "2999-01-01"}]))
    res = gate.run(str(tmp_path), waivers=gate.load_waivers(str(wv)))
    assert res["ok"] and res["waived"][0]["waiver"] == "public id"
    wv.write_text(json.dumps([{"path": "a.env", "rule": "sensitive_assignment", "reason": "x", "expires": "2000-01-01"}]))
    assert gate.load_waivers(str(wv)) == []                                     # expired waivers do nothing
    wv.write_text(json.dumps([{"path": "a.env", "rule": "x"}]))
    with pytest.raises(ValueError): gate.load_waivers(str(wv))


def test_gate_fail_closed_on_unscannable_and_tracked_secrets(tmp_path):
    (tmp_path / "bin.json").write_bytes(b"{\x00}")
    (tmp_path / "big.env").write_text("A=" + "x" * (gate.MAX_BYTES + 1))
    assert rules(gate.run(str(tmp_path))) == ["unscannable"]
    g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True, capture_output=True)
    g("init", "-q"); w(tmp_path / ".secrets/k", "v"); g("add", "-f", ".secrets/k")
    assert "secrets_dir_tracked" in rules(gate.run(str(tmp_path)))
    (tmp_path / "bin.json").unlink(); (tmp_path / "big.env").unlink()
    assert rules(gate.run(str(tmp_path), secret_dirs=("other",))) == []


def test_gate_skips_vendor_dirs_and_symlinks(tmp_path):
    w(tmp_path / "node_modules/x/a.env", "TOKEN=realvalue1\n")
    w(tmp_path / "real.env", "TOKEN=realvalue2\n"); os.symlink(tmp_path / "real.env", tmp_path / "link.env")
    res = gate.run(str(tmp_path))
    assert len(res["violations"]) == 1 and res["violations"][0]["file"] == "real.env"


def test_gate_cli_exit_codes(tmp_path, capsys):
    w(tmp_path / "ok.env", "NAME=1\n")
    assert main(["gate", str(tmp_path)]) == 0
    w(tmp_path / "bad.env", "TOKEN=zzzzzz\n")
    assert main(["gate", str(tmp_path)]) == 1


def test_anomaly_redacts_and_falls_back(tmp_path):
    db = str(tmp_path / "led" / "a.db")
    assert anomaly.check_writable(db)[0]
    r = anomaly.record(db, "p", "apply", "dist", "ERROR", "failed with token=abc123 at https://u:pw@h/x", source="ghp_" + "A" * 20)
    assert r["stored"] == "sqlite"
    row = anomaly.recent(db)[0]
    assert "abc123" not in row["message"] and "pw@" not in row["message"] and "ghp_AAAA" not in row["source"]
    assert anomaly.recent(db, level="INFO") == []
    bad = str(tmp_path / "isdir.db"); os.mkdir(bad)                     # sqlite cannot open a directory
    r = anomaly.record(bad, "p", "x", "y", message="boom token=zzz")
    assert r["stored"] == "fallback" and "zzz" not in open(r["path"]).read()
    arch = anomaly.archive(db, ['{"phase":"a","module":"m","message":"secret=q1"}', "not json", "[1]", ""], "proj")
    assert arch == {"attempted": 1, "recorded": 1, "skipped_bad_lines": 2, "status": "RECORDED"}
    assert anomaly.archive(db, [], "p")["status"] == "NOT_APPLICABLE"


def test_anomaly_cli_requires_explicit_db(tmp_path, monkeypatch):
    monkeypatch.delenv("EXTSCOPE_ANOMALY_DB", raising=False)
    with pytest.raises(SystemExit) as e: main(["anomaly", "list"])
    assert e.value.code == 2
    db = str(tmp_path / "x.db")
    assert main(["anomaly", "--db", db, "record", "--project", "p", "--phase", "a", "--module", "m", "--message", "hi"]) == 0
    monkeypatch.setenv("EXTSCOPE_ANOMALY_DB", db)
    assert main(["anomaly", "list"]) == 0


def scopes(*rows):
    return [{"name": n, "uuid": f"{i:08d}-0000-4000-8000-000000000000", "category": c, "includes": inc, "path": ""} for i, (n, c, inc) in enumerate(rows, 1)]


def test_authority_layers_and_manifest():
    m = authority.load_manifest(None)
    ok = scopes(("t", "tooling", []), ("s", "solution", ["t"]), ("p", "project", ["s"]))
    assert authority.check(ok, m) == []
    bad = scopes(("t", "tooling", ["s"]), ("s", "solution", []), ("x", "system", ["s"]))
    out = authority.check(bad, m)
    assert [o["rule"] for o in out] == ["reverse_layer_dependency"]          # unknown category 'system' is not judged
    assert authority.check(scopes(("a", "project", ["ghost"])), m)[0]["rule"] == "unresolved_include"
    strict = {"layers": authority.DEFAULT_LAYERS, "scopes": {"s": {"category": "preset", "allowed_dependencies": []}, "t": {}}}
    rs = {o["rule"] for o in authority.check(scopes(("t", "tooling", []), ("s", "solution", ["t"]), ("zz", "project", [])), strict)}
    assert rs == {"category_mismatch", "dependency_not_allowed", "not_in_authority"}


def test_authority_cli_and_custom_layers(tmp_path):
    a = w(tmp_path / "a.json", json.dumps({"name": "a", "uuid": "00000001-0000-4000-8000-000000000000", "category": "base", "include_scopes": ["b"]}))
    b = w(tmp_path / "b.json", json.dumps({"name": "b", "uuid": "00000002-0000-4000-8000-000000000000", "category": "app"}))
    mf = w(tmp_path / "m.json", json.dumps({"layers": {"base": 1, "app": 2}}))
    assert main(["authority", "--manifest", str(mf), str(a), str(b)]) == 1
    assert main(["authority", str(a), str(b)]) == 0                           # default map doesn't know these categories
    assert main(["authority", "--manifest", str(w(tmp_path / "bad.json", '{"oops":1}')), str(a)]) == 2


def test_independent_and_no_builtin_paths():
    for f in (ROOT / "src/extscope_tools").glob("*.py"):
        text = f.read_text()
        for n in ast.walk(ast.parse(text)):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
            assert not any(x.split(".")[0] in ("extscope", "extscope_dist", "extscope_modules", "extscope_container") for x in names), f
        for bad in ("lab-env", "/var/opt", "/srv/", "deployments.db", "SILO_HUB", "R2_"):
            assert bad not in text, (f, bad)
    for f in ROOT.parent.glob("**/SKILL.md"):
        text = f.read_text()
        for bad in ("lab-env", "/var/opt", "/srv/", "/var/backups/"):
            assert bad not in text, (f, bad)


def test_gate_scans_one_line_json_pairs(tmp_path):
    (tmp_path / "compact.json").write_text('{"apiKey": "FAKEVAL-1", "inputTokens": 123}\n')
    (tmp_path / "ok.json").write_text('{"apiKey": "${API_KEY}", "DB_PASSWORD_FILE": "/run/s", "enabled": true}\n')
    got = gate.run(str(tmp_path))["violations"]
    assert [v["file"] for v in got] == ["compact.json"]
    assert "apiKey" in got[0]["message"] and "FAKEVAL" not in str(got)
