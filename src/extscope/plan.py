"""Explicit dependency expansion and immutable, non-executing pipeline plans.

Exclusions are collected globally from the enabled include closure before a
second traversal prunes excluded paths. Disabled declarations contribute no
children or exclusions. Even an excluded branch must be structurally resolvable
and acyclic. This is a deliberate strict contract, not legacy compatibility.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import PurePosixPath
import hashlib
import json
import re
from .declaration import ScopeError, VARIABLE, freeze, thaw
from .config.paths import _path

def resolve_order(entry: str, index) -> tuple:
    root = index.resolve(entry)
    if not root.enabled:
        raise ScopeError("entry scope is disabled")

    active, seen, closure = set(), set(), []

    def dfs1(curr):
        uid = curr.uuid
        if uid in active:
            raise ScopeError("include graph contains a cycle")
        if uid in seen:
            return
        active.add(uid)
        for ref in curr.include_scopes:
            child = index.resolve(ref)
            if child.enabled:
                dfs1(child)
        active.remove(uid)
        seen.add(uid)
        closure.append(curr)

    dfs1(root)

    excluded = set()
    for doc in closure:
        included_ids = {index.resolve(ref).uuid for ref in doc.include_scopes}
        excluded_ids = {index.resolve(ref).uuid for ref in doc.exclude_scopes}
        if included_ids & excluded_ids:
            raise ScopeError("include/exclude overlap through aliases")
        excluded.update(excluded_ids)

    if root.uuid in excluded:
        raise ScopeError("entry scope is excluded")

    ordered, visited = [], set()

    def dfs2(curr):
        uid = curr.uuid
        if uid in excluded or not curr.enabled or uid in visited:
            return
        visited.add(uid)
        for ref in curr.include_scopes:
            dfs2(index.resolve(ref))
        ordered.append(curr)

    dfs2(root)
    return tuple(ordered)


@dataclass(frozen=True)
class PlannedItem:
    scope_uuid: str
    scope_name: str
    origin: str
    item: Mapping


@dataclass(frozen=True)
class FlattenPlan:
    items: tuple[PlannedItem, ...]
    scopes: tuple
    plan_sha256: str
    binding_sha256: str

    def as_dict(self):
        return {'schema': 'extscope.plan.v1', 'plan_sha256': self.plan_sha256,
                **_payload(self.items, self.scopes, self.binding_sha256)}


def _payload(items, scopes, binding_sha256):
    return {'binding_sha256': binding_sha256, 'scopes': [{'uuid': d.uuid, 'origin': d.origin,
                        'declaration_sha256': d.raw_sha256} for d in scopes],
            'items': [{'scope_uuid': i.scope_uuid, 'scope_name': i.scope_name,
                       'origin': i.origin, 'item': thaw(i.item)} for i in items]}


def _bind(value, variables):
    if isinstance(value, Mapping):
        return {key: _bind(v, variables) for key, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_bind(v, variables) for v in value]
    if not isinstance(value, str):
        return value
    aliases = {'{project}': 'PROJECT_NAME', '{service}': 'PROJECT_NAME',
               '{volumes_root}': 'VOLUMES_ROOT'}
    token = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}|(\{project\}|\{service\}|\{volumes_root\})')
    def replacement(match):
        key = match.group(1) if match.group(1) is not None else aliases[match.group(2)]
        if key not in variables:
            raise ScopeError('unbound template variable')
        return variables[key]
    result = token.sub(replacement, value)
    if '{{' in result or '}}' in result or token.search(result):
        raise ScopeError('unsupported, injected or unresolved template syntax')
    return result


def _item_paths(item, source_root):
    def path(value, source=False):
        if source and not value.startswith('/') and source_root is not None:
            if str(PurePosixPath(value)) != value:
                raise ScopeError('relative source spelling must be normalized')
            value = str(PurePosixPath(source_root) / value)
        try:
            return str(_path(value))
        except ValueError:
            raise ScopeError('item paths must be explicit, absolute and normalized') from None
    item['src'] = path(item['src'], source=True)
    item['dst_fhs'] = path(item['dst_fhs'])
    for step in item.get('pipeline', []):
        config = step['config']
        for key in ('src', 'dst', 'target', 'script_path', 'tmpfs_dst'):
            if key in config:
                config[key] = path(config[key], source=(key in ('src', 'script_path')))
    return item


def flatten(entry: str, index, *, variables=None, source_root=None) -> FlattenPlan:
    """Build an inspectable plan; no source inspection, execution or authorization.

    plan_sha256 binds declarations/bindings, not current payload bytes. Publication
    must separately bind true source digests and validate policy/collisions.
    """
    if variables is None:
        variables = {}
    if not isinstance(variables, Mapping) or any(
            not isinstance(k, str) or not VARIABLE.fullmatch(k) or
            not isinstance(v, str) or not v for k, v in variables.items()):
        raise ScopeError('variables must be explicit nonempty string bindings')
    if source_root is not None:
        try:
            source_root = str(_path(source_root))
        except ValueError:
            raise ScopeError('source_root must be absolute and normalized') from None
    variables = dict(variables)
    binding = json.dumps([variables, source_root], sort_keys=True, ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    binding_sha256 = hashlib.sha256(binding).hexdigest()
    scopes = resolve_order(entry, index)
    items = []
    for document in scopes:
        if any(name not in variables for name in document.template_vars):
            raise ScopeError('missing declared template variable')
        try:
            excluded_paths = [str(_path(_bind(path, variables))) for path in document.exclude_items]
        except ValueError:
            raise ScopeError('exclude_items paths must be absolute and normalized') from None
        if len(set(excluded_paths)) != len(excluded_paths):
            raise ScopeError('duplicate expanded exclude_items path')
        excluded = set(excluded_paths)
        matched = set()
        for raw in document.include_items:
            if raw.get('enabled', True) is False:
                continue
            try:
                destination = str(_path(_bind(raw['dst_fhs'], variables)))
            except ValueError:
                raise ScopeError('item destination must be absolute and normalized') from None
            if destination in excluded:
                matched.add(destination)
                continue
            item = _item_paths(_bind(raw, variables), source_root)
            items.append(PlannedItem(document.uuid, document.name,
                                     document.origin, freeze(item)))
        if excluded - matched:
            raise ScopeError('exclude_items must match an enabled item in its own declaration')
    if not items:
        raise ScopeError('plan contains no usable items')
    items = tuple(items)
    encoded = json.dumps(['extscope.plan.v1', _payload(items, scopes, binding_sha256)],
                         sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
    return FlattenPlan(items, scopes, hashlib.sha256(encoded).hexdigest(), binding_sha256)
