"""Approved safety regressions; fixtures only, no real launcher or home mutation."""
from pathlib import Path
import importlib,json,os,stat,sys,subprocess
import pytest
PKG=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PKG/'adapters'))
ph=importlib.import_module('project-harness')
@pytest.mark.parametrize('raw',['{bad','[]','', '{"mcpServers":[]}'])
def test_bad_existing_json_preserved(tmp_path,raw):
    target=tmp_path/'existing.json';target.write_text(raw);target.chmod(0o640)
    with pytest.raises(ValueError):ph._merge_json_mcp(target,{'fixture':{'command':'fixture'}})
    assert target.read_text()==raw and stat.S_IMODE(target.stat().st_mode)==0o640


def test_json_merge_atomic_and_idempotent(tmp_path):
    target=tmp_path/'config.json';target.write_text('{"other":1,"mcpServers":{"old":{"command":"old"}}}');target.chmod(0o640)
    assert ph._merge_json_mcp(target,{'new':{'command':'new'}})
    doc=json.loads(target.read_text());assert doc['other']==1 and set(doc['mcpServers'])=={'old','new'}
    before=target.stat().st_mtime_ns
    assert not ph._merge_json_mcp(target,{'new':{'command':'new'}})
    assert target.stat().st_mtime_ns==before and stat.S_IMODE(target.stat().st_mode)==0o640


def test_json_replace_failure_preserves_original(tmp_path,monkeypatch):
    p=tmp_path/'config.json';p.write_text('{"other":1}');before=p.read_bytes()
    monkeypatch.setattr(ph.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(OSError):ph._merge_json_mcp(p,{'new':{'command':'new'}})
    assert p.read_bytes()==before and list(tmp_path.iterdir())==[p]


def test_json_symlink_never_followed(tmp_path):
    target=tmp_path/'real';target.write_text('{"other":1}');link=tmp_path/'alias';link.symlink_to(target)
    with pytest.raises(ValueError):ph._merge_json_mcp(link,{'new':{'command':'new'}})
    assert target.read_text()=='{"other":1}'


def args(tmp_path,harness='good'):
    canonical=tmp_path/'canonical';canonical.mkdir();skill=canonical/'skills/demo';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('# fixture')
    manifests=tmp_path/'manifests';manifests.mkdir()
    manifest={'harness_id':'good','projections':{'skills':{'mode':'symlink_children','target_dir':'~/skills'}}}
    (manifests/'harness-good.json').write_text(json.dumps(manifest))
    return ['project-harness','--harness',harness,'--canonical-root',str(canonical),'--manifests-dir',str(manifests),'--workspace',str(tmp_path/'workspace'),'--user-home',str(tmp_path/'home')]


@pytest.mark.parametrize('harness',['unknown','good,unknown','','good,good','../escape'])
def test_invalid_harness_preflight_before_home_writes(tmp_path,monkeypatch,harness):
    argv=args(tmp_path,harness);monkeypatch.setattr(sys,'argv',argv)
    assert ph.main()!=0 and not (tmp_path/'home').exists()


def test_missing_canonical_before_home_writes(tmp_path,monkeypatch):
    argv=args(tmp_path);argv[argv.index('--canonical-root')+1]=str(tmp_path/'missing')
    monkeypatch.setattr(sys,'argv',argv);assert ph.main()!=0 and not (tmp_path/'home').exists()


def test_corrupt_second_harness_target_blocks_first(tmp_path,monkeypatch):
    argv=args(tmp_path,'good,bad');target=tmp_path/'home/bad.json';target.parent.mkdir();target.write_text('{broken')
    manifests=tmp_path/'manifests';(manifests/'harness-bad.json').write_text(json.dumps({'projections':{'mcp':{'mode':'json_merge','target_file':'~/bad.json'}}}))
    mcp=tmp_path/'canonical/mcp';mcp.mkdir();(mcp/'fixture.json').write_text('{"fixture":{"command":"fixture"}}')
    monkeypatch.setattr(sys,'argv',argv);assert ph.main()!=0
    assert not (tmp_path/'home/skills').exists() and target.read_text()=='{broken'


def test_required_missing_category_rejected(tmp_path,monkeypatch):
    argv=args(tmp_path);p=tmp_path/'manifests/harness-good.json';d=json.loads(p.read_text());d['required_assets']=['mcp'];p.write_text(json.dumps(d))
    monkeypatch.setattr(sys,'argv',argv);assert ph.main()!=0 and not (tmp_path/'home').exists()


def test_manifest_paths_must_be_relative_to_home_or_project(tmp_path,monkeypatch):
    for bad in ('/root/skills','/workspace/x','/var/lib/x','skills','~/../x','./a/../b'):
        sub=tmp_path/str(abs(hash(bad)));sub.mkdir()
        argv=args(sub);p=sub/'manifests/harness-good.json';d=json.loads(p.read_text())
        d['projections']['skills']['target_dir']=bad;p.write_text(json.dumps(d))
        monkeypatch.setattr(sys,'argv',argv);assert ph.main()==2,bad
    assert ph.expand_path('~/a',Path('/p'),Path('/h'))==Path('/h/a') and ph.expand_path('./',Path('/p'),Path('/h'))==Path('/p')


def test_workspace_and_home_are_required_no_defaults(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'argv',['x','--harness','good','--canonical-root',str(tmp_path)])
    with pytest.raises(SystemExit):ph.main()


def test_manifests_carry_no_host_path():
    for f in (PKG/'manifests').glob('*.json'):
        text=f.read_text()
        for needle in ('/root','/workspace','/home','/var/','/opt','/srv','/run'):
            assert needle not in text,(f.name,needle)


def test_json_fsync_failure_before_commit_preserves_original(tmp_path,monkeypatch):
    p=tmp_path/'config';p.write_text('{"keep":1}');before=p.read_bytes()
    monkeypatch.setattr(ph.os,'fsync',lambda *a:(_ for _ in ()).throw(OSError('injected fsync')))
    with pytest.raises(OSError):ph._merge_json_mcp(p,{'fixture':{'command':'fixture'}})
    assert p.read_bytes()==before and list(tmp_path.iterdir())==[p]


def test_canonical_json_loader_rejects_bad_input(tmp_path):
    (tmp_path/'bad.json').write_text('{broken')
    with pytest.raises(ValueError):ph._load_canonical_mcp_servers(tmp_path,tmp_path,tmp_path)


def test_json_fifo_rejected_without_blocking(tmp_path):
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(ValueError):ph._merge_json_mcp(fifo,{'fixture':{'command':'fixture'}})
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_new_target_race_preserves_other_writer(tmp_path,monkeypatch):
    p=tmp_path/'config';original=ph.os.link
    def race(src,dst):
        p.write_text('{"concurrent":1}')
        return original(src,dst)
    monkeypatch.setattr(ph.os,'link',race)
    with pytest.raises(FileExistsError):ph._merge_json_mcp(p,{'fixture':{'command':'fixture'}})
    assert p.read_text()=='{"concurrent":1}' and list(tmp_path.iterdir())==[p]


def test_existing_target_change_rejected_before_replace(tmp_path,monkeypatch):
    p=tmp_path/'config';p.write_text('{"keep":1}');original=ph.os.fsync
    calls=[]
    def race(fd):
        original(fd)
        if not calls:
            calls.append(1);p.write_text('{"changed_by_other_writer":true}')
    monkeypatch.setattr(ph.os,'fsync',race)
    with pytest.raises(ValueError):ph._merge_json_mcp(p,{'fixture':{'command':'fixture'}})
    assert p.read_text()=='{"changed_by_other_writer":true}'


