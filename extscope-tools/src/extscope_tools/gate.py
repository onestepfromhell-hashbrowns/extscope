"""Static gate: finds secrets and unsafe secret mounts in a project tree before it ships.
Fail-closed: a file that cannot be scanned is a violation. Values are never printed."""
import datetime
import fnmatch
import json
import os
import re
import shlex
import subprocess

MAX_BYTES = 1 << 20
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}
PATTERNS = ("*.env", ".env", ".env.*", "*.container", "*.service", "*.conf", "*.ini", "*.toml",
            "*.json", "*.yml", "*.yaml", "*.sh", "*.pem", "*.key", "*.volume", "*.network", "*.kube")
KEYWORDS = ("password", "passwd", "token", "secret", "private", "credential", "access_key", "kek", "api_key")
URL_CRED = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^:\s/@]*:[^@\s/]+@[^\s/]+")
PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
ASSIGN = re.compile(r"""^(?:["']?)([A-Za-z0-9_.-]+)(?:["']?)\s*([:=])\s*(.*)$""")
PAIR = re.compile(r"""["']?([A-Za-z0-9_.-]+)["']?\s*([:=])\s*("[^"]*"|'[^']*'|[^\s,}\]]+)""")
NON_SECRET_LITERAL = re.compile(r"""^["']?(-?\d+(\.\d+)?|true|false|null)["']?$""", re.I)
PLACEHOLDER = re.compile(r"""^\s*["']?(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|<[^>]*>|\{\{.*\}\}|%[A-Za-z_]+%|)["']?\s*$""")


def _violation(path, line, rule, message, root):
    return {"file": os.path.relpath(path, root), "line": line, "rule": rule, "message": message}


def sensitive_assignments(line, allowlist):
    """[(name, keyword)] for assignments whose name looks sensitive and whose value is real."""
    text = line.strip()
    if text.startswith("#") or text.startswith("//"):
        return []
    if text.startswith("Environment="):
        try:
            parts = shlex.split(text[len("Environment="):])
        except ValueError:
            parts = [text[len("Environment="):]]
    elif text.startswith("export "):
        try:
            parts = shlex.split(text[len("export "):])
        except ValueError:
            parts = [text[len("export "):]]
    else:
        parts = [text]
    out = []
    for item in parts:
        item = item.strip().rstrip(",")
        m = ASSIGN.match(item)
        # A one-line JSON/YAML flow such as {"apiKey": "x"} is not a plain assignment: scan its pairs.
        candidates = [m.groups()] if m else PAIR.findall(item)
        for name, sep, value in candidates:
            value = value.strip().rstrip(",")
            if sep == ":" and NON_SECRET_LITERAL.match(value):      # JSON/YAML counters and flags, not secrets
                continue
            if name.upper().endswith("_FILE") or name.upper() in allowlist or PLACEHOLDER.match(value):
                continue
            low = re.sub(r"[_.\-]", "", name.lower())          # apiKey, API_KEY and api-key are the same name
            for kw in KEYWORDS:
                if kw.replace("_", "") in low:
                    out.append((name, kw)); break
    return out


def _scope_mounts(node, hits, dst="", mode=""):
    if isinstance(node, list):
        for n in node:
            _scope_mounts(n, hits, dst, mode)
        return
    if not isinstance(node, dict):
        return
    d = str(node.get("dst_fhs") or node.get("dst") or dst or "")
    m = str(node.get("mode") or mode or "").lower()
    kind = str(node.get("type", "")).lower() + " " + str(node.get("module", "")).lower()
    is_mount = "mount" in kind or "dst_fhs" in node or "dst" in node
    if "secrets" in d.lower() and is_mount and m != "ro":
        hits.append(d)
    for key in ("pipeline", "include_items"):
        _scope_mounts(node.get(key), hits, d, m)


def load_waivers(path, today=None):
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    today = today or datetime.date.today()
    keep = []
    for r in rows:
        if not all(r.get(k) for k in ("path", "rule", "reason", "expires")):
            raise ValueError("waiver needs path, rule, reason, expires")
        if datetime.date.fromisoformat(r["expires"]) >= today:
            keep.append(r)
    return keep


def scan_file(path, root, allowlist):
    v = []
    try:
        size = os.path.getsize(path)
        if size > MAX_BYTES:
            return [_violation(path, 0, "unscannable", f"file larger than {MAX_BYTES} bytes", root)]
        with open(path, "rb") as fh:
            raw = fh.read()
        if b"\x00" in raw:
            return [_violation(path, 0, "unscannable", "binary content in a text-pattern file", root)]
        text = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        return [_violation(path, 0, "unscannable", f"unreadable: {exc.strerror}", root)]
    for n, line in enumerate(text.splitlines(), 1):
        if PEM.search(line):
            v.append(_violation(path, n, "private_key", "private key material", root))
        if URL_CRED.search(line):
            v.append(_violation(path, n, "url_credentials", "credentials embedded in a URL", root))
        for name, kw in sensitive_assignments(line, allowlist):
            v.append(_violation(path, n, "sensitive_assignment", f"{name} (matches '{kw}') has a literal value", root))
        s = line.strip()
        if s.startswith("Volume=") and "secrets" in s.lower():
            opts = s.split(":")
            if not (len(opts) >= 3 and "ro" in opts[-1].split(",")):
                v.append(_violation(path, n, "unsafe_secret_mount", "secrets directory mounted without :ro", root))
    if path.endswith(".json"):
        try:
            hits = []
            _scope_mounts(json.loads(text), hits)
            for d in hits:
                v.append(_violation(path, 1, "unsafe_secret_mount", f"mount of '{d}' is not mode 'ro'", root))
        except ValueError:
            pass
    return v


def tracked_secret_dirs(root, secret_dirs):
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                             env={"PATH": os.environ.get("PATH", ""), "HOME": os.devnull}).stdout.decode("utf-8", "replace")
    except OSError:
        return []
    files = [f for f in out.split("\0") if f]
    return sorted({f for f in files for d in secret_dirs if f == d or f.startswith(d.rstrip("/") + "/")})


def run(root, allowlist=(), waivers=(), secret_dirs=(".secrets",)):
    root = os.path.realpath(root)
    allow = {a.upper() for a in allowlist}
    found = []
    for f in tracked_secret_dirs(root, secret_dirs):
        found.append({"file": f, "line": 0, "rule": "secrets_dir_tracked", "message": "tracked by git"})
    scanned = 0
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if any(fnmatch.fnmatch(name, p) for p in PATTERNS):
                p = os.path.join(cur, name)
                if os.path.islink(p):
                    continue
                scanned += 1
                found += scan_file(p, root, allow)
    waived = [w for w in waivers]
    kept, used = [], []
    for x in found:
        w = next((w for w in waived if w["rule"] == x["rule"] and fnmatch.fnmatch(x["file"], w["path"])), None)
        (used if w else kept).append({**x, **({"waiver": w["reason"]} if w else {})})
    return {"root": root, "scanned_files": scanned, "violations": kept, "waived": used, "ok": not kept}
