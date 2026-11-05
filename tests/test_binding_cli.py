"""Real fd confinement, content verification, CLI statuses and cleanup behavior."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STAGE/'src'))
from extscope.binding import SourceRoots, BindingMismatch, _open_beneath, bind_sources, verify_sources
from extscope.declaration import parse_document
from extscope.digest import hash_file_fd, hash_tree, hash_tree_fd
from extscope.plan import flatten
from extscope.resolver import ScopeIndex
from extscope.cli import main


def scope_data(paths):
    return {'uuid':'11111111-1111-4111-8111-111111111111','name':'fixture', 'enabled':True,
            'category':'biz','include_items':[{'src':str(p),'dst_fhs':'/delivery/'+str(i),
            'type':'copy_inject','mode':'ro'} for i,p in enumerate(paths)]}


def plan_for(paths, **kwargs):
    return flatten('fixture',ScopeIndex([parse_document(json.dumps(scope_data(paths)))]),**kwargs)


def test_fd_digest_preserves_ownership_and_file_offset(tmp_path):
    p=tmp_path/'file';p.write_bytes(b'full file bytes')
    fd=os.open(p,os.O_RDONLY)
    try:
        os.lseek(fd,4,os.SEEK_SET)
        assert hash_file_fd(fd)==hashlib.sha256(p.read_bytes()).hexdigest()
        assert os.lseek(fd,0,os.SEEK_CUR)==4
        assert os.fstat(fd).st_ino==p.stat().st_ino
    finally:os.close(fd)
    directory=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        assert hash_tree_fd(directory)==hash_tree(tmp_path)
        assert hash_tree_fd(directory)==hash_tree(tmp_path)
        assert stat.S_ISDIR(os.fstat(directory).st_mode)
        with pytest.raises(ValueError):hash_file_fd(directory)
    finally:os.close(directory)


def test_binding_records_real_bytes_modes_and_canonical_grants(tmp_path):
    root=tmp_path/'allowed';root.mkdir();f=root/'file';f.write_bytes(b'data');f.chmod(0o640)
    tree=root/'tree';tree.mkdir();(tree/'.meta.json').write_text('{"content_digest":"claimed"}')
    plan=plan_for([f,tree,f]);receipt=bind_sources(plan,allowed_roots=[root])
    assert len(receipt['sources'])==2
    file=next(x for x in receipt['sources'] if x['kind']=='file')
    assert file['sha256']==hashlib.sha256(b'data').hexdigest() and file['mode']==0o640
    assert verify_sources(plan,receipt,allowed_roots=[root])==receipt
    unsigned={k:v for k,v in receipt.items() if k!='binding_sha256'}
    expected=hashlib.sha256(json.dumps(unsigned,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('ascii')).hexdigest()
    assert receipt['binding_sha256']==expected


@pytest.mark.parametrize('change',['payload','mode','metadata','plan-binding','grant','receipt','extra-key','algorithm'])
def test_changes_reject_receipt_reuse(tmp_path,change):
    root=tmp_path/'allowed';root.mkdir();f=root/'file';f.write_text('first')
    (root/'.meta.json').write_text('{"content_digest":"'+'0'*64+'"}')
    plan=plan_for([root]);receipt=bind_sources(plan,allowed_roots=[root]);grants=[root]
    if change=='payload':f.write_text('other')
    elif change=='mode':f.chmod(f.stat().st_mode ^ 0o100)
    elif change=='metadata':(root/'.meta.json').write_text('{}')
    elif change=='plan-binding':plan=plan_for([root],variables={'CONTEXT':'changed'})
    elif change=='grant':other=tmp_path/'other';other.mkdir();grants.append(other)
    elif change=='receipt':receipt['binding_sha256']='0'*64
    elif change=='extra-key':receipt['unexpected']=True
    else:receipt['sources'][0]['algorithm']='legacy'
    with pytest.raises(BindingMismatch):verify_sources(plan,receipt,allowed_roots=grants)


def test_mtime_only_does_not_break_content_receipt(tmp_path):
    f=tmp_path/'file';f.write_text('same');plan=plan_for([f]);receipt=bind_sources(plan,allowed_roots=[tmp_path])
    os.utime(f,ns=(10_000_000_000,20_000_000_000))
    assert verify_sources(plan,receipt,allowed_roots=[tmp_path])==receipt


@pytest.mark.parametrize('case',['sibling-prefix','leaf-link','middle-link','root-link','relative-source','root-file','fifo'])
def test_path_confinement_rejects_aliases_and_special_files(tmp_path,case):
    root=tmp_path/'allowed';root.mkdir();other=tmp_path/'allowed-other';other.mkdir()
    (other/'secret').write_text('outside source')
    f=root/'file';f.write_text('allowed');src=f;grants=[root]
    if case=='sibling-prefix':src=other/'secret'
    elif case=='leaf-link':src=root/'link';src.symlink_to(other/'secret')
    elif case=='middle-link':(root/'link').symlink_to(other,target_is_directory=True);src=root/'link/secret'
    elif case=='root-link':link=tmp_path/'alias';link.symlink_to(root,target_is_directory=True);grants=[link]
    elif case=='relative-source':
        with SourceRoots([root]) as roots:
            with pytest.raises(ValueError):roots.open_source('relative')
        return
    elif case=='root-file':grants=[f]
    else:src=root/'pipe';os.mkfifo(src)
    with pytest.raises((ValueError,OSError)):bind_sources(plan_for([src]),allowed_roots=grants)


@pytest.mark.parametrize('roots',[[],['/'],['relative'],None])
def test_grants_are_never_implicit(roots):
    with pytest.raises((ValueError,TypeError)):SourceRoots(roots)


@pytest.mark.parametrize('parts',[('',),('.',),('..',),('a/b',),('\x00',),(1,)])
def test_bad_components_fail_before_open_and_do_not_close_root(tmp_path,parts):
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):_open_beneath(fd,parts)
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
    finally:os.close(fd)


def test_open_root_copy_and_failed_walk_never_leak_fd(tmp_path):
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        before=len(os.listdir('/proc/self/fd'))
        copy=_open_beneath(fd,());assert copy!=fd;os.close(copy)
        for _ in range(25):
            with pytest.raises(OSError):_open_beneath(fd,('missing','leaf'))
        assert len(os.listdir('/proc/self/fd'))==before
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
    finally:os.close(fd)


def test_grant_cleanup_closes_all_fds_even_when_a_close_reports_error(tmp_path,monkeypatch):
    import extscope.binding as binding
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir();grants=SourceRoots([a,b]);grants.__enter__()
    fds=list(grants._fds.values());original=os.close;failed=False
    def close(fd):
        nonlocal failed
        original(fd)
        if not failed:failed=True;raise OSError('injected close report')
    with monkeypatch.context() as guard:
        guard.setattr(binding.os,'close',close)
        with pytest.raises(OSError):grants.__exit__(None,None,None)
    for fd in fds:
        with pytest.raises(OSError):os.fstat(fd)
    assert not grants._fds


def test_repeated_binding_failure_does_not_leak_roots(tmp_path):
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'outside';outside.write_text('outside')
    plan=plan_for([outside]);before=len(os.listdir('/proc/self/fd'))
    for _ in range(20):
        with pytest.raises(ValueError):bind_sources(plan,allowed_roots=[root])
    assert len(os.listdir('/proc/self/fd'))==before


def test_cli_real_bind_verify_and_payload_mismatch(tmp_path,capsys):
    source=tmp_path/'source';source.mkdir();f=source/'payload';f.write_text('first')
    scope=tmp_path/'scope.json';scope.write_text(json.dumps(scope_data([f])))
    common=['--scope',str(scope),'--entry','fixture','--allow-root',str(source)]
    assert main(['bind',*common])==0
    receipt=tmp_path/'receipt.json';receipt.write_text(capsys.readouterr().out)
    assert main(['verify',*common,'--receipt',str(receipt)])==0
    assert json.loads(capsys.readouterr().out)['status']=='matched'
    f.write_text('other')
    assert main(['verify',*common,'--receipt',str(receipt)])==3
    output=capsys.readouterr();assert output.out=='' and json.loads(output.err)['error']=='binding_mismatch'


def test_cli_plan_validate_and_digest_are_actual_readonly_operations(tmp_path,capsys):
    f=tmp_path/'file';f.write_text('bytes');scope=tmp_path/'scope.json';scope.write_text(json.dumps(scope_data([f])))
    assert main(['validate','--scope',str(scope)])==0
    assert json.loads(capsys.readouterr().out)['execution_authorized'] is False
    assert main(['plan','--scope',str(scope),'--entry','fixture'])==0
    assert len(json.loads(capsys.readouterr().out)['items'])==1
    assert main(['digest',str(tmp_path)])==0
    assert json.loads(capsys.readouterr().out)['algorithm']=='extscope.tree.v1'


@pytest.mark.parametrize('case',['duplicate-key','invalid-json','oversize','symlink'])
def test_cli_bad_receipts_fail_nonzero_without_echoing_content(tmp_path,capsys,case):
    f=tmp_path/'file';f.write_text('content');scope=tmp_path/'scope.json';scope.write_text(json.dumps(scope_data([f])))
    receipt=tmp_path/'receipt';receipt.write_text('{"marker":"sensitive-fixture", "marker":"duplicate"}')
    if case=='invalid-json':receipt.write_text('{invalid sensitive-fixture')
    elif case=='oversize':receipt.write_bytes(b'x'*(4*1024*1024+1))
    elif case=='symlink':link=tmp_path/'link';link.symlink_to(receipt);receipt=link
    assert main(['verify','--scope',str(scope),'--entry','fixture','--allow-root',str(tmp_path),'--receipt',str(receipt)])==2
    output=capsys.readouterr();assert output.out=='' and 'sensitive-fixture' not in output.err


def test_cli_rejects_duplicate_bindings_and_unknown_execution_command(tmp_path,capsys):
    f=tmp_path/'file';f.write_text('bytes');scope=tmp_path/'scope.json';scope.write_text(json.dumps(scope_data([f])))
    assert main(['plan','--scope',str(scope),'--entry','fixture','--var','A=one','--var','A=two'])==2
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:main(['apply'])
    assert error.value.code==2


def test_directory_swap_to_symlink_between_selection_and_open_is_rejected(tmp_path,monkeypatch):
    import extscope.binding as binding
    root=tmp_path/'allowed';root.mkdir();middle=root/'middle';middle.mkdir();(middle/'file').write_text('inside')
    outside=tmp_path/'outside';outside.mkdir();(outside/'file').write_text('outside')
    plan=plan_for([middle/'file']);original=os.open;changed=False
    def swapped(path,*args,**kwargs):
        nonlocal changed
        if os.fspath(path)=='middle' and not changed:
            changed=True;middle.rename(root/'retired');middle.symlink_to(outside,target_is_directory=True)
        return original(path,*args,**kwargs)
    before=len(os.listdir('/proc/self/fd'))
    with monkeypatch.context() as guard:
        guard.setattr(binding.os,'open',swapped)
        with pytest.raises(OSError):bind_sources(plan,allowed_roots=[root])
    assert changed and len(os.listdir('/proc/self/fd'))==before


def test_grant_setup_close_report_does_not_orphan_the_opened_root(tmp_path,monkeypatch):
    import extscope.binding as binding
    original_open=os.open;original_close=os.close;base=None;failed=False
    def opened(path,*args,**kwargs):
        nonlocal base
        fd=original_open(path,*args,**kwargs)
        if os.fspath(path)=='/' and 'dir_fd' not in kwargs:base=fd
        return fd
    def closed(fd):
        nonlocal failed
        original_close(fd)
        if fd==base and not failed:failed=True;raise OSError('injected bootstrap close report')
    before=len(os.listdir('/proc/self/fd'))
    with monkeypatch.context() as guard:
        guard.setattr(binding.os,'open',opened);guard.setattr(binding.os,'close',closed)
        with pytest.raises(OSError):SourceRoots([tmp_path]).__enter__()
    assert failed and len(os.listdir('/proc/self/fd'))==before


def test_file_digest_read_error_preserves_caller_fd_and_offset(tmp_path,monkeypatch):
    p=tmp_path/'file';p.write_text('bytes');fd=os.open(p,os.O_RDONLY)
    try:
        os.lseek(fd,2,os.SEEK_SET)
        def denied(*args,**kwargs):raise PermissionError('fixture read denied')
        with monkeypatch.context() as guard:
            guard.setattr(os,'read',denied)
            with pytest.raises(PermissionError):hash_file_fd(fd)
        assert os.lseek(fd,0,os.SEEK_CUR)==2 and os.fstat(fd).st_ino==p.stat().st_ino
    finally:os.close(fd)


def test_cli_plan_preview_redacts_environment_values_without_changing_core_plan(tmp_path,capsys):
    source=tmp_path/'file';source.write_text('bytes')
    data=scope_data([source]);data['include_items'][0]['pipeline']=[
        {'step':1,'module':'copy_inject','config':{'dst':'/delivery/asset'}},
        {'step':2,'module':'env_inject','config':{'key':'API_KEY','value':'sensitive-fixture-value'}}]
    scope=tmp_path/'scope.json';scope.write_text(json.dumps(data))
    core=flatten('fixture',ScopeIndex.from_files([scope]));expected=core.plan_sha256
    assert main(['plan','--scope',str(scope),'--entry','fixture'])==0
    output=capsys.readouterr().out;preview=json.loads(output)
    assert 'sensitive-fixture-value' not in output
    assert preview['schema']=='extscope.plan-preview.v1' and preview['plan_sha256']==expected
    assert preview['execution_authorized'] is False
    assert core.items[0].item['pipeline'][1]['config']['value']=='sensitive-fixture-value'
