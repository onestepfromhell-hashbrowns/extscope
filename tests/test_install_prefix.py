# SPDX-License-Identifier: MIT
"""install.sh --prefix keeps every write and every config lookup inside the prefix."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prefix_install_is_self_contained_and_launchers_read_prefix_config(tmp_path):
    home, prefix = tmp_path / "home", tmp_path / "pre fix"
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    done = subprocess.run(["bash", str(ROOT / "install.sh"), f"--prefix={prefix}", "--copy"],
                          env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert not home.exists()                      # nothing leaked into HOME
    conf = prefix / "etc" / "extscope"
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "dist.json").write_text(json.dumps({"allowed_roots": ["/opt/zz"]}))
    shown = subprocess.run([str(prefix / "bin" / "extscope-dist"), "show-config"],
                           env=env, capture_output=True, text=True)
    assert shown.returncode == 0, shown.stderr
    out = json.loads(shown.stdout)
    assert out["source"] == str(conf / "dist.json") and out["allowed_roots"] == ["/opt/zz"]
    # an explicit XDG root still wins over the prefix default
    other = tmp_path / "other" / "extscope"
    other.mkdir(parents=True)
    (other / "dist.json").write_text(json.dumps({"allowed_roots": ["/opt/yy"]}))
    shown = subprocess.run([str(prefix / "bin" / "extscope-dist"), "show-config"],
                           env={**env, "XDG_CONFIG_HOME": str(other.parent)},
                           capture_output=True, text=True)
    assert json.loads(shown.stdout)["allowed_roots"] == ["/opt/yy"]
