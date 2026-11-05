"""Anomaly ledger: structured events in SQLite, with a JSONL fallback next to it when SQLite
cannot be written. The path is always explicit. Secrets are redacted before storage."""
import json
import os
import re
import sqlite3
import time

TABLE = """CREATE TABLE IF NOT EXISTS anomaly_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL, module TEXT NOT NULL, level TEXT NOT NULL, source TEXT, target TEXT,
    message TEXT NOT NULL, created_at TEXT NOT NULL)"""
_PATTERNS = [
    (re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|bearer|auth[_-]?key)\s*[:=]\s*['\"]?([^\s'\"&,;]+)"), r"\1=[REDACTED]"),
    (re.compile(r"ghp_[A-Za-z0-9_]{10,}"), "ghp_[REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*"), "[JWT_REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----"), "[PRIVATE_KEY_REDACTED]"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
]
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")


def redact(text):
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text or "")
    return text


def check_writable(db):
    if db == ":memory:":
        return True, ""
    p = os.path.abspath(db)
    if os.path.exists(p):
        if not os.path.isfile(p):
            return False, "not a regular file"
        return (os.access(p, os.W_OK), "" if os.access(p, os.W_OK) else "not writable")
    cur = os.path.dirname(p)
    while cur and not os.path.exists(cur) and os.path.dirname(cur) != cur:
        cur = os.path.dirname(cur)
    ok = os.access(cur, os.W_OK | os.X_OK)
    return ok, "" if ok else f"directory not writable: {cur}"


def _connect(db):
    d = os.path.dirname(os.path.abspath(db))
    if db != ":memory:" and d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(db, timeout=2)
    conn.execute(TABLE)
    return conn


def record(db, project, phase, module, level="ERROR", message="", scope="", source="", target=""):
    """Returns {'stored': 'sqlite'|'fallback'|'none', ...}. Never raises: auditing must not break the caller."""
    if level not in LEVELS:
        level = "ERROR"
    row = (project or "_shared", scope, phase, module, level, redact(source), redact(target), redact(message),
           time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    try:
        conn = _connect(db)
        try:
            conn.execute("INSERT INTO anomaly_events (project,scope,phase,module,level,source,target,message,created_at) VALUES (?,?,?,?,?,?,?,?,?)", row)
            conn.commit()
        finally:
            conn.close()
        return {"stored": "sqlite"}
    except (sqlite3.Error, OSError) as exc:
        fb = db + ".fallback.jsonl"
        try:
            with open(fb, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(zip(("project", "scope", "phase", "module", "level", "source", "target", "message", "created_at"), row)), ensure_ascii=False) + "\n")
            return {"stored": "fallback", "path": fb, "reason": type(exc).__name__}
        except OSError:
            return {"stored": "none", "reason": type(exc).__name__}


def archive(db, lines, project):
    """Load JSON Lines; bad lines are skipped and counted."""
    rows, bad = [], 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            bad += 1; continue
        if isinstance(r, dict):
            rows.append(r)
        else:
            bad += 1
    stored = 0
    for r in rows:
        res = record(db, project, str(r.get("phase", "archive")), str(r.get("module", r.get("module_name", "unknown"))),
                     str(r.get("level", r.get("event_level", "ERROR"))), str(r.get("message", "")),
                     str(r.get("scope", "")), str(r.get("source", "")), str(r.get("target", "")))
        stored += res["stored"] == "sqlite"
    return {"attempted": len(rows), "recorded": stored, "skipped_bad_lines": bad,
            "status": "RECORDED" if rows and stored == len(rows) else "UNAVAILABLE" if rows else "NOT_APPLICABLE"}


def recent(db, limit=20, project=None, level=None):
    conn = _connect(db)
    try:
        q, args = "SELECT id,project,scope,phase,module,level,source,target,message,created_at FROM anomaly_events", []
        cond = []
        if project:
            cond.append("project=?"); args.append(project)
        if level:
            cond.append("level=?"); args.append(level)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY id DESC LIMIT ?"
        cols = ("id", "project", "scope", "phase", "module", "level", "source", "target", "message", "created_at")
        return [dict(zip(cols, r)) for r in conn.execute(q, args + [int(limit)])]
    finally:
        conn.close()
