"""Controller/runner crash evidence with fixtures, never vendor acceptance."""
import copy
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.models import AgentRequest, ExecutionPlan, PlanStep, ToolName
from engineering_agent.software_jobs_client import ReceiptStore
from engineering_agent.transactions import RunTransaction
from engineering_agent.worker_jobs import JobManager, TERMINAL
from engineering_agent.worker_protocol import WorkerError
from engineering_agent.workflows import WorkflowStore, DurableWorkflow

SCRIPT = '''import json,sys,time
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
p=r['parameters']
with open(p['marker'],'a') as f: f.write(r['request_id']+'\\n')
if p.get('gate'):
    while not Path(p['gate']).exists(): time.sleep(.025)
if p.get('fail'): sys.exit(7)
values={name:Path(name).read_text() for name in r['inputs']}
for name in r['outputs']:
    target=Path(name);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'fixture':True,'inputs':values,'request_id':r['request_id']}))
'''
COMMAND = [sys.executable, '{script}', '{request_file}']


@contextmanager
def worker_error(code):
    with pytest.raises(WorkerError) as caught:
        yield
    assert caught.value.code == code


def wait_for(fn, timeout=15):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        result=fn()
        if result: return result
        time.sleep(.025)
    raise AssertionError('Expected live process transition did not occur')


@pytest.fixture
def workflow_setup(tmp_path, monkeypatch):
    project=tmp_path/'project';project.mkdir()
    (project/'runner.py').write_text(SCRIPT)
    (project/'source.txt').write_text('original native input fixture')
    commands={'catia.create_or_update_model':COMMAND,'ansys.mesh_and_solve':COMMAND}
    manager=JobManager(tmp_path/'worker', commands, timeout=15)
    config={'generate_scripts':False,'timeouts':{'step_seconds':1},'tools':{
        'catia':{'bridge_url':'http://fixture-worker','poll_interval_seconds':.025,
                 'actions':{'create_or_update_model':{'script':'runner.py'}}},
        'ansys':{'bridge_url':'http://fixture-worker','poll_interval_seconds':.025,
                 'actions':{'mesh_and_solve':{'script':'runner.py'}}}}}
    plan=ExecutionPlan(AgentRequest('CATIA then Ansys fixture', str(project)),[
        PlanStep('geometry',ToolName.CATIA,'create_or_update_model','model',inputs=['source.txt'],outputs=['outputs/model.step'],parameters={'marker':str(tmp_path/'model-count')}),
        PlanStep('solve',ToolName.ANSYS,'mesh_and_solve','solve',depends_on=['geometry'],inputs=['outputs/model.step'],outputs=['outputs/results.json'],parameters={'marker':str(tmp_path/'solve-count'),'gate':str(tmp_path/'gate')})])
    def make_agent(cfg=None):
        agent=EngineeringAgent(cfg or config)
        monkeypatch.setattr(agent,'plan',lambda *a,**kw:copy.deepcopy(plan))
        for adapter in agent.adapters.values():
            monkeypatch.setattr(adapter,'_post',lambda payload,timeout:manager.handle(payload))
        return agent
    yield project,manager,config,plan,make_agent
    (tmp_path/'gate').touch()
    for job in manager.store.list(limit=100)['jobs']:
        if job['status'] not in TERMINAL: manager.store.cancel(job['request_id'])
    wait_for(lambda:all(j['status'] in TERMINAL for j in manager.store.list(limit=100)['jobs']))


def start(setup):
    project,manager,config,plan,make=setup
    report=make().run('fixture',project_dir=str(project),dry_run=False)
    for _ in range(10):
        assert report.status=='waiting',report.to_dict()
        state=WorkflowStore(project).read(report.workflow_id)
        if state['steps']['geometry']['status']=='completed' and state['steps']['solve'].get('request_id'):
            wait_for(lambda:(project.parent/'solve-count').is_file())
            return report
        report=DurableWorkflow(make(),project).resume(report.workflow_id)
    raise AssertionError('Fixture did not reach the intentionally gated solve')


