"""Plan input: the JSON `extscope plan` prints (extscope.plan-preview.v1) or a native
list. Read as plain data; produces container-side operations."""
import json
import re

from .errors import ContainerError
from .policy import expand, norm_posix

PREVIEW = "extscope.plan-preview.v1"
NATIVE = "extscope-container.plan.v1"
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _mode(text, default):
    text = default if text is None else text
    if not isinstance(text, str) or not re.fullmatch(r"0?[0-7]{3,4}", text):
        raise ContainerError("bad_mode", str(text))
    if int(text, 8) & 0o7000:
        raise ContainerError("special_mode_bits_refused", text)
    return int(text, 8)


def parse(path, variables):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ContainerError("bad_plan", f"{path}: {exc}") from None
    if not isinstance(doc, dict) or doc.get("schema") not in (PREVIEW, NATIVE):
        raise ContainerError("bad_plan", f"schema must be {PREVIEW} or {NATIVE}")
    ex = lambda v: expand(v, variables) if isinstance(v, str) else v
    ops = []
    if doc["schema"] == NATIVE:
        for o in doc.get("ops", []):
            ops.append({k: ex(v) for k, v in o.items()})
        return _check(ops)
    for entry in doc.get("items", []):
        it = entry.get("item", {})
        if it.get("enabled") is False:
            continue
        src, dst, mode = ex(it.get("src", "")), ex(it.get("dst_fhs", "")), it.get("mode", "ro")
        steps = it.get("pipeline")
        if not steps:
            if it.get("type") == "mount":
                ops.append({"op": "mount", "src": src, "dst": dst, "mode": mode})
            elif it.get("type") == "symlink":
                ops.append({"op": "symlink", "target": src, "dst": dst})
            else:
                ops.append({"op": "copy", "src": src, "dst": dst})
            continue
        for s in steps:
            m, c = s.get("module"), {k: ex(v) for k, v in s.get("config", {}).items()}
            if m == "volume_mount":
                ops.append({"op": "mount", "src": src, "dst": c["dst"], "mode": mode})
            elif m == "copy_inject":
                ops.append({"op": "copy", "src": src, "dst": c["dst"]})
            elif m == "symlink":
                ops.append({"op": "symlink", "target": c["src"], "dst": c["target"]})
            elif m == "md_block_inject":
                ops.append({"op": "md_block", "src": c["src"], "dst": c["dst"], "block_id": c["block_id"]})
            elif m == "chmod":
                ops.append({"op": "chmod", "target": c["target"], "mode": c["mode"]})
            elif m == "chown":
                ops.append({"op": "chown", "target": c["target"], "owner": c["owner"]})
            elif m == "env_inject":
                ops.append({"op": "env", "key": c["key"], "value": c["value"]})
            elif m == "key_inject":
                dst = c["dst"]
                if c.get("sensitivity") == "secret" and c.get("persist") is False and c.get("tmpfs_dst"):
                    dst = c["tmpfs_dst"]
                ops.append({"op": "key", "src": c["src"], "dst": dst, "mode": c.get("chmod", "0600")})
            elif m == "custom_hook":
                ops.append({"op": "hook", "script": c["script_path"], "args": c.get("args", [])})
            else:
                raise ContainerError("unsupported_module", str(m))
    return _check(ops)


def _check(ops):
    for o in ops:
        kind = o.get("op")
        if kind not in ("mount", "copy", "symlink", "md_block", "chmod", "chown", "env", "key", "hook"):
            raise ContainerError("bad_plan", f"op {kind!r}")
        if kind == "env" and not KEY.match(str(o.get("key", ""))):
            raise ContainerError("bad_env_key", str(o.get("key")))
        if kind == "env" and ("\n" in o.get("value", "") or "\x00" in o.get("value", "")):
            raise ContainerError("bad_env_value", o["key"])
        if kind == "chmod":
            _mode(o.get("mode"), None)
        if kind == "key":
            _mode(o.get("mode"), "0600")
    return ops
