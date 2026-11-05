"""Explicit declarations, graph semantics and plans; independent behavioral tests."""
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

import pytest

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / 'src'))
from extscope.declaration import MAX_DOCUMENT_BYTES, ScopeError, parse_document, thaw
from extscope.resolver import ScopeIndex, load_document
from extscope.plan import flatten, resolve_order


def uid(name):
    return str(UUID(bytes=hashlib.sha256(name.encode()).digest()[:16], version=4))


def asset(name='asset'):
    return {'src': '/explicit/source/' + name, 'dst_fhs': '/run/ext/canonical/' + name,
            'mode': 'ro', 'type': 'copy_inject', 'persist': True,
            'pipeline': [{'step': 1, 'module': 'copy_inject',
                          'config': {'dst': '/run/ext/canonical/' + name}}]}


def body(name='root', *, includes=(), excludes=(), items=(), enabled=True, **extra):
    return {'uuid': uid(name), 'name': name, 'enabled': enabled, 'category': 'biz',
            'include_scopes': list(includes), 'exclude_scopes': list(excludes),
            'include_items': list(items), 'exclude_items': [], **extra}


def doc(name='root', **kwargs):
    return parse_document(json.dumps(body(name, **kwargs)))


def test_declaration_is_deeply_immutable_and_hashes_exact_input():
    text = json.dumps(body(items=[asset()]))
    parsed = parse_document(text)
    assert parsed.raw_sha256 == hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(TypeError): parsed.document['name'] = 'other'
    with pytest.raises(TypeError): parsed.include_items[0]['pipeline'][0]['config']['dst'] = '/other'
    with pytest.raises(AttributeError): parsed.name = 'other'
    copy = thaw(parsed.document); copy['include_items'][0]['src'] = '/different'
    assert parsed.include_items[0]['src'] == '/explicit/source/asset'


@pytest.mark.parametrize('source', ['[]', 'null', 'true', '{bad}', '',
    '{"uuid":NaN}', '{"uuid":Infinity}', '{"uuid":-Infinity}'])
def test_invalid_json_and_nonobjects_fail(source):
    with pytest.raises(ScopeError): parse_document(source)


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(uuid='not-a-uuid'),
    lambda d: d.update(enabled=1),
    lambda d: d.update(name='ambiguous name'),
    lambda d: d.update(unknown=True),
    lambda d: d.update(post_actions=[]),
    lambda d: d.update(bin_items=[]),
    lambda d: d.update(include_scopes=['x','x']),
    lambda d: d.update(include_scopes=['x'],exclude_scopes=['x']),
    lambda d: d.update(include_scopes='x'),
    lambda d: d.update(template_vars=['home']),
    lambda d: d.update(include_items=[True]),
    lambda d: d['include_items'][0].update(mode='write'),
    lambda d: d['include_items'][0].update(persist=1),
    lambda d: d['include_items'][0].update(dst_fhs='relative'),
    lambda d: d['include_items'][0].update(pipeline=[]),
    lambda d: d['include_items'][0]['pipeline'][0].update(step=True),
    lambda d: d['include_items'][0]['pipeline'][0].update(module='unknown'),
    lambda d: d['include_items'][0]['pipeline'][0].update(config={'typo':'/dst'}),
    lambda d: d['include_items'][0]['pipeline'][0].update(required=1),
])
def test_invalid_structure_is_rejected(mutation):
    data = body(items=[asset()]); mutation(data)
    with pytest.raises(ScopeError): parse_document(json.dumps(data))


def test_duplicate_keys_rejected_at_every_level():
    text = json.dumps(body(items=[asset()]))
    for source in [text.replace('"enabled": true', '"enabled": true, "enabled": false', 1),
                   text.replace('"dst": "/run/ext/canonical/asset"',
                                '"dst": "/one", "dst": "/two"')]:
        with pytest.raises(ScopeError): parse_document(source)


def test_oversized_document_and_huge_integer_fail_semantically():
    with pytest.raises(ScopeError): parse_document(' ' * (MAX_DOCUMENT_BYTES + 1))
    source = json.dumps(body(items=[asset()])).replace('"step": 1', '"step": ' + '9' * 5000)
    with pytest.raises(ScopeError): parse_document(source)


@pytest.mark.parametrize('conflict', ['uuid','name','cross-alias','same-object'])
def test_index_rejects_identity_ambiguity(conflict):
    a = doc('first')
    if conflict == 'same-object': b = a
    else:
        data = body('second')
        if conflict == 'uuid': data['uuid'] = a.uuid
        elif conflict == 'name': data['name'] = a.name
        else: data['name'] = a.uuid
        b = parse_document(json.dumps(data))
    with pytest.raises(ScopeError): ScopeIndex([a,b])


def test_diamond_is_deduplicated_by_uuid_with_aliases():
    a = doc('root', includes=['left', uid('right')])
    b = doc('left', includes=['shared']); c = doc('right', includes=[uid('shared')]); d = doc('shared')
    order = resolve_order('root', ScopeIndex([a,b,c,d]))
    assert [x.name for x in order] == ['shared','left','right','root']


