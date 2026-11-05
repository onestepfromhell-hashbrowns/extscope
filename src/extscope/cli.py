"""Read-only standalone CLI. No command performs publication or grants execution."""
import argparse
import json
import os
import sys
from . import __version__
from .binding import BindingMismatch, bind_sources, verify_sources
from .declaration import ScopeError
from .digest import hash_tree
from .io import read_text_file
from .plan import flatten
from .resolver import ScopeIndex
from .plugins import plan_plugins
from . import catalog, graph, pkgs


def _receipt(path):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError('duplicate JSON receipt key')
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError('nonfinite JSON receipt value')
    return json.loads(read_text_file(path), object_pairs_hook=pairs, parse_constant=nonfinite)


def _variables(values):
    result = {}
    for value in values:
        key, separator, item = value.partition('=')
        if not separator or key in result:
            raise ValueError('bindings require unique NAME=VALUE entries')
        result[key] = item
    return result


def _preview(plan):
    result = plan.as_dict()
    result['schema'] = 'extscope.plan-preview.v1'
    result['sensitive_fields_redacted'] = True
    result['execution_authorized'] = False
    for item in result['items']:
        for step in item['item'].get('pipeline', []):
            if step['module'] == 'env_inject':
                step['config']['value'] = '[redacted]'
    return result


def _docs(args):
    docs, problems = catalog.collect(args.scope, args.scope_dir)
    if not (args.scope or args.scope_dir):
        raise ValueError('give --scope and/or --scope-dir')
    return docs, problems


