"""Explicit plugin loading, actual payload pinning and target/dependency validation."""
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

STAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STAGE/'src'))
from extscope import __version__
from extscope.binding import SourceRoots,canonical
from extscope.digest import hash_tree
from extscope.plugin_api import PluginContext
from extscope import plugins


@pytest.fixture(autouse=True)
def fresh_namespace():
    yield
    for key in list(sys.modules):
        if key.startswith(('fixture_plugin','fixture_other')):sys.modules.pop(key,None)
    importlib.invalidate_caches()


def source_tree(tmp_path):
    root=tmp_path/'generation';root.mkdir();skills=root/'skills';skills.mkdir();asset=skills/'sample';asset.mkdir()
    (asset/'SKILL.md').write_text('---\nname: sample\ndescription: fixture\n---\nBody')
    return root


def provider(tmp_path,monkeypatch,*,namespace='fixture_plugin',identifier='fixture',behavior='normal'):
    root=tmp_path/namespace;root.mkdir();(root/'__init__.py').write_text('')
    mutation=''
    if behavior=='mutate':mutation="        Path(config['source']).joinpath('auxiliary').write_text('changed during planning')\n"
    target="context.target_roots[0]+'/skill'"
    if behavior=='outside-target':target="str(Path(context.target_roots[0]).parent/'outside')"
    source="config['source']"
    if behavior=='outside-source':source="config['outside']"
    marker=tmp_path/(namespace+'-imported')
    prefix=f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n" if behavior=='trap' else 'from pathlib import Path\n'
    code=prefix+f'''import os,stat
from extscope.digest import hash_tree_fd
from extscope.plugin_api import PluginPlan,TargetOperation,SourceRequirement
class Plugin:
    api_version={plugins.API_VERSION}
    schema_version={plugins.SCHEMA_VERSION}
    def plan(self,config,context):
'''+mutation+f'''        source={source}
        fd=context.sources.open_source(source)
        try:
            digest=hash_tree_fd(fd);mode=stat.S_IMODE(os.fstat(fd).st_mode)
        finally:os.close(fd)
        operation=TargetOperation('symlink',source,{target},digest,'directory',mode,'extscope.tree.v1')
        dependencies=()
        if 'expected' in config:
            dependencies=(SourceRequirement(source,'directory','extscope.tree.v1',config['expected']),)
        return PluginPlan((operation,),dependencies)
'''
    (root/'plugin.py').write_text(code)
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    record={'id':identifier,'distribution':identifier+'-distribution','version':'1.0.0','api_version':plugins.API_VERSION,'schema_version':plugins.SCHEMA_VERSION,
            'namespace':namespace,'entry_point':namespace+'.plugin:Plugin','files':files,
            'payload_sha256':hashlib.sha256(canonical(files)).hexdigest(),'config':{}}
    dist=SimpleNamespace(version='1.0.0',metadata={'Name':record['distribution']},locate_file=lambda name:tmp_path/name)
    entry=SimpleNamespace(name=identifier,value=record['entry_point'],dist=dist,
                          load=lambda: getattr(importlib.import_module(namespace+'.plugin'),'Plugin'))
    monkeypatch.syspath_prepend(str(tmp_path))
    return record,entry,root,marker


def consumer(*records):
    return {'schema':'extscope.consumer.v1','core_version':__version__,'plugins':list(records)}


def launch(monkeypatch,record,entry,source,target):
    monkeypatch.setattr(plugins.metadata,'entry_points',lambda **kw:[entry])
    record['config']={'source':str(source)}
    return plugins.plan_plugins(consumer(record),source_roots=[source],target_roots=[target])


def test_disabled_never_discovers_or_loads(monkeypatch):
    def forbidden(**kwargs):raise AssertionError('plugin discovery when disabled')
    monkeypatch.setattr(plugins.metadata,'entry_points',forbidden)
    result=plugins.plan_plugins({'schema':'extscope.consumer.v1','plugins':[]})
    assert result['status']=='disabled' and result['operations']==[] and result['execution_authorized'] is False


def test_correct_pin_loads_and_proposes_without_writing_target(tmp_path,monkeypatch):
    source=source_tree(tmp_path);target=tmp_path/'target'
    record,entry,root,marker=provider(tmp_path,monkeypatch)
    output=launch(monkeypatch,record,entry,source,target)
    assert len(output['operations'])==1 and not target.exists()
    assert output['operations'][0]['target']==str(target/'skill')
    assert not (root/'__pycache__').exists()
    assert not output['execution_authorized']


