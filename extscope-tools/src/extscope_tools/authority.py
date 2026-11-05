"""Layer-dependency authority. Higher-numbered layers may depend on lower ones, never the
reverse. The category->layer map and optional per-scope allowances come from a manifest
you supply; the default is the five-layer model."""
import json

DEFAULT_LAYERS = {"tooling": 1, "custom": 2, "solution": 3, "preset": 4, "project": 5}


def load_manifest(path):
    if not path:
        return {"layers": dict(DEFAULT_LAYERS), "scopes": {}}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or set(data) - {"layers", "scopes", "status", "authority_id", "title"}:
        raise ValueError("manifest accepts layers, scopes, status, authority_id, title")
    layers = data.get("layers", DEFAULT_LAYERS)
    if not all(isinstance(k, str) and type(v) is int for k, v in layers.items()):
        raise ValueError("layers must map category -> integer")
    scopes = data.get("scopes", {})
    if not isinstance(scopes, dict):
        raise ValueError("scopes must be an object")
    return {"layers": layers, "scopes": scopes, "provisional": str(data.get("status", "")).lower() == "provisional"}


def dependency_ok(src_cat, tgt_cat, layers):
    s, t = layers.get(src_cat), layers.get(tgt_cat)
    if s is None or t is None:
        return True, ""
    if s < t:
        return False, f"reverse layer dependency: layer {s} ('{src_cat}') depends on higher layer {t} ('{tgt_cat}')"
    return True, ""


def check(scopes, manifest):
    """scopes: [{'name','uuid','category','includes':[names/uuids]}]. Returns violations."""
    layers, per = manifest["layers"], manifest.get("scopes", {})
    by = {}
    for s in scopes:
        by[s["name"]] = s; by[s["uuid"]] = s
    out = []
    for s in scopes:
        entry = per.get(s["name"])
        if per and entry is None:
            out.append({"scope": s["name"], "rule": "not_in_authority", "message": "scope is not listed in the authority manifest"})
        if entry and entry.get("category") and entry["category"] != s["category"]:
            out.append({"scope": s["name"], "rule": "category_mismatch",
                        "message": f"declared '{s['category']}', authority says '{entry['category']}'"})
        for ref in s["includes"]:
            tgt = by.get(ref)
            if tgt is None:
                out.append({"scope": s["name"], "rule": "unresolved_include", "message": f"'{ref}' is not in the supplied scopes"})
                continue
            ok, why = dependency_ok(s["category"], tgt["category"], layers)
            if not ok:
                out.append({"scope": s["name"], "rule": "reverse_layer_dependency", "message": why + f" via '{ref}'"})
            allowed = (entry or {}).get("allowed_dependencies")
            if allowed is not None and tgt["name"] not in allowed:
                out.append({"scope": s["name"], "rule": "dependency_not_allowed", "message": f"'{tgt['name']}' not in allowed_dependencies"})
    return out


def read_scopes(paths):
    rows, problems = [], []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            rows.append({"name": d["name"], "uuid": d["uuid"], "category": d.get("category", ""),
                         "includes": list(d.get("include_scopes", [])), "path": p})
        except (OSError, ValueError, KeyError) as exc:
            problems.append({"path": p, "error": type(exc).__name__})
    return rows, problems