def _read_only(args):
    status = 0
    if args.command in ('init', 'new'):
        sys.stdout.write(catalog.scaffold(args.name, args.category, args.description, args.include))
        return 0
    if args.command == 'pkg':
        if args.pkg_command == 'list':
            result = pkgs.list_packages(args.root, args.type)
        elif args.pkg_command == 'doctor':
            result = pkgs.doctor(args.root, args.package_id)
            status = 0 if result['ok'] else 1
        else:
            result = pkgs.override_check(args.root, args.override_id, args.candidate)
            status = {'SAFE': 0, 'REDUNDANT': 1, 'CONFLICT': 1}.get(result['status'], 3)
    else:
        docs, problems = _docs(args)
        if args.command == 'index':
            result = catalog.listing(docs, problems)
        elif args.command == 'where':
            result = catalog.where(docs, args.path)
            status = 0 if result['matches'] else 1
        elif args.command == 'resolve':
            result = catalog.resolve(docs, args.refs)
            status = 0 if not result['unresolved'] else 1
        else:
            if problems:
                raise ValueError('unreadable scope: ' + problems[0]['path'])
            idx = ScopeIndex(docs)
            if args.command == 'dag':
                g = graph.build(idx, args.entry)
                if args.format in ('json',):
                    print(graph.render(g, 'json'))
                else:
                    print(graph.render(g, args.format))
                return 0
            keys = [k.strip() for k in args.batch_keys.split(',') if k.strip()] if args.batch_keys else None
            result = graph.query(graph.records(idx, args.entry), args.pattern, args.scope_uuid, keys)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='ExtScope read-only validation, planning and source verification; not execution authorization')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--debug', action='store_true', help='on failure also print the exception class and message (default output stays opaque)')
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate', help='structural validation only; no policy approval')
    validate.add_argument('--scope', action='append', required=True)
    directory = commands.add_parser('digest', help='read a directory identity; no publication')
    directory.add_argument('path')
    plugin = commands.add_parser('plugin-plan', help='explicit pinned plugin preview; no publication')
    plugin.add_argument('--consumer', required=True)
    plugin.add_argument('--allow-source-root', action='append', default=[])
    plugin.add_argument('--allow-target-root', action='append', default=[])
    for name in ('plan','bind','verify'):
        command = commands.add_parser(name)
        command.add_argument('--scope', action='append', required=True)
        command.add_argument('--entry', required=True)
        command.add_argument('--var', action='append', default=[])
        command.add_argument('--source-root')
        if name in ('bind','verify'):
            command.add_argument('--allow-root', action='append', required=True)
        if name == 'verify':
            command.add_argument('--receipt', required=True)
    def sources(command, required=False):
        command.add_argument('--scope', action='append', default=[], help='absolute scope file (repeatable)')
        command.add_argument('--scope-dir', action='append', default=[], help='absolute directory searched for *.extscope.json / ext-default.json')
    dag = commands.add_parser('dag', help='dependency graph; no publication')
    sources(dag)
    dag.add_argument('--entry')
    dag.add_argument('--format', choices=('compact', 'mermaid', 'json', 'markdown'), default='compact')
    query = commands.add_parser('query', help='item provenance lookup; no publication')
    sources(query)
    query.add_argument('--entry')
    query.add_argument('--pattern')
    query.add_argument('--scope-uuid')
    query.add_argument('--batch-keys', help='comma separated keys; one answer per key')
    index = commands.add_parser('index', help='list scopes under explicit files/directories')
    sources(index)
    resolve = commands.add_parser('resolve', help='find scope files by uuid or name')
    sources(resolve)
    resolve.add_argument('refs', nargs='+')
    where = commands.add_parser('where', help='which scope declares this source path, and its git repo; no publication')
    sources(where)
    where.add_argument('path', help='absolute source path (e.g. the src printed by extscope-dist origin)')
    init = commands.add_parser('init', aliases=['new'], help='print a valid scope template to stdout; writes nothing')
    init.add_argument('name')
    init.add_argument('--category', default='biz')
    init.add_argument('--description', default='')
    init.add_argument('--include', action='append', default=[])
    pkg = commands.add_parser('pkg', help='read-only package identity governance')
    pkg_sub = pkg.add_subparsers(dest='pkg_command', required=True)
    p_list = pkg_sub.add_parser('list')
    p_list.add_argument('--root', required=True)
    p_list.add_argument('--type', choices=pkgs.SOURCE_TYPES)
    p_doc = pkg_sub.add_parser('doctor')
    p_doc.add_argument('--root', required=True)
    p_doc.add_argument('package_id', nargs='?')
    p_ovr = pkg_sub.add_parser('override-check')
    p_ovr.add_argument('--root', required=True)
    p_ovr.add_argument('override_id')
    p_ovr.add_argument('candidate', help='full candidate commit sha in the upstream repo')
    args = parser.parse_args(argv)
    try:
        if args.command in ('dag', 'query', 'index', 'resolve', 'where', 'init', 'new', 'pkg'):
            return _read_only(args)
        if args.command == 'plugin-plan':
            result = plan_plugins(_receipt(args.consumer), source_roots=args.allow_source_root,
                                  target_roots=args.allow_target_root)
        elif args.command == 'digest':
            result = {'algorithm':'extscope.tree.v1', 'sha256':hash_tree(args.path)}
        else:
            index = ScopeIndex.from_files(args.scope)
            if args.command == 'validate':
                result = {'status':'structurally_valid', 'scopes':len(index.documents), 'execution_authorized':False}
            else:
                plan = flatten(args.entry, index, variables=_variables(args.var), source_root=args.source_root)
                if args.command == 'plan':
                    result = _preview(plan)
                elif args.command == 'bind':
                    result = bind_sources(plan, allowed_roots=args.allow_root)
                else:
                    current = verify_sources(plan, _receipt(args.receipt), allowed_roots=args.allow_root)
                    result = {'status':'matched', 'binding_sha256':current['binding_sha256'], 'execution_authorized':False}
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    except BindingMismatch:
        print(json.dumps({'error':'binding_mismatch'}), file=sys.stderr)
        return 3
    except (ValueError, TypeError, OSError, RuntimeError, RecursionError, OverflowError) as exc:
        out = {'error':'operation_failed'}
        if args.debug or os.environ.get('EXTSCOPE_DEBUG') == '1':      # opt-in only (flag or env, the launcher takes no global flags): default stays opaque
            out.update(type=type(exc).__name__, detail=str(exc)[:300])
        print(json.dumps(out, ensure_ascii=True), file=sys.stderr)
        return 2
