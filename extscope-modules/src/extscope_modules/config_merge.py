import json
import os
import re
import shutil
import tomllib

from ._util import file_bytes, lstat_mode, salted, write_private

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}\Z")


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(k, ensure_ascii=False)} = {_scalar(x)}" for k, x in v.items()) + " }"
    raise ValueError("bad_entry: unsupported value type")


def _section(table, name, body):
    lines = [f"[{table}.{json.dumps(name, ensure_ascii=False)}]"]
    lines += [f"{k} = {_scalar(v)}" for k, v in body.items()]
    return "\n".join(lines) + "\n"


class ConfigMerge:
    """Merge named entries into one table of an existing JSON or TOML config, keeping
    every other key. json: entries go under top-level object `key`. toml: each entry
    becomes a `[table."name"]` section. on_exist: replace (default) or keep. The
    result is parsed before it is written; an invalid result is a refusal. Rollback
    restores the previous bytes, or removes the file if apply created it."""
    path_fields = ("target",)
    irreversible = False

    def validate(self, config, policy):
        if set(config) - {"target", "format", "key", "table", "entries", "on_exist"}:
            raise ValueError("unknown_config_field")
        fmt = config.get("format")
        if fmt not in ("json", "toml"):
            raise ValueError("bad_format: json or toml")
        where = config.get("key" if fmt == "json" else "table")
        if not isinstance(where, str) or not _NAME.match(where):
            raise ValueError("bad_table: key (json) / table (toml) required")
        ent = config.get("entries")
        if not isinstance(ent, dict) or not ent or not all(isinstance(v, dict) for v in ent.values()):
            raise ValueError("bad_entries: non-empty object of objects")
        if config.get("on_exist", "replace") not in ("replace", "keep"):
            raise ValueError("bad_on_exist")
        if fmt == "toml":
            for name, body in ent.items():
                if not name or not all(isinstance(k, str) and _NAME.match(k) for k in body):
                    raise ValueError("bad_entry: toml keys must be bare names")
                _section(where, name, body)
        self._check_target(config["target"])

    @staticmethod
    def _check_target(p):
        if os.path.islink(p) or (os.path.lexists(p) and not os.path.isfile(p)):
            raise ValueError("bad_target: must be absent or a regular non-symlink file")

    def _merged(self, c):
        p = c["target"]
        old = file_bytes(p).decode("utf-8") if os.path.lexists(p) else ""
        keep = c.get("on_exist", "replace") == "keep"
        if c["format"] == "json":
            try:
                doc = json.loads(old) if old.strip() else {}
            except ValueError:
                raise ValueError("bad_target: existing file is not valid JSON") from None
            if not isinstance(doc, dict) or not isinstance(doc.setdefault(c["key"], {}), dict):
                raise ValueError("bad_target: JSON root and key must be objects")
            for name, body in c["entries"].items():
                if not (keep and name in doc[c["key"]]):
                    doc[c["key"]][name] = body
            return old, json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        try:
            existing = tomllib.loads(old).get(c["table"], {})
        except tomllib.TOMLDecodeError:
            raise ValueError("bad_target: existing file is not valid TOML") from None
        text = old
        for name, body in c["entries"].items():
            block = _section(c["table"], name, body)
            rx = re.compile(rf'(?m)^\[{re.escape(c["table"])}\.(?:"{re.escape(name)}"|{re.escape(name)})\][ \t]*\n.*?(?=\n\[|\Z)', re.S)
            if rx.search(text):
                if not keep:
                    text = rx.sub(lambda _m: block.rstrip("\n") + "\n", text, count=1)
            else:
                text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + block
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            raise ValueError("bad_result: merge would produce invalid TOML") from None
        return old, text

    def describe(self, config):
        return f"config_merge {config['format']} {sorted(config['entries'])} -> {config['target']}"

    def plan(self, config, policy):
        self._check_target(config["target"])
        old, new = self._merged(config)
        if config["format"] == "json" and old.strip():
            return "noop" if json.loads(old) == json.loads(new) else "run"
        return "noop" if old == new else "run"

    def apply(self, config, policy, ctx):
        p = config["target"]
        self._check_target(p)
        _, new = self._merged(config)
        data = new.encode("utf-8")
        existed = os.path.lexists(p)
        mode = lstat_mode(p) if existed else 0o600
        tmp = write_private(p, data, mode)
        backup = ""
        try:
            if existed:
                os.makedirs(ctx["backup_dir"], mode=0o700, exist_ok=True)
                backup = os.path.join(ctx["backup_dir"], "prev")
                shutil.copy2(p, backup)
            os.replace(tmp, p)
        except BaseException:
            if os.path.lexists(tmp):
                os.unlink(tmp)
            raise
        salt, h = salted(data)
        return {"path": p, "backup": backup, "salt": salt, "salted_sha256": h}

    def undo(self, rec, force):
        p = rec["path"]
        if not force and os.path.isfile(p) and salted(file_bytes(p), rec["salt"])[1] != rec["salted_sha256"]:
            raise ValueError("config changed since apply")
        if rec["backup"]:
            shutil.copy2(rec["backup"], p)
        elif os.path.lexists(p):
            os.unlink(p)

    def verify(self, rec):
        p = rec["path"]
        if not os.path.isfile(p):
            return ["missing"]
        return [] if salted(file_bytes(p), rec["salt"])[1] == rec["salted_sha256"] else ["content_drift"]
