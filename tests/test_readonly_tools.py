"""dag / query / index / resolve / init / pkg: read-only, explicit roots, no ambient search."""
import hashlib, json, os, subprocess, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from extscope import catalog, graph, pkgs
from extscope.cli import main
from extscope.declaration import ScopeError, parse_document
from extscope.resolver import ScopeIndex

U = lambda n: f'{n:08d}-0000-4000-8000-000000000000'


def scope(path, n, name, category='biz', includes=(), items=()):
    doc = {'uuid': U(n), 'name': name, 'category': category, 'enabled': True,
           'include_scopes': list(includes), 'include_items': list(items)}
    path.write_text(json.dumps(doc)); return str(path)


@pytest.fixture
def tree(tmp_path):
    item = {'src': '/s/ponytail', 'dst_fhs': '/d/ponytail', 'mode': 'ro', 'type': 'copy_inject'}
    a = scope(tmp_path / 'a.extscope.json', 1, 'biz-a', items=[item])
    b = scope(tmp_path / 'b.extscope.json', 2, 'solution-b', 'solution', includes=['biz-a'])
    c = scope(tmp_path / 'sub' / 'ext-default.json', 3, 'proj-c', 'project', includes=[U(2)]) if (tmp_path / 'sub').mkdir() is None else ''
    return tmp_path, [a, b, c]


def test_graph_edges_roots_and_formats(tree):
    root, files = tree
    docs, problems = catalog.collect([], [str(root)])
    assert not problems and len(docs) == 3
    idx = ScopeIndex(docs)
    g = graph.build(idx, 'proj-c')
    assert [g['nodes'][u]['name'] for u in g['order']] == ['biz-a', 'solution-b', 'proj-c']
    assert g['roots'] == [U(3)]
    assert 'graph TD' in graph.render(g, 'mermaid') and json.loads(graph.render(g, 'json'))['edges']
    assert graph.render(g, 'compact').splitlines()[1].startswith('  solution-b')


def test_graph_cycle_is_refused(tmp_path):
    a = scope(tmp_path / 'a.extscope.json', 1, 'a', includes=['b'])
    b = scope(tmp_path / 'b.extscope.json', 2, 'b', includes=['a'])
    idx = ScopeIndex(catalog.collect([a, b], [])[0])
    with pytest.raises(ScopeError): graph.build(idx)


def test_query_by_pattern_scope_and_batch(tree):
    root, _ = tree
    idx = ScopeIndex(catalog.collect([], [str(root)])[0])
    recs = graph.records(idx, 'proj-c')
    assert len(recs) == 1 and recs[0]['scope_name'] == 'biz-a'
    assert graph.query(recs, pattern='PONYTAIL') == recs
    assert graph.query(recs, scope_uuid=U(9)) == []
    got = graph.query(recs, batch_keys=['ponytail', 'nothing'])
    assert len(got['ponytail']) == 1 and got['nothing'] == []


def test_index_resolve_report_bad_files_without_hiding_good(tree):
    root, _ = tree
    (root / 'bad.extscope.json').write_text('{not json')
    docs, problems = catalog.collect([], [str(root)])
    out = catalog.listing(docs, problems)
    assert out['count'] == 3 and len(out['problems']) == 1
    r = catalog.resolve(docs, ['biz-a', U(2), 'ghost'])
    assert [x['name'] for x in r['resolved']] == ['biz-a', 'solution-b'] and r['unresolved'][0]['ref'] == 'ghost'


def test_discovery_requires_absolute_roots():
    with pytest.raises(ScopeError): catalog.discover(['relative/dir'])


def test_init_template_is_valid_and_writes_nothing(tmp_path, capsys):
    before = set(os.listdir(tmp_path)); os.chdir(tmp_path)
    assert main(['init', 'biz-new', '--category', 'biz']) == 0
    doc = parse_document(capsys.readouterr().out)
    assert doc.name == 'biz-new' and set(os.listdir(tmp_path)) == before
    with pytest.raises(ScopeError): catalog.scaffold('x', 'nope')


def make_pkg(root, pid, files, source_type='inhouse_native', **extra):
    d = root / pid; d.mkdir(parents=True)
    for n, t in files.items():
        (d / n).parent.mkdir(parents=True, exist_ok=True); (d / n).write_text(t)
    digest, _ = pkgs.content_digest(str(d))
    meta = {'schema_version': 1, 'package_id': pid, 'source_type': source_type,
            'source': {'kind': 'directory', 'locator': f'local://{pid}'}, 'content_digest': digest,
            'exports': [{'id': 'main', 'kind': 'asset', 'path': 'a.txt'}], **extra}
    (root / '.meta').mkdir(exist_ok=True)
    (root / '.meta' / f'{pid}.meta.json').write_text(json.dumps(meta)); return d