@pytest.mark.parametrize('change',['version','distribution','api','schema','digest','files','entry-point','extra-key'])
def test_invalid_pins_never_import_plugin(tmp_path,monkeypatch,change):
    source=source_tree(tmp_path);record,entry,root,marker=provider(tmp_path,monkeypatch,behavior='trap')
    if change=='version':record['version']='2.0.0'
    elif change=='distribution':record['distribution']='wrong'
    elif change=='api':record['api_version']=999
    elif change=='schema':record['schema_version']=True
    elif change=='digest':record['payload_sha256']='0'*64
    elif change=='files':record['files']['plugin.py']='0'*64;record['payload_sha256']=hashlib.sha256(canonical(record['files'])).hexdigest()
    elif change=='entry-point':record['entry_point']='fixture_plugin.plugin:Missing'
    else:record['enabled']=True
    with pytest.raises(ValueError):launch(monkeypatch,record,entry,source,tmp_path/'target')
    assert not marker.exists()


@pytest.mark.parametrize('change',['modified','extra','missing','symlink','bytecode'])
def test_actual_files_are_verified_before_import(tmp_path,monkeypatch,change):
    source=source_tree(tmp_path);record,entry,root,marker=provider(tmp_path,monkeypatch,behavior='trap')
    if change=='modified':(root/'plugin.py').write_text('raise AssertionError("must not execute")')
    elif change=='extra':(root/'extra.py').write_text('')
    elif change=='missing':(root/'plugin.py').unlink()
    elif change=='symlink':(root/'plugin.py').unlink();(root/'plugin.py').symlink_to(source/'skills/sample/SKILL.md')
    else:(root/'__pycache__').mkdir();(root/'__pycache__/module.pyc').write_bytes(b'bytecode')
    with pytest.raises((ValueError,OSError)):launch(monkeypatch,record,entry,source,tmp_path/'target')
    assert not marker.exists()


@pytest.mark.parametrize('change',['unknown','ambiguous','duplicate','cached','shadowed'])
def test_selection_and_namespace_ambiguity_fail_closed(tmp_path,monkeypatch,change):
    source=source_tree(tmp_path);record,entry,root,marker=provider(tmp_path,monkeypatch,behavior='trap')
    record['config']={'source':str(source)};entries=[entry];records=[record]
    if change=='unknown':entries=[]
    elif change=='ambiguous':entries=[entry,entry]
    elif change=='duplicate':records=[record,copy.deepcopy(record)]
    elif change=='cached':sys.modules['fixture_plugin']=SimpleNamespace(__file__=str(root/'__init__.py'))
    else:monkeypatch.setattr(plugins.util,'find_spec',lambda name:SimpleNamespace(origin='/wrong/module.py'))
    monkeypatch.setattr(plugins.metadata,'entry_points',lambda **kw:entries)
    with pytest.raises(ValueError):plugins.plan_plugins(consumer(*records),source_roots=[source],target_roots=[tmp_path/'target'])
    assert not marker.exists()


def test_every_pin_is_checked_before_any_selected_module_load(tmp_path,monkeypatch):
    source=source_tree(tmp_path);a,e1,_,marker=provider(tmp_path,monkeypatch,behavior='trap')
    b,e2,_,_=provider(tmp_path,monkeypatch,namespace='fixture_other',identifier='other')
    a['config']=b['config']={'source':str(source)};b['version']='invalid'
    monkeypatch.setattr(plugins.metadata,'entry_points',lambda **kw:[e1,e2])
    with pytest.raises(ValueError):plugins.plan_plugins(consumer(a,b),source_roots=[source],target_roots=[tmp_path/'target'])
    assert not marker.exists()


@pytest.mark.parametrize('behavior',['outside-source','outside-target'])
def test_output_cannot_expand_grants(tmp_path,monkeypatch,behavior):
    source=source_tree(tmp_path);outside=tmp_path/'outside';outside.mkdir()
    record,entry,_,_=provider(tmp_path,monkeypatch,behavior=behavior)
    record['config']={'source':str(source),'outside':str(outside)}
    monkeypatch.setattr(plugins.metadata,'entry_points',lambda **kw:[entry])
    with pytest.raises(ValueError):plugins.plan_plugins(consumer(record),source_roots=[source],target_roots=[tmp_path/'target'])