def finish(setup,tmp_path,identifier):
    project,manager,config,plan,make=setup
    (tmp_path/'gate').touch()
    state=WorkflowStore(project).read(identifier)
    solve=state['steps']['solve']['request_id']
    wait_for(lambda:manager.store.status(solve)['status']=='completed')
    config['timeouts']['step_seconds']=10
    return make().workflow('resume',identifier,project_dir=str(project))


def test_resume_entire_plan_preserves_ids_and_uses_original_upstream_outputs(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    initial=WorkflowStore(project).read(report.workflow_id)
    assert initial['steps']['geometry']['status']=='completed'
    assert not (project/'outputs/model.step').exists()
    assert not (project/'outputs/results.json').exists()
    resumed=finish(workflow_setup,tmp_path,report.workflow_id)
    assert resumed['status']=='committed'
    result=json.loads((project/'outputs/results.json').read_text())
    geometry=json.loads(result['inputs']['outputs/model.step'])
    assert geometry['inputs']['source.txt']=='original native input fixture'
    assert geometry['request_id']==initial['steps']['geometry']['request_id']
    assert (tmp_path/'model-count').read_text().splitlines()==[geometry['request_id']]
    assert (tmp_path/'solve-count').read_text().splitlines()==[initial['steps']['solve']['request_id']]
    for entry in initial['steps'].values():
        receipt=ReceiptStore(project).read(entry['request_id'])
        assert receipt['collected'] and receipt['publication_run_id']==resumed['run_id']
    manifest=json.loads((project/'artifacts/runs'/resumed['run_id']/'changes.json').read_text())['files']
    assert f'.engineering-agent/workflows/{report.workflow_id}/state.json' in {m['path'] for m in manifest}
    again=make().workflow('resume',report.workflow_id,project_dir=str(project))
    assert again['run_id']==resumed['run_id']
    assert len(manager.store.list()['jobs'])==2


@pytest.mark.parametrize('change,code',[
    ('input','WORKFLOW_INPUT_CHANGED'),('script','WORKFLOW_INPUT_CHANGED'),
    ('output','PROJECT_OUTPUT_CHANGED'),('binding','WORKFLOW_BINDING_CHANGED'),
    ('cache','WORKFLOW_CACHE_CHANGED'),('request','WORKFLOW_CACHE_CHANGED')])
def test_resume_rejects_changed_project_configuration_or_cache(workflow_setup,tmp_path,change,code):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    store=WorkflowStore(project);state=store.read(report.workflow_id)
    if change=='input': (project/'source.txt').write_text('engineer edit')
    elif change=='script': (project/'runner.py').write_text('changed script')
    elif change=='output':
        (project/'outputs').mkdir();(project/'outputs/model.step').write_text('engineer edit')
    elif change=='binding': config['tools']['catia']['actions']['create_or_update_model']['vendor_options']={'changed':True}
    else:
        entry=state['steps']['geometry'];folder='responses' if change=='cache' else 'requests'
        (store.root(report.workflow_id)/folder/(entry['request_id']+'.json')).write_text('{}')
    (tmp_path/'gate').touch()
    with worker_error(code): make().workflow('resume',report.workflow_id,project_dir=str(project))
    assert len(manager.store.list()['jobs'])==2
    assert len((tmp_path/'model-count').read_text().splitlines())==1
    assert not (project/'outputs/results.json').exists()


def test_single_step_collect_cannot_publish_part_of_pending_workflow(workflow_setup):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    identifier=WorkflowStore(project).read(report.workflow_id)['steps']['geometry']['request_id']
    with worker_error('WORKFLOW_COLLECTION_REQUIRED'):
        make().software_job('catia','collect',identifier,project_dir=str(project))
    assert not (project/'outputs/model.step').exists()


def test_known_remote_job_disappearing_never_triggers_new_submit(workflow_setup,tmp_path,monkeypatch):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    agent=make();calls=[]
    def missing(payload,timeout):
        calls.append(payload['operation'])
        raise WorkerError('JOB_NOT_FOUND')
    monkeypatch.setattr(agent.adapters[ToolName.ANSYS],'_post',missing)
    with worker_error('REMOTE_JOB_HISTORY_LOST'):
        agent.workflow('resume',report.workflow_id,project_dir=str(project))
    assert calls==['job.status']
    assert len((tmp_path/'solve-count').read_text().splitlines())==1


def test_terminal_failure_is_not_reexecuted_and_downstream_never_starts(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    plan.steps[0].parameters['fail']=True
    config['timeouts']['step_seconds']=5
    report=make().run('fixture',project_dir=str(project),dry_run=False)
    assert report.status=='failed'
    again=make().workflow('resume',report.workflow_id,project_dir=str(project))
    assert again['status']=='failed'
    assert len((tmp_path/'model-count').read_text().splitlines())==1
    assert not (tmp_path/'solve-count').exists()


def test_publish_failure_restores_entire_group_and_resumes_cached_results(workflow_setup,tmp_path,monkeypatch):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    (tmp_path/'gate').touch()
    wait_for(lambda:all(j['status']=='completed' for j in manager.store.list()['jobs']))
    original=RunTransaction._install
    with monkeypatch.context() as patch:
        def fail(self,entry,target,side):
            if side=='after' and entry['path']=='outputs/results.json': raise OSError('publication fixture failure')
            return original(self,entry,target,side)
        patch.setattr(RunTransaction,'_install',fail)
        with pytest.raises(OSError,match='publication fixture'): make().workflow('resume',report.workflow_id,project_dir=str(project))
    assert not (project/'outputs/model.step').exists()
    assert not (project/'outputs/results.json').exists()
    assert all(not r['collected'] for r in ReceiptStore(project).list())
    result=finish(workflow_setup,tmp_path,report.workflow_id)
    assert result['status']=='committed'
    assert len((tmp_path/'model-count').read_text().splitlines())==1
    assert len((tmp_path/'solve-count').read_text().splitlines())==1


@pytest.mark.parametrize('mode',['running','between_steps','publication'])
def test_actual_controller_process_exit_resumes_without_duplicate_software_execution(workflow_setup,tmp_path,mode):
    project,manager,config,plan,make=workflow_setup
    config['timeouts']['step_seconds']=10
    if mode=='publication': (tmp_path/'gate').touch()
    data={'config':config,'plan':plan.to_dict(),'worker':str(manager.root),'mode':mode}
    data_path=tmp_path/'controller.json';data_path.write_text(json.dumps(data))
    code='''import json,os,sys
from pathlib import Path
from engineering_agent import EngineeringAgent
from engineering_agent.workflows import restore_plan
from engineering_agent.worker_jobs import JobManager
from engineering_agent.transactions import RunTransaction
r=json.loads(Path(sys.argv[1]).read_text())
a=EngineeringAgent(r['config']);plan=restore_plan(r['plan'])
m=JobManager(r['worker'],{'catia.create_or_update_model':[sys.executable,'{script}','{request_file}'],'ansys.mesh_and_solve':[sys.executable,'{script}','{request_file}']},timeout=15)
a.plan=lambda *args,**kw:plan
for adapter in a.adapters.values(): adapter._post=lambda payload,timeout:m.handle(payload)
def progress(event,payload):
    if r['mode']=='running' and event=='software_job' and payload.get('status')=='running': os._exit(73)
    if r['mode']=='between_steps' and event=='workflow_step': os._exit(73)
original=RunTransaction._install
def install(self,entry,target,side):
    original(self,entry,target,side)
    if r['mode']=='publication' and side=='after' and entry['path']=='outputs/results.json': os._exit(73)
RunTransaction._install=install
a.run('fixture',project_dir=plan.request.project_dir,dry_run=False,on_event=progress)
'''
    child=subprocess.run([sys.executable,'-c',code,str(data_path)],capture_output=True,text=True,timeout=30)
    assert child.returncode==73,child.stderr+child.stdout
    store=WorkflowStore(project);identifier=store.list()[0]['workflow_id']
    (tmp_path/'gate').touch()
    result=make().workflow('resume',identifier,project_dir=str(project))
    assert result['status']=='committed',result
    assert len((tmp_path/'model-count').read_text().splitlines())==1
    assert len((tmp_path/'solve-count').read_text().splitlines())==1
    assert len(manager.store.list()['jobs'])==2


def test_preflight_orders_dependencies_and_rejects_collision_before_any_job(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    plan.steps[1].outputs=list(plan.steps[0].outputs)
    plan.steps[1].inputs=[]
    with worker_error('WORKFLOW_OUTPUT_COLLISION'):
        make().run('fixture',project_dir=str(project),dry_run=False)
    assert not manager.store.list()['jobs']


def test_topological_execution_does_not_depend_on_list_order(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    plan.steps.reverse();(tmp_path/'gate').touch();config['timeouts']['step_seconds']=10
    report=make().run('fixture',project_dir=str(project),dry_run=False)
    assert report.status=='committed'
    assert [r.step_id for r in report.results]==['geometry','solve']


def test_local_step_uncertainty_is_not_reexecuted(workflow_setup,tmp_path,monkeypatch):
    project,manager,config,plan,make=workflow_setup
    config['tools']['catia'].pop('bridge_url')
    config['tools']['catia']['actions']['create_or_update_model']['command']=COMMAND
    import engineering_agent.workflows as workflows
    original=workflows.run_job
    with monkeypatch.context() as patch:
        def uncertain(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('controller lost local result')
        patch.setattr(workflows,'run_job',uncertain)
        with pytest.raises(RuntimeError,match='controller lost local result'):
            make().run('fixture',project_dir=str(project),dry_run=False)
    identifier=WorkflowStore(project).list()[0]['workflow_id']
    with worker_error('LOCAL_STEP_STATE_UNKNOWN'):
        make().workflow('resume',identifier,project_dir=str(project))
    assert len((tmp_path/'model-count').read_text().splitlines())==1
    assert not (tmp_path/'solve-count').exists()


def test_final_publication_checks_original_targets_again(workflow_setup,tmp_path,monkeypatch):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    (tmp_path/'gate').touch()
    wait_for(lambda:all(j['status']=='completed' for j in manager.store.list()['jobs']))
    agent=make();original=agent._stage_collected_jobs
    def external_edit(*args,**kwargs):
        output=project/'outputs/model.step';output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text('concurrent engineer edit')
        return original(*args,**kwargs)
    monkeypatch.setattr(agent,'_stage_collected_jobs',external_edit)
    with pytest.raises(RuntimeError,match='TRANSACTION_CONFLICT'):
        agent.workflow('resume',report.workflow_id,project_dir=str(project))
    assert (project/'outputs/model.step').read_text()=='concurrent engineer edit'
    assert not (project/'outputs/results.json').exists()


def test_task_queue_and_report_keep_workflow_identity_across_resume(workflow_setup,tmp_path):
    from engineering_agent.tasks import TaskRunner,input_snapshot
    from engineering_agent.queue import TaskQueue
    from engineering_agent.reports import build_report
    project,manager,config,plan,make=workflow_setup
    queue=TaskQueue(tmp_path/'queue.db')
    task=queue.submit('fixture',str(project),inputs=input_snapshot())
    report=TaskRunner(make(),queue).run(task,execute=True)
    assert queue.get(task)['status']=='waiting'
    assert queue.get(task)['workflow_id']==report['workflow_id']
    saved=build_report(queue,task)
    assert saved['resume_allowed'] and not saved['retry_allowed'] and saved['error'] is None
    (tmp_path/'gate').touch();config['timeouts']['step_seconds']=10
    resumed=TaskRunner(make(),queue).resume(task)
    assert resumed['status']=='committed'
    assert queue.get(task)['status']=='completed'
    assert not build_report(queue,task)['resume_allowed']
    assert queue.find_workflow(report['workflow_id'])['id']==task


def test_safe_mcp_exposes_read_only_workflow_actions(workflow_setup):
    from engineering_agent.mcp_server import MCPServer
    from engineering_agent.protocol import ProtocolError
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup);server=MCPServer(make())
    schema=next(t for t in server._tools() if t['name']=='engineering_workflow')
    assert schema['inputSchema']['properties']['action']['enum']==['list','status']
    result=server.call('engineering_workflow',{'action':'status','workflow_id':report.workflow_id,'project_dir':str(project)})
    assert result['workflow_id']==report.workflow_id
    with pytest.raises(ProtocolError,match='observation only'):
        server.call('engineering_workflow',{'action':'resume','workflow_id':report.workflow_id,'project_dir':str(project)})


def test_status_read_does_not_require_long_running_project_lock(workflow_setup):
    project,manager,config,plan,make=workflow_setup
    report=start(workflow_setup)
    with RunTransaction(project) as txn:
        txn.begin({'fixture':'another client is running'})
        status=make().workflow('status',report.workflow_id,project_dir=str(project))
        assert status['status']=='waiting'
        with pytest.raises(RuntimeError,match='PROJECT_LOCKED'):
            make().workflow('resume',report.workflow_id,project_dir=str(project))
        txn.rollback('fixture')


@pytest.mark.parametrize('path,code',[('OUTPUTS/MODEL.STEP','WORKFLOW_PATH_CASE_CONFLICT'),
                                    ('outputs/model.step/part.step','WORKFLOW_PATH_COLLISION')])
def test_cross_step_windows_path_collisions_are_rejected_before_launch(workflow_setup,path,code):
    project,manager,config,plan,make=workflow_setup
    plan.steps[1].inputs=[];plan.steps[1].outputs=[path]
    with worker_error(code): make().run('fixture',project_dir=str(project),dry_run=False)
    assert not manager.store.list()['jobs']


def test_failed_engineering_metric_creates_child_repair_workflow_without_overwriting_parent(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    (tmp_path/'gate').touch()
    config['timeouts']['step_seconds']=10
    first=make().run('fixture',project_dir=str(project),dry_run=False)
    assert first.status=='committed'
    parent=WorkflowStore(project).read(first.workflow_id)
    # The fixture has no vendor contract, so install a persisted evidence record
    # exactly as a real independent vendor validator would have produced it.
    target=next(item for item in parent['report']['results'] if item['step_id']=='solve')
    target['details']['vendor_validation']={'engineering_acceptance':'failed','metric_verdicts':[
        {'name':'equivalent_stress','value':120,'unit':'MPa','operator':'<=','target':80,'passed':False}]}
    WorkflowStore(project).save(parent)
    config['tools']['ansys']['actions']['mesh_and_solve']['repair_strategies']=[{
        'id':'try_preconfigured_load_case','when':{'metric':'equivalent_stress','failed':True},
        'parameter_patch':{'marker':str(tmp_path/'repair-count')},
        'reason':'切换工程师已配置的备用载荷工况，不修改验收阈值。','max_attempts':1}]
    repaired=make().workflow('repair',first.workflow_id,project_dir=str(project))
    assert repaired['status']=='committed',repaired
    assert repaired['repair']['parent_workflow_id']==first.workflow_id
    child_id=repaired['workflow_id']
    child=WorkflowStore(project).read(child_id)
    assert child['parent_workflow_id']==first.workflow_id
    assert child['repair_candidate']['strategy_id']=='try_preconfigured_load_case'
    assert child['plan']['steps'][1]['parameters']['marker']==str(tmp_path/'repair-count')
    assert all(path.startswith(f'artifacts/repairs/{first.workflow_id}/') for path in child['before'])
    assert len((tmp_path/'model-count').read_text().splitlines())==2
    assert len((tmp_path/'repair-count').read_text().splitlines())==1
    assert (project/'outputs/model.step').is_file() and (project/'outputs/results.json').is_file()
    assert (project / next(path for path in child['published'] if path['path'].startswith('artifacts/repairs/'))['path']).is_file()
    parent_after=WorkflowStore(project).read(first.workflow_id)
    assert parent_after['repair_history'][0]['child_workflow_id']==child_id


def test_repair_requires_explicit_strategy_and_does_not_change_thresholds(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    (tmp_path/'gate').touch();config['timeouts']['step_seconds']=10
    first=make().run('fixture',project_dir=str(project),dry_run=False)
    parent=WorkflowStore(project).read(first.workflow_id)
    target=next(item for item in parent['report']['results'] if item['step_id']=='solve')
    target['details']['vendor_validation']={'engineering_acceptance':'failed','metric_verdicts':[
        {'name':'equivalent_stress','value':120,'unit':'MPa','operator':'<=','target':80,'passed':False}]}
    WorkflowStore(project).save(parent)
    result=make().workflow('repair',first.workflow_id,project_dir=str(project))
    assert result['status']=='needs_review'
    assert '没有匹配' in result['message']
    assert not list((project/'artifacts/repairs').rglob('state.json'))
    assert parent['report']['results'][-1]['details']['vendor_validation']['metric_verdicts'][0]['target']==80


def test_repair_rejects_strategy_that_attempts_to_change_contract(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    (tmp_path/'gate').touch();config['timeouts']['step_seconds']=10
    first=make().run('fixture',project_dir=str(project),dry_run=False)
    parent=WorkflowStore(project).read(first.workflow_id)
    target=next(item for item in parent['report']['results'] if item['step_id']=='solve')
    target['details']['vendor_validation']={'engineering_acceptance':'failed','metric_verdicts':[
        {'name':'equivalent_stress','value':120,'unit':'MPa','operator':'<=','target':80,'passed':False}]}
    WorkflowStore(project).save(parent)
    config['tools']['ansys']['actions']['mesh_and_solve']['repair_strategies']=[{
        'id':'unsafe','when':{'metric':'equivalent_stress','failed':True},
        'parameter_patch':{'metric_rules':[{'name':'equivalent_stress','operator':'<=','target':1000,'unit':'MPa'}]}}]
    with worker_error('REPAIR_PATCH_IMMUTABLE_PARAMETER'):
        make().workflow('repair',first.workflow_id,project_dir=str(project))


def test_repair_strategy_attempt_limit_is_enforced(workflow_setup,tmp_path):
    project,manager,config,plan,make=workflow_setup
    (tmp_path/'gate').touch();config['timeouts']['step_seconds']=10
    first=make().run('fixture',project_dir=str(project),dry_run=False)
    parent=WorkflowStore(project).read(first.workflow_id)
    target=next(item for item in parent['report']['results'] if item['step_id']=='solve')
    target['details']['vendor_validation']={'engineering_acceptance':'failed','metric_verdicts':[
        {'name':'equivalent_stress','value':120,'unit':'MPa','operator':'<=','target':80,'passed':False}]}
    WorkflowStore(project).save(parent)
    config['tools']['ansys']['actions']['mesh_and_solve']['repair_strategies']=[{
        'id':'one_try','when':{'metric':'equivalent_stress','failed':True},
        'parameter_patch':{'marker':str(tmp_path/'repair-count')},'max_attempts':1}]
    first_repair=make().workflow('repair',first.workflow_id,project_dir=str(project))
    assert first_repair['status']=='committed'
    second=make().workflow('repair',first.workflow_id,project_dir=str(project))
    assert second['status']=='needs_review'
    assert len((tmp_path/'repair-count').read_text().splitlines())==1