def test_pkg_doctor_detects_each_failure(tmp_path):
    make_pkg(tmp_path, 'good', {'a.txt': '1'})
    d = make_pkg(tmp_path, 'drift', {'a.txt': '1'}); (d / 'a.txt').write_text('changed')
    (tmp_path / 'orphan').mkdir()
    (tmp_path / '.meta' / 'ghost.meta.json').write_text('{}')
    m = json.loads((tmp_path / '.meta/good.meta.json').read_text())
    make_pkg(tmp_path, 'leak', {'a.txt': '1'}, source={'kind': 'git', 'locator': 'https://u:pw@host/x.git'})
    make_pkg(tmp_path, 'badexp', {'a.txt': '1'}, exports=[{'id': 'x', 'kind': 'asset', 'path': '../etc'}])
    st = {r['package_id']: r for r in pkgs.doctor(str(tmp_path))['reports']}
    assert st['good']['status'] == 'ok'
    codes = lambda k: {x['code'] for x in st[k]['diagnostics']}
    assert 'DIGEST_MISMATCH' in codes('drift') and st['orphan']['status'] == 'unknown'
    assert st['ghost']['status'] == 'orphan_meta' and 'CREDENTIAL_LEAK' in codes('leak')
    assert 'PATH_TRAVERSAL' in codes('badexp')


def test_pkg_digest_symlink_policy(tmp_path):
    # 現行規則(pkgs.content_digest):套件內部的連結視為內容並跟隨;逃出套件、懸空者拒絕。
    d = make_pkg(tmp_path, 'p', {'a.txt': '1'}); os.symlink('a.txt', d / 'inner')
    digest, ds = pkgs.content_digest(str(d))
    assert digest is not None and ds == []
    (tmp_path / 'outside.txt').write_text('x')
    os.symlink('../outside.txt', d / 'escape')
    digest, ds = pkgs.content_digest(str(d))
    assert digest is None and [x['code'] for x in ds] == ['SYMLINK_ESCAPES_PACKAGE']
    os.unlink(d / 'escape'); os.symlink('missing', d / 'dangling')
    digest, ds = pkgs.content_digest(str(d))
    assert digest is None and [x['code'] for x in ds] == ['SYMLINK_DANGLING']


def git(cwd, *a):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_override_check_safe_redundant_conflict(tmp_path):
    up = tmp_path / 'up'; up.mkdir(); git(up, 'init', '-q')
    for n, t in (('keep.txt', 'base-k'), ('same.txt', 'base-s'), ('clash.txt', 'base-c')):
        (up / n).write_text(t)
    git(up, 'add', '.'); git(up, 'commit', '-qm', 'base'); base = git(up, 'rev-parse', 'HEAD')
    (up / 'same.txt').write_text('mine-s'); (up / 'clash.txt').write_text('theirs-c'); (up / 'extra.txt').write_text('x')
    git(up, 'add', '.'); git(up, 'commit', '-qm', 'cand'); cand = git(up, 'rev-parse', 'HEAD')
    sha = lambda t: hashlib.sha256(t.encode()).hexdigest()
    ov = tmp_path / 'ov'; ov.mkdir(); (ov / 'k').write_text('k'); digest, _ = pkgs.content_digest(str(ov))
    meta = {'schema_version': 1, 'package_id': 'ov', 'source_type': 'override_patch', 'source': {'kind': 'directory', 'locator': 'local://ov'},
            'target_upstream': 'up', 'base_commit': base, 'content_digest': digest, 'overridden_files': [
                {'path': 'keep.txt', 'base_sha256': sha('base-k'), 'override_sha256': sha('mine-k'), 'mode': '0644'},
                {'path': 'same.txt', 'base_sha256': sha('base-s'), 'override_sha256': sha('mine-s'), 'mode': '0644'},
                {'path': 'clash.txt', 'base_sha256': sha('base-c'), 'override_sha256': sha('mine-c'), 'mode': '0644'}]}
    (tmp_path / '.meta').mkdir(); (tmp_path / '.meta/ov.meta.json').write_text(json.dumps(meta))
    (tmp_path / '.meta/up.meta.json').write_text('{}')
    r = pkgs.override_check(str(tmp_path), 'ov', cand)
    got = {f['path']: f['status'] for f in r['files']}
    assert got == {'keep.txt': 'SAFE', 'same.txt': 'REDUNDANT', 'clash.txt': 'CONFLICT'} and r['status'] == 'CONFLICT'
    assert {f['path'] for f in r['info_files']} == {'extra.txt'}
    assert pkgs.override_check(str(tmp_path), 'ov', 'not-a-sha')['status'] == 'ERROR'


def test_where_finds_scope_and_includers(tmp_path):
    import json, subprocess, sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True); (repo / "skill").mkdir()
    sd = tmp_path / "scopes"; sd.mkdir()
    item = {"src": str(repo / "skill"), "dst_fhs": "/opt/ext/fhs/skills/x", "mode": "ro", "type": "copy_inject"}
    base = {"enabled": True, "exclude_scopes": [], "exclude_items": []}
    (sd / "biz-x.extscope.json").write_text(json.dumps({**base, "uuid": "11111111-1111-4111-8111-111111111111", "name": "biz-x", "category": "biz", "include_scopes": [], "include_items": [item]}))
    (sd / "solution-y.extscope.json").write_text(json.dumps({**base, "uuid": "22222222-2222-4222-8222-222222222222", "name": "solution-y", "category": "solution", "include_scopes": ["biz-x"], "include_items": []}))
    r = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,%r);from extscope.cli import main;raise SystemExit(main(sys.argv[1:]))" % str(root / "src"),
                        "where", "--scope-dir", str(sd), str(repo / "skill" / "SKILL.md")], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["matches"][0]["scope_name"] == "biz-x"
    assert out["matches"][0]["included_by"] == ["solution-y"] and out["matches"][0]["repo_root"] == str(repo)