def test_cross_plugin_target_conflict_is_rejected(tmp_path,monkeypatch):
    source=source_tree(tmp_path);a,e1,_,_=provider(tmp_path,monkeypatch)
    b,e2,_,_=provider(tmp_path,monkeypatch,namespace='fixture_other',identifier='other')
    a['config']=b['config']={'source':str(source)}
    monkeypatch.setattr(plugins.metadata,'entry_points',lambda **kw:[e1,e2])
    with pytest.raises(ValueError):plugins.plan_plugins(consumer(a,b),source_roots=[source],target_roots=[tmp_path/'target'])


def test_complete_generation_dependency_is_revalidated_after_planning(tmp_path,monkeypatch):
    source=source_tree(tmp_path);expected=hash_tree(source)
    record,entry,_,_=provider(tmp_path,monkeypatch,behavior='mutate')
    record['config']={'source':str(source),'expected':expected}
    monkeypatch.setattr(plugins.metadata,'entry_points',lambda **kw:[entry])
    with pytest.raises(ValueError):plugins.plan_plugins(consumer(record),source_roots=[source],target_roots=[tmp_path/'target'])


def adapter_module():
    path=STAGE/'plugins/agent-harness-adapter/src/agent_harness_adapter/plugin.py'
    spec=importlib.util.spec_from_file_location('fixture_adapter_function',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def adapter_config(source,target):
    return {'canonical_generation':str(source),'canonical_sha256':hash_tree(source),
            'harness':'codex-host','target_home':str(target),'asset_kinds':['skills']}


def test_actual_adapter_proposes_typed_skills_and_generation_requirement(tmp_path):
    source=source_tree(tmp_path);target=tmp_path/'profile';config=adapter_config(source,target)
    with SourceRoots([source]) as grants:
        plan=adapter_module().Plugin().plan(config,PluginContext(grants,(str(target),)))
    assert len(plan.operations)==1 and len(plan.dependencies)==1
    assert plan.operations[0].target==str(target/'skills/sample') and not target.exists()
    assert plan.dependencies[0].sha256==config['canonical_sha256']


@pytest.mark.parametrize('change',['hash','unsupported-kind','harness','generic-agents','missing-skill','empty','skill-link'])
def test_adapter_rejects_incomplete_or_unsupported_inputs(tmp_path,change):
    source=source_tree(tmp_path);target=tmp_path/'profile';config=adapter_config(source,target)
    if change=='hash':config['canonical_sha256']='0'*64
    elif change=='unsupported-kind':config['asset_kinds']=['skills','mcp']
    elif change=='harness':config['harness']='unknown'
    elif change=='generic-agents':config['target_home']=str(tmp_path/'.agents')
    elif change=='missing-skill':(source/'skills/sample/SKILL.md').unlink();config['canonical_sha256']=hash_tree(source)
    elif change=='empty':(source/'skills/sample/SKILL.md').unlink();(source/'skills/sample').rmdir();config['canonical_sha256']=hash_tree(source)
    else:
        (source/'skills/alias').symlink_to(source/'skills/sample',target_is_directory=True);config['canonical_sha256']=hash_tree(source)
    with SourceRoots([source]) as grants:
        with pytest.raises((ValueError,OSError)):adapter_module().Plugin().plan(config,PluginContext(grants,(str(target),)))


def test_adapter_declares_its_own_supported_api_not_the_current_core_value(monkeypatch):
    import extscope.plugin_api as api
    monkeypatch.setattr(api,'API_VERSION',999);monkeypatch.setattr(api,'SCHEMA_VERSION',999)
    adapter=adapter_module().Plugin()
    assert adapter.api_version==1 and adapter.schema_version==1


def test_provider_failure_does_not_echo_provider_data_and_restores_global_flag(tmp_path,monkeypatch):
    source=source_tree(tmp_path);record,entry,_,_=provider(tmp_path,monkeypatch)
    def failed():raise RuntimeError('sensitive-fixture-provider-data')
    entry.load=failed;before=sys.dont_write_bytecode
    with pytest.raises(ValueError) as error:launch(monkeypatch,record,entry,source,tmp_path/'target')
    assert 'sensitive-fixture-provider-data' not in str(error.value)
    assert sys.dont_write_bytecode==before