def test_alias_cycle_is_not_mistaken_for_diamond():
    a = doc('root', includes=[uid('child')]); b = doc('child', includes=['root'])
    with pytest.raises(ScopeError): resolve_order('root', ScopeIndex([a,b]))


def test_global_exclusion_prunes_path_but_not_other_shared_route():
    a = doc('root', includes=['left','right','policy'])
    b = doc('left', includes=['shared']); c = doc('right', includes=['shared']); d = doc('shared')
    policy = doc('policy', excludes=[uid('left')])
    order = resolve_order('root', ScopeIndex([a,b,c,d,policy]))
    assert [x.name for x in order] == ['shared','right','policy','root']


def test_disabled_branch_contributes_no_dependencies_or_exclusions():
    a = doc('root', includes=['disabled'])
    b = doc('disabled', enabled=False, includes=['missing'], excludes=['root'])
    assert resolve_order('root', ScopeIndex([a,b])) == (a,)
    with pytest.raises(ScopeError): resolve_order('disabled', ScopeIndex([a,b]))


def test_excluded_policy_still_contributes_its_global_exclusions():
    a = doc('root', includes=['leaf','policy','other-policy'])
    b = doc('leaf'); policy = doc('policy', excludes=['leaf'])
    other = doc('other-policy', excludes=['policy'])
    assert [d.name for d in resolve_order('root', ScopeIndex([a,b,policy,other]))] == ['other-policy','root']


@pytest.mark.parametrize('case', ['missing-include','missing-exclude','excluded-root','excluded-broken-branch'])
def test_incomplete_graphs_fail_without_partial_success(case):
    if case == 'missing-include': docs = [doc(includes=['missing'])]
    elif case == 'missing-exclude': docs = [doc(excludes=['missing'])]
    elif case == 'excluded-root': docs = [doc(includes=['child']), doc('child',excludes=['root'])]
    else:
        docs = [doc(includes=['left','policy']), doc('left', includes=['missing']), doc('policy',excludes=['left'])]
    with pytest.raises(ScopeError): resolve_order('root', ScopeIndex(docs))


def test_flatten_bindings_exclusions_and_provenance():
    item = asset('{{PROJECT_NAME}}')
    disabled = asset('disabled'); disabled['enabled'] = False
    removed = asset('removed')
    root = doc(items=[item,disabled,removed], template_vars=['PROJECT_NAME'],
               exclude_items=[{'path':'/run/ext/canonical/removed'}])
    result = flatten('root', ScopeIndex([root]), variables={'PROJECT_NAME':'demo'})
    assert len(result.items) == 1
    assert result.items[0].item['src'] == '/explicit/source/demo'
    assert result.items[0].item['pipeline'][0]['config']['dst'] == '/run/ext/canonical/demo'
    assert result.items[0].scope_uuid == root.uuid
    output = result.as_dict(); claimed = output.pop('plan_sha256'); output.pop('schema')
    expected = hashlib.sha256(json.dumps(['extscope.plan.v1',output], sort_keys=True,
                              ensure_ascii=True,separators=(',',':')).encode('ascii')).hexdigest()
    assert claimed == expected
    output['items'][0]['item']['src'] = '/changed'
    assert result.items[0].item['src'] == '/explicit/source/demo'


def test_explicit_source_root_and_legacy_token_binding():
    item = asset(); item['src'] = 'bundle/{{USER_HOME}}'; item['dst_fhs'] = '/cap/{project}'
    result = flatten('root', ScopeIndex([doc(items=[item])]), source_root='/source',
                     variables={'USER_HOME':'fragment','PROJECT_NAME':'demo'})
    assert result.items[0].item['src'] == '/source/bundle/fragment'
    assert result.items[0].item['dst_fhs'] == '/cap/demo'


@pytest.mark.parametrize('case', ['missing','ambient','empty','bad-key','relative-source','traversal','injected-token','empty-plan'])
def test_unbound_invalid_or_empty_plan_fails(case, monkeypatch):
    item = asset('{{PROJECT_NAME}}')
    variables = None
    if case == 'ambient': monkeypatch.setenv('PROJECT_NAME','ambient-is-not-input')
    elif case == 'empty': variables = {'PROJECT_NAME':''}
    elif case == 'bad-key': variables = {'project_name':'demo'}
    elif case == 'relative-source': item = asset(); item['src'] = 'relative'
    elif case == 'traversal': variables = {'PROJECT_NAME':'../escape'}
    elif case == 'injected-token': variables = {'PROJECT_NAME':'{service}'}
    items = [] if case == 'empty-plan' else [item]
    with pytest.raises(ScopeError): flatten('root', ScopeIndex([doc(items=items)]), variables=variables)


def test_all_bindings_are_part_of_plan_identity():
    index = ScopeIndex([doc(items=[asset()])])
    a = flatten('root',index,variables={'CONTEXT':'one'})
    b = flatten('root',index,variables={'CONTEXT':'two'})
    assert a.plan_sha256 != b.plan_sha256
    assert a.binding_sha256 != b.binding_sha256


