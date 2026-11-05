"""Dependency graph and item provenance over an explicit scope catalog. Read-only."""
import fnmatch
import json

from .declaration import ScopeError, thaw


def build(index, entry=None):
    """Nodes reachable from `entry` (or every enabled document), with edge validity checks."""
    docs = {d.uuid: d for d in index.documents}
    if entry is not None:
        start = [index.resolve(entry).uuid]
    else:
        start = [d.uuid for d in index.documents]
    nodes, edges, order = {}, [], []

    def visit(uid, stack):
        if uid in stack:
            raise ScopeError("include graph contains a cycle")
        if uid in nodes:
            return
        d = docs[uid]
        nodes[uid] = {"uuid": uid, "name": d.name, "category": d.document.get("category", ""),
                      "enabled": d.enabled, "origin": d.origin,
                      "children": [], "items": len(d.include_items)}
        for ref in d.include_scopes:
            child = index.resolve(ref)
            nodes[uid]["children"].append(child.uuid)
            edges.append((uid, child.uuid))
            visit(child.uuid, stack | {uid})
        order.append(uid)

    for uid in start:
        visit(uid, frozenset())
    roots = sorted(set(nodes) - {c for _, c in edges}, key=lambda u: nodes[u]["name"])
    return {"nodes": nodes, "edges": edges, "roots": roots, "order": order}


def render(graph, fmt="compact"):
    nodes = graph["nodes"]
    if fmt == "json":
        return json.dumps({"nodes": list(nodes.values()), "edges": graph["edges"]}, indent=2, sort_keys=True)
    if fmt == "mermaid":
        lines = ["graph TD"]
        for n in nodes.values():
            mark = "" if n["enabled"] else " (disabled)"
            lines.append(f'  {n["uuid"][:8]}["{n["name"]}{mark}"]')
        lines += [f"  {nodes[a]['uuid'][:8]} --> {nodes[b]['uuid'][:8]}" for a, b in graph["edges"]]
        return "\n".join(lines)
    out = []

    def walk(uid, depth, seen):
        n = nodes[uid]
        pad = "  " * depth
        flag = "" if n["enabled"] else " [disabled]"
        if fmt == "markdown":
            out.append(f"{pad}- **{n['name']}** `{n['category'] or '-'}` ({n['items']} items){flag}")
        else:
            out.append(f"{pad}{n['name']} [{n['category'] or '-'}] items={n['items']}{flag}")
        if uid in seen:
            return
        for c in n["children"]:
            walk(c, depth + 1, seen | {uid})

    for r in graph["roots"]:
        walk(r, 0, frozenset())
    return "\n".join(out)


def records(index, entry=None):
    """One provenance record per declared item, in a stable order."""
    graph = build(index, entry)
    docs = {d.uuid: d for d in index.documents}
    out = []
    for uid in graph["order"]:
        d = docs[uid]
        for it in d.include_items:
            it = thaw(it)
            out.append({"scope_uuid": uid, "scope_name": d.name, "scope_path": d.origin,
                        "src": it.get("src", ""), "dst_fhs": it.get("dst_fhs", ""),
                        "type": it.get("type", ""), "mode": it.get("mode", "")})
    return out


def query(recs, pattern=None, scope_uuid=None, batch_keys=None):
    def hit(r):
        hay = " ".join((r["scope_name"], r["src"], r["dst_fhs"])).lower()
        if scope_uuid and r["scope_uuid"] != scope_uuid:
            return False
        if pattern and not (fnmatch.fnmatch(hay, "*" + pattern.lower() + "*") or pattern.lower() in hay):
            return False
        return True
    out = [r for r in recs if hit(r)]
    if batch_keys:                       # one answer per key: which items mention it
        return {k: [r for r in out if k.lower() in " ".join((r["scope_name"], r["src"], r["dst_fhs"])).lower()]
                for k in batch_keys}
    return out
