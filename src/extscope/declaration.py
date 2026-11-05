"""Strict, immutable declarations. Structural parsing is not execution approval."""
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from collections.abc import Mapping
from uuid import UUID

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
TOP_FIELDS = frozenset({'$schema', 'uuid', 'name', 'category', 'description', 'enabled',
    'include_scopes', 'exclude_scopes', 'include_items', 'exclude_items', 'tags',
    'template_vars', 'deprecated', 'deprecated_by', 'adaptation_target', 'project_name', 'version'})
ITEM_FIELDS = frozenset({'src', 'dst_fhs', 'mode', 'type', 'persist', 'pipeline', 'enabled'})
CONFIG = {'volume_mount': ({'dst'}, {'dst'}), 'copy_inject': ({'dst'}, {'dst'}),
    'symlink': ({'src','target'}, {'src','target'}),
    'chmod': ({'mode','target'}, {'mode','target'}),
    'chown': ({'owner','target'}, {'owner','target'}),
    'key_inject': ({'src','dst','chmod'}, {'src','dst','chmod','persist','sensitivity','tmpfs_dst'}),
    'env_inject': ({'key','value'}, {'key','value'}),
    'custom_hook': ({'script_path'}, {'script_path'}),
    'md_block_inject': ({'src','dst','block_id'}, {'src','dst','block_id'})}
CATEGORIES = frozenset({'system','biz','solution','preset','project','tooling','custom','resource','res','tool'})
IDENTIFIER = re.compile(r'(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.:-]+\Z')
VARIABLE = re.compile(r'[A-Z][A-Z0-9_]*\Z')


class ScopeError(ValueError):
    """Invalid declaration or incomplete plan; never a partial success."""


def require(condition, where):
    if not condition:
        raise ScopeError('invalid declaration at ' + where)


def text(value, where, *, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())), where)
    return value


def object_fields(value, allowed, where):
    require(isinstance(value, dict) and not (value.keys() - allowed), where)
    return value


def string_list(value, where, pattern=None):
    require(isinstance(value, list), where)
    for v in value:
        text(v, where)
        require(pattern is None or pattern.fullmatch(v) is not None, where)
    require(len(set(value)) == len(value), where)
    return tuple(value)


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


def validate_item(item):
    object_fields(item, ITEM_FIELDS, '/include_items')
    text(item.get('src'), '/include_items/src')
    require(text(item.get('dst_fhs'), '/include_items/dst_fhs').startswith('/'), '/include_items/dst_fhs')
    require(item.get('mode') in ('ro','rw'), '/include_items/mode')
    require(item.get('type') in ('mount','copy_inject','symlink'), '/include_items/type')
    for field in ('enabled','persist'):
        require(field not in item or type(item[field]) is bool, '/include_items/' + field)
    if 'pipeline' not in item:
        return
    steps = item['pipeline']
    require(isinstance(steps, list) and bool(steps), '/pipeline')
    previous = 0
    for step in steps:
        object_fields(step, {'step','module','config','required'}, '/pipeline')
        number = step.get('step')
        require(type(number) is int and number > previous, '/pipeline/step')
        previous = number
        require('required' not in step or type(step['required']) is bool, '/pipeline/required')
        module = step.get('module')
        require(isinstance(module, str) and module in CONFIG, '/pipeline/module')
        required, allowed = CONFIG[module]
        config = object_fields(step.get('config'), allowed, '/pipeline/config')
        require(required <= config.keys(), '/pipeline/config')
        for key, value in config.items():
            if key == 'persist':
                require(type(value) is bool, '/pipeline/config/persist')
            else:
                text(value, '/pipeline/config/' + key, empty=(key == 'value'))
        for key in ('mode','chmod'):
            if key in config:
                require(re.fullmatch(r'0?[0-7]{3,4}', config[key]) is not None, '/pipeline/config/' + key)


@dataclass(frozen=True)
class ScopeDocument:
    uuid: str
    name: str
    enabled: bool
    include_scopes: tuple[str, ...]
    exclude_scopes: tuple[str, ...]
    include_items: tuple
    exclude_items: tuple[str, ...]
    template_vars: tuple[str, ...]
    document: Mapping
    origin: str
    raw_sha256: str


def parse_document(source: str, *, origin: str = '') -> ScopeDocument:
    """Parse without I/O, ambient variables, external schema fetch or authority lookup."""
    require(isinstance(source, str) and isinstance(origin, str), '/')
    try:
        raw = source.encode('utf-8')
        require(len(raw) <= MAX_DOCUMENT_BYTES, '/')
        def pairs(entries):
            result = {}
            for key, value in entries:
                require(key not in result, '/duplicate-key')
                result[key] = value
            return result
        def reject_constant(value):
            raise ScopeError('non-finite JSON number')
        data = json.loads(source, object_pairs_hook=pairs, parse_constant=reject_constant)
    except ScopeError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ScopeError('invalid JSON document') from None
    object_fields(data, TOP_FIELDS, '/')
    uid = text(data.get('uuid'), '/uuid')
    try:
        parsed_uuid = UUID(uid)
    except ValueError:
        raise ScopeError('invalid declaration at /uuid') from None
    require(parsed_uuid.version == 4 and str(parsed_uuid) == uid, '/uuid')
    name = text(data.get('name'), '/name')
    require(IDENTIFIER.fullmatch(name) is not None, '/name')
    require(type(data.get('enabled')) is bool, '/enabled')
    includes = string_list(data.get('include_scopes', []), '/include_scopes', IDENTIFIER)
    excludes = string_list(data.get('exclude_scopes', []), '/exclude_scopes', IDENTIFIER)
    require(not (set(includes) & set(excludes)), '/include_scopes')
    variables = string_list(data.get('template_vars', []), '/template_vars', VARIABLE)
    if 'category' in data:
        require(isinstance(data['category'], str) and data['category'] in CATEGORIES, '/category')
    if 'adaptation_target' in data:
        require(data['adaptation_target'] in ('host','container'), '/adaptation_target')
    if 'deprecated' in data:
        require(type(data['deprecated']) is bool, '/deprecated')
    for field in ('$schema','description','deprecated_by','project_name'):
        if field in data:
            text(data[field], '/' + field, empty=(field == 'description'))
    if 'tags' in data:
        string_list(data['tags'], '/tags')
    items = data.get('include_items', [])
    require(isinstance(items, list), '/include_items')
    for item in items:
        validate_item(item)
    removed = data.get('exclude_items', [])
    require(isinstance(removed, list), '/exclude_items')
    excluded_paths = []
    for item in removed:
        object_fields(item, {'path'}, '/exclude_items')
        path = text(item.get('path'), '/exclude_items/path')
        require(path.startswith('/'), '/exclude_items/path')
        excluded_paths.append(path)
    require(len(set(excluded_paths)) == len(excluded_paths), '/exclude_items')
    try:
        frozen = freeze(data)
    except RecursionError:
        raise ScopeError('document nesting too deep') from None
    return ScopeDocument(uid, name, data['enabled'], includes, excludes,
                         tuple(freeze(item) for item in items), tuple(excluded_paths),
                         variables, frozen, origin, hashlib.sha256(raw).hexdigest())
