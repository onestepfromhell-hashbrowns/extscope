"""Explicit, pinned trusted-plugin planning; not an OS or dependency sandbox.

Managed immutable installation and a fresh plugin namespace are prerequisites.
Pre-existing bytecode or modules are rejected rather than trusted or reloaded.
"""
from dataclasses import asdict
from importlib import metadata, util
from pathlib import Path, PurePosixPath
import hashlib
import os
import re
import stat
import sys
from . import __version__
from .binding import SourceRoots, canonical
from .declaration import freeze
from .digest import _fingerprint, _read_file, hash_tree_fd, hash_file_fd
from .plugin_api import API_VERSION, SCHEMA_VERSION, PluginContext, TargetOperation, SourceRequirement, PluginPlan, validate_path

LOCK_FIELDS = {'id','distribution','version','api_version','schema_version',
               'namespace','entry_point','files','payload_sha256','config'}


def _check(condition):
    if not condition:
        raise ValueError('invalid plugin consumer or pin')


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _distribution_name(value):
    return re.sub('[-_.]+','-',value).lower()


def _locks(consumer):
    _check(isinstance(consumer, dict) and not (consumer.keys()-{'schema','core_version','plugins'}))
    _check(consumer.get('schema')=='extscope.consumer.v1' and isinstance(consumer.get('plugins'),list))
    _check('core_version' not in consumer or consumer['core_version']==__version__)
    records=consumer['plugins']
    if records:
        _check(consumer.get('core_version')==__version__)
    ids=set()
    for record in records:
        _check(isinstance(record,dict) and set(record)==LOCK_FIELDS)
        identifier=record['id'];namespace=record['namespace']
        _check(isinstance(identifier,str) and re.fullmatch('[a-z0-9][a-z0-9_.-]*',identifier))
        _check(identifier not in ids);ids.add(identifier)
        _check(isinstance(namespace,str) and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',namespace) and namespace!='extscope')
        _check(all(isinstance(record[k],str) and record[k] for k in ('distribution','version','entry_point')))
        _check(type(record['api_version']) is int and record['api_version']==API_VERSION)
        _check(type(record['schema_version']) is int and record['schema_version']==SCHEMA_VERSION)
        _check(re.fullmatch(re.escape(namespace)+r'(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*',record['entry_point']))
        files=record['files'];_check(isinstance(files,dict) and '__init__.py' in files)
        for name,digest in files.items():
            _check(isinstance(name,str) and name and not name.startswith('/') and str(PurePosixPath(name))==name)
            _check('..' not in PurePosixPath(name).parts and '__pycache__' not in PurePosixPath(name).parts and not name.endswith('.pyc'))
            _check('\\' not in name and '\x00' not in name and _sha(digest))
        _check(_sha(record['payload_sha256']) and hashlib.sha256(canonical(files)).hexdigest()==record['payload_sha256'])
        _check(isinstance(record['config'],dict))
    return tuple(freeze(record) for record in records)


def _verify_payload(dist, record):
    root=Path(dist.locate_file(record['namespace']))
    expected=record['files'];allowed_dirs={''}
    for name in expected:
        allowed_dirs.update(str(p) for p in PurePosixPath(name).parents if str(p)!='.')
    seen=set()
    with SourceRoots([root]) as grant:
        fd=grant.open_source(root)
        def walk(current,relative):
            before=os.fstat(current)
            for name in sorted(os.listdir(current)):
                path=relative+'/'+name if relative else name
                observed=os.stat(name,dir_fd=current,follow_symlinks=False)
                if stat.S_ISDIR(observed.st_mode):
                    _check(path in allowed_dirs)
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
                    try:
                        _check(_fingerprint(os.fstat(child))==_fingerprint(observed));walk(child,path)
                        _check(_fingerprint(os.stat(name,dir_fd=current,follow_symlinks=False))==_fingerprint(observed))
                    finally:os.close(child)
                else:
                    _check(stat.S_ISREG(observed.st_mode) and path in expected)
                    _check(_read_file(current,name,observed)==expected[path]);seen.add(path)
            _check(_fingerprint(os.fstat(current))==_fingerprint(before))
        try:walk(fd,'')
        finally:os.close(fd)
    _check(seen==set(expected))
    return root


def _prepare(records):
    # Metadata discovery occurs only after a nonempty, valid explicit selection.
    available=metadata.entry_points(group='extscope.plugins')
    prepared=[]
    namespaces=set()
    for record in records:
        _check(record['namespace'] not in namespaces);namespaces.add(record['namespace'])
        candidates=[ep for ep in available if ep.name==record['id']]
        _check(len(candidates)==1);ep=candidates[0]
        _check(ep.dist is not None and ep.dist.version==record['version'])
        _check(_distribution_name(ep.dist.metadata['Name'])==_distribution_name(record['distribution']))
        _check(ep.value==record['entry_point'])
        _check(not any(name==record['namespace'] or name.startswith(record['namespace']+'.') for name in sys.modules))
        root=_verify_payload(ep.dist,record)
        spec=util.find_spec(record['namespace'])
        _check(spec is not None and spec.origin==str(root/'__init__.py'))
        prepared.append((record,ep,root))
    return prepared


def _validate_operations(operations, context):
    result=[];targets=[];blocks=set()
    for operation in operations:
        _check(isinstance(operation,TargetOperation) and operation.operation=='symlink')
        source=str(validate_path(operation.source));target=str(validate_path(operation.target))
        _check(source!=target and _sha(operation.source_sha256))
        _check(any(PurePosixPath(target).is_relative_to(root) and target!=root for root in context.target_roots))
        _check(not any(target==old or PurePosixPath(target).is_relative_to(old) or PurePosixPath(old).is_relative_to(target) for old in targets))
        if operation.block_id is not None:
            _check(isinstance(operation.block_id,str) and bool(operation.block_id) and operation.block_id not in blocks)
            blocks.add(operation.block_id)
        fd=context.sources.open_source(source)
        try:
            observed=os.fstat(fd)
            if stat.S_ISDIR(observed.st_mode):
                kind,algorithm,digest='directory','extscope.tree.v1',hash_tree_fd(fd)
            else:
                kind,algorithm,digest='file','sha256',hash_file_fd(fd)
            _check(operation.source_kind==kind and operation.algorithm==algorithm and operation.source_sha256==digest)
            _check(type(operation.source_mode) is int and operation.source_mode==stat.S_IMODE(observed.st_mode))
        finally:os.close(fd)
        targets.append(target);result.append(operation)
    _check(bool(result))
    return tuple(result)


def _validate_requirements(requirements, context):
    pinned={}
    for requirement in requirements:
        _check(isinstance(requirement,SourceRequirement))
        source=str(validate_path(requirement.source));_check(_sha(requirement.sha256))
        _check(source not in pinned or pinned[source]==requirement)
        pinned[source]=requirement
    for source,requirement in sorted(pinned.items()):
        fd=context.sources.open_source(source)
        try:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                kind,algorithm,digest='directory','extscope.tree.v1',hash_tree_fd(fd)
            else:
                kind,algorithm,digest='file','sha256',hash_file_fd(fd)
            _check(requirement.source_kind==kind and requirement.algorithm==algorithm and requirement.sha256==digest)
        finally:os.close(fd)
    return tuple(pinned[key] for key in sorted(pinned))


def _invoke(record, entry_point, root, context):
    old=sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode=True
        plugin=entry_point.load()()
        module=sys.modules.get(record['namespace'])
        _check(module is not None and getattr(module,'__file__',None)==str(root/'__init__.py'))
        _check(type(getattr(plugin,'api_version',None)) is int and plugin.api_version==API_VERSION)
        _check(type(getattr(plugin,'schema_version',None)) is int and plugin.schema_version==SCHEMA_VERSION)
        _check(callable(getattr(plugin,'plan',None)))
        return plugin.plan(record['config'],context)
    except Exception:
        raise ValueError('enabled plugin failed to initialize or plan') from None
    finally:
        sys.dont_write_bytecode=old


def plan_plugins(consumer, *, source_roots=(), target_roots=()):
    """Load only explicitly pinned plugins and return validated, read-only targets."""
    records=_locks(consumer)
    if not records:
        return {'schema':'extscope.plugin-plan-preview.v1','status':'disabled','execution_authorized':False,'operations':[]}
    targets=tuple(sorted(str(validate_path(path)) for path in target_roots))
    _check(bool(targets) and len(set(targets))==len(targets))
    prepared=_prepare(records)
    operations=[];requirements=[]
    with SourceRoots(source_roots) as grants:
        context=PluginContext(grants,targets)
        for record,ep,root in prepared:
            batch=_invoke(record,ep,root,context)
            _check(isinstance(batch,PluginPlan) and isinstance(batch.operations,tuple) and bool(batch.operations))
            _check(isinstance(batch.dependencies,tuple));operations.extend(batch.operations);requirements.extend(batch.dependencies)
        operations=_validate_operations(operations,context)
        requirements=_validate_requirements(requirements,context)
    payload={'schema':'extscope.plugin-plan-preview.v1','execution_authorized':False,
             'consumer_sha256':hashlib.sha256(canonical(consumer)).hexdigest(),
             'source_roots':list(grants.paths),'target_roots':list(targets),
             'plugins':[{'id':r['id'],'distribution':r['distribution'],'version':r['version'],
                         'payload_sha256':r['payload_sha256']} for r in records],
             'operations':[asdict(operation) for operation in operations],
             'source_requirements':[asdict(requirement) for requirement in requirements]}
    return {**payload,'plan_sha256':hashlib.sha256(canonical(payload)).hexdigest()}