def test_parsing_and_planning_do_not_inspect_source_files(monkeypatch):
    index = ScopeIndex([doc(items=[asset()])])
    def forbidden(*args, **kwargs): raise AssertionError('unexpected filesystem lookup')
    with monkeypatch.context() as guard:
        guard.setattr(os,'open',forbidden); guard.setattr(os,'listdir',forbidden)
        assert len(flatten('root',index).items) == 1
        with pytest.raises(ScopeError): index.resolve('biz-sample-missing')


def test_explicit_loader_covers_bytes_and_does_not_follow_scope_links(tmp_path):
    p = tmp_path/'scope.json'; source = json.dumps(body(items=[asset()])); p.write_text(source)
    parsed = load_document(p)
    assert parsed.origin == str(p)
    assert parsed.raw_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    index = ScopeIndex.from_files([p]); assert len(flatten('root',index).items) == 1
    link = tmp_path/'link.json'; link.symlink_to(p)
    with pytest.raises(OSError): load_document(link)
    with pytest.raises(ScopeError): ScopeIndex.from_files(p)
    with pytest.raises(ScopeError): load_document('relative.json')


@pytest.mark.parametrize('case', ['fifo','invalid-utf8','oversize'])
def test_loader_rejects_invalid_inputs(tmp_path, case):
    p = tmp_path/'scope.json'
    if case == 'fifo': os.mkfifo(p)
    elif case == 'invalid-utf8': p.write_bytes(b'\xff')
    else: p.write_bytes(b' ' * (MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(ScopeError): load_document(p)


def test_loader_detects_observed_mutation_and_closes_fd(tmp_path, monkeypatch):
    p = tmp_path/'scope.json'; p.write_text(json.dumps(body()))
    original = os.read; changed = False
    def mutated(fd, count):
        nonlocal changed
        result = original(fd,count)
        if not changed:
            changed = True; p.write_text(json.dumps(body('different')))
        return result
    before = len(os.listdir('/proc/self/fd'))
    with monkeypatch.context() as guard:
        guard.setattr(os,'read',mutated)
        with pytest.raises(ScopeError): load_document(p)
    assert len(os.listdir('/proc/self/fd')) == before


def test_include_exclude_overlap_cannot_hide_behind_aliases():
    root = doc(includes=['leaf'], excludes=[uid('leaf')], items=[asset()])
    with pytest.raises(ScopeError): flatten('root', ScopeIndex([root,doc('leaf')]))


@pytest.mark.parametrize('token', ['{project}', '{service}', '{volumes_root}', '{{PROJECT_NAME}}'])
def test_variable_values_are_not_reparsed_as_templates(token):
    item = asset(); item['src'] = '{{SOURCE}}'
    variables = {'SOURCE':'/source/' + token, 'PROJECT_NAME':'expanded', 'VOLUMES_ROOT':'/volumes'}
    with pytest.raises(ScopeError): flatten('root', ScopeIndex([doc(items=[item])]), variables=variables)


@pytest.mark.parametrize('path', ['/run/ext/canonical/typo', '//run/ext/canonical/asset',
    '/run/ext/canonical/x/../asset', '/run/ext/canonical/asset/'])
def test_invalid_or_unmatched_item_exclusions_never_silently_succeed(path):
    root = doc(items=[asset()], exclude_items=[{'path':path}])
    with pytest.raises(ScopeError): flatten('root', ScopeIndex([root]))


def test_duplicate_item_exclusions_are_rejected():
    with pytest.raises(ScopeError):
        doc(items=[asset()], exclude_items=[{'path':'/one'},{'path':'/one'}])
    root = doc(items=[asset()],exclude_items=[{'path':'/{{A}}'},{'path':'/{{B}}'}])
    with pytest.raises(ScopeError): flatten('root',ScopeIndex([root]),variables={'A':'same','B':'same'})


def test_item_exclusion_is_local_and_cannot_hide_child_items():
    root = doc(includes=['child'],items=[asset('root')],
               exclude_items=[{'path':'/run/ext/canonical/child'}])
    child = doc('child', items=[asset('child')])
    with pytest.raises(ScopeError): flatten('root',ScopeIndex([root,child]))


def test_excluded_item_does_not_require_undeclared_source_binding():
    excluded = asset('removed'); excluded['src'] = '{{NOT_NEEDED}}'
    root = doc(items=[asset(),excluded], exclude_items=[{'path':'/run/ext/canonical/removed'}])
    result = flatten('root', ScopeIndex([root]))
    assert len(result.items)==1 and result.items[0].item['src']=='/explicit/source/asset'


@pytest.mark.parametrize("source", ["foo//bar", "foo/./bar", "foo/"])
def test_source_root_does_not_silently_normalize_relative_spelling(source):
    item = asset(); item["src"] = source
    with pytest.raises(ScopeError):
        flatten("root",ScopeIndex([doc(items=[item])]),source_root="/explicit/root")


def test_version_metadata_field_is_accepted():
    from extscope.declaration import TOP_FIELDS
    assert 'version' in TOP_FIELDS
