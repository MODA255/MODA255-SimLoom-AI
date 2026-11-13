"""Real subprocess and HTTP evidence for job durability, not vendor acceptance."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from engineering_agent.worker_jobs import JobManager, JobStore, TERMINAL, ProcessGuard, request_digest
from engineering_agent.worker_protocol import SCHEMA, WorkerError, pack_file
from engineering_agent.windows_bridge import create_bridge_server

SCRIPT = '''import json, os, sys, time
from pathlib import Path
request=json.loads(Path(sys.argv[1]).read_text())
p=request["parameters"]
print("runner-started", flush=True)
marker=p.get("marker")
if marker:
    with open(marker,"a") as out: out.write(request["request_id"]+"\\n")
if p.get("gate"):
    while not Path(p["gate"]).exists(): time.sleep(.05)
time.sleep(p.get("delay",0))
if p.get("exit"): sys.exit(p["exit"])
for name in request["outputs"]:
    target=Path(name); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({"job":request["request_id"],"fixture":True}))
print("runner-finished", flush=True)
'''
COMMAND=[sys.executable,"{script}","{request_file}"]


@pytest.fixture
def job_setup(tmp_path):
    inputs=tmp_path/'inputs'; inputs.mkdir()
    (inputs/'runner.py').write_text(SCRIPT)
    manager=JobManager(tmp_path/'worker', {'catia.create_or_update_model':COMMAND}, timeout=20)
    def request(**params):
        return {"schema_version":SCHEMA,"operation":"job.submit","request_id":uuid.uuid4().hex,
                "tool":"catia","action":"create_or_update_model","script":"runner.py",
                "parameters":params,"inputs":[],"outputs":["artifacts/model.step"],
                "files":[pack_file(inputs,"runner.py")]}
    yield manager,request
    # Let queued requests cancel without starting more runners; active runners are stopped.
    for job in manager.store.list(limit=100)['jobs']:
        if job['status'] not in TERMINAL: manager.store.cancel(job['request_id'])
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if all(j['status'] in TERMINAL for j in manager.store.list(limit=100)['jobs']): break
        time.sleep(.05)


def until(manager, identifier, predicate=lambda x:x['status'] in TERMINAL, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        item=manager.store.status(identifier)
        if predicate(item): return item
        time.sleep(.05)
    raise AssertionError(item)


def test_submit_returns_while_runner_lives_and_result_is_queryable(job_setup,tmp_path):
    manager,request=job_setup
    gate=tmp_path/'release'
    payload=request(gate=str(gate))
    submitted=manager.submit(payload)
    assert submitted['status'] in {'accepted','queued','running'}
    running=until(manager,payload['request_id'],lambda r:bool(r['runner_pid']))
    assert running['status']=='running'
    assert running['software_lease']==payload['request_id']
    guard=manager.store.guard(payload['request_id'])
    assert not guard.acquire()  # Actual open process lock, not a heartbeat-age guess.
    deadline=time.monotonic()+3
    logs=manager.store.logs(payload['request_id'])
    while not logs['text'] and time.monotonic()<deadline:
        time.sleep(.02)
        logs=manager.store.logs(payload['request_id'])
    assert 'runner-started' in logs['text']
    assert manager.store.logs(payload['request_id'],offset=logs['next_offset'])['text']==''
    gate.touch()
    finished=until(manager,payload['request_id'])
    assert finished['status']=='completed' and not finished['software_lease']
    result=manager.store.result(payload['request_id'])
    assert result['status']=='completed' and result['outputs'][0]['content_base64']
    assert result['request_digest']==request_digest(payload)
    events=manager.store.events(payload['request_id'],limit=2)
    assert [e['state'] for e in events['events']]==['accepted','queued']
    more=manager.store.events(payload['request_id'],after=events['next_cursor'])
    assert any(e['state']=='completed' for e in more['events'])


def test_concurrent_repeated_submission_starts_one_runner_and_conflict_rejected(job_setup,tmp_path):
    manager,request=job_setup
    marker=tmp_path/'executions'
    payload=request(marker=str(marker),delay=.2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(manager.submit,[payload]*8))
    assert all(item['request_id']==payload['request_id'] for item in results)
    assert until(manager,payload['request_id'])['status']=='completed'
    assert marker.read_text().splitlines()==[payload['request_id']]
    assert manager.submit(payload)['status']=='completed'
    changed=dict(payload,parameters={'delay':3})
    with pytest.raises(WorkerError,match='different inputs'): manager.submit(changed)
    assert marker.read_text().splitlines()==[payload['request_id']]


def test_second_job_waits_for_same_software_and_can_cancel_without_starting(job_setup,tmp_path):
    manager,request=job_setup
    gate=tmp_path/'gate'; marker=tmp_path/'marker'
    first=request(gate=str(gate)); second=request(marker=str(marker))
    manager.submit(first)
    until(manager,first['request_id'],lambda j:bool(j['runner_pid']))
    manager.submit(second)
    waiting=until(manager,second['request_id'],lambda j:j['status']=='queued')
    assert waiting['software_lease']==first['request_id']
    assert not marker.exists()
    manager.store.cancel(second['request_id'])
    cancelled=until(manager,second['request_id'])
    assert cancelled['status']=='cancelled' and not cancelled['requires_review']
    assert not marker.exists()
    gate.touch()
    assert until(manager,first['request_id'])['status']=='completed'


def test_running_cancel_retains_software_lease_until_explicit_inspection(job_setup,tmp_path):
    manager,request=job_setup
    payload=request(delay=10)
    manager.submit(payload)
    until(manager,payload['request_id'],lambda j:bool(j['runner_pid']))
    manager.store.cancel(payload['request_id'])
    stopped=until(manager,payload['request_id'])
    assert stopped['status']=='cancelled' and stopped['requires_review']
    assert stopped['software_lease']==payload['request_id']
    result=manager.store.result(payload['request_id'])
    assert result['error']['code']=='JOB_CANCELLED'
    with pytest.raises(WorkerError): manager.store.release(payload['request_id'],False,'checked')
    # Terminal persistence can precede final guard release by a few instructions.
    deadline=time.monotonic()+3
    while True:
        try:
            released=manager.store.release(payload['request_id'],True,'Fixture runner checked stopped')
            break
        except WorkerError as exc:
            if exc.code!='JOB_STILL_ACTIVE' or time.monotonic()>deadline: raise
            time.sleep(.02)
    assert not released['software_lease'] and not released['requires_review']
    assert released['released_reason']=='Fixture runner checked stopped'


def test_result_mutation_is_rejected_and_list_pages_are_stable(job_setup):
    manager,request=job_setup
    ids=[]
    for _ in range(3):
        payload=request(); ids.append(payload['request_id']); manager.submit(payload)
        assert until(manager,ids[-1])['status']=='completed'
    page=manager.store.list(limit=2)
    assert [j['request_id'] for j in page['jobs']]==list(reversed(ids[1:]))
    assert manager.store.list(limit=2,before=page['next_cursor'])['jobs'][0]['request_id']==ids[0]
    (manager.root/'jobs'/ids[0]/'artifacts/model.step').write_text('changed externally')
    with pytest.raises(WorkerError,match='artifacts/model.step'): manager.store.result(ids[0])


def test_worker_restart_does_not_restart_running_job(job_setup,tmp_path):
    manager,request=job_setup
    gate=tmp_path/'gate'; marker=tmp_path/'marker'
    payload=request(gate=str(gate),marker=str(marker))
    manager.submit(payload)
    active=until(manager,payload['request_id'],lambda j:bool(j['runner_pid']))
    recreated=JobManager(manager.root,manager.commands)
    assert recreated.store.status(payload['request_id'])['runner_pid']==active['runner_pid']
    assert recreated.submit(payload)['runner_pid']==active['runner_pid']
    gate.touch()
    assert until(recreated,payload['request_id'])['status']=='completed'
    assert marker.read_text().splitlines()==[payload['request_id']]


def test_killed_supervisor_is_interrupted_and_does_not_free_software(job_setup,tmp_path):
    manager,request=job_setup
    gate=tmp_path/'gate'
    payload=request(gate=str(gate))
    manager.submit(payload)
    active=until(manager,payload['request_id'],lambda j:bool(j['runner_pid']))
    os.kill(active['supervisor_pid'],signal.SIGTERM)
    interrupted=until(manager,payload['request_id'])
    assert interrupted['status']=='interrupted'
    assert interrupted['requires_review'] and interrupted['software_lease']==payload['request_id']
    assert manager.submit(payload)['status']=='interrupted'
    # The original runner survives supervisor loss; release its fixture gate explicitly.
    gate.touch()
    deadline=time.monotonic()+5
    output=manager.root/'jobs'/payload['request_id']/'artifacts/model.step'
    while not output.exists() and time.monotonic()<deadline: time.sleep(.05)
    assert output.exists()
    assert manager.store.result(payload['request_id'])['status']=='failed'


def test_timeout_stops_direct_runner_and_preserves_review_state(job_setup):
    manager,request=job_setup
    manager.timeout=.25
    payload=request(delay=10)
    manager.submit(payload)
    result=until(manager,payload['request_id'])
    assert result['status']=='failed' and result['requires_review']
    assert manager.store.result(payload['request_id'])['error']['code']=='SOFTWARE_TIMEOUT'


def test_cancel_accepted_job_prevents_late_supervisor_start(job_setup,monkeypatch,tmp_path):
    manager,request=job_setup
    monkeypatch.setattr(manager,'_spawn',lambda identifier:None)
    payload=request(marker=str(tmp_path/'marker'))
    assert manager.submit(payload)['status']=='accepted'
    assert manager.store.cancel(payload['request_id'])['status']=='cancelled'
    from engineering_agent.worker_jobs import supervise
    supervise(manager.root,payload['request_id'])
    assert not (tmp_path/'marker').exists()


def test_http_job_operations_require_token_and_recover_after_service_restart(job_setup):
    manager,request=job_setup
    config={'root_dir':str(manager.root),'commands':manager.commands,'port':0,'token':'test-token'}
    payload=request(delay=.5)
    def start():
        server=create_bridge_server(config)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        return server,thread,f'http://127.0.0.1:{server.server_address[1]}'
    server,thread,url=start()
    headers={'X-Bridge-Token':'test-token'}
    try:
        assert httpx.post(url,json=payload).status_code==401
        response=httpx.post(url,json=payload,headers=headers).json()
        assert response['request_id']==payload['request_id']
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
    server,thread,url=start()
    try:
        state=until(manager,payload['request_id'])
        request_status={'schema_version':SCHEMA,'operation':'job.status','request_id':payload['request_id']}
        response=httpx.post(url,json=request_status,headers=headers).json()
        assert response['status']=='completed'
        assert httpx.post(url,json=dict(request_status,operation='job.result'),headers=headers).json()['outputs']
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def remote_agent(manager, project, monkeypatch, timeout=5):
    from engineering_agent import EngineeringAgent
    from engineering_agent.models import ToolName
    cfg={'generate_scripts':False,'timeouts':{'step_seconds':timeout},'tools':{'catia':{
        'bridge_url':'http://fixture-worker','poll_interval_seconds':.05,
        'actions':{'create_or_update_model':{'script':'runner.py','outputs':['artifacts/model.step']}}}}}
    agent=EngineeringAgent(cfg)
    adapter=agent.adapters[ToolName.CATIA]
    monkeypatch.setattr(adapter,'_post',lambda payload,timeout:manager.handle(payload))
    (project/'runner.py').write_text(SCRIPT)
    return agent,adapter


def test_dropped_submission_response_reconnects_same_job_without_second_execution(job_setup,tmp_path,monkeypatch):
    from engineering_agent.adapters.base import AdapterContext
    from engineering_agent.models import PlanStep,ToolName
    manager,request=job_setup
    project=tmp_path/'project'; project.mkdir()
    agent,adapter=remote_agent(manager,project,monkeypatch)
    marker=tmp_path/'marker'; lost=[False]
    def post(payload,timeout):
        response=manager.handle(payload)
        if payload['operation']=='job.submit' and not lost[0]:
            lost[0]=True
            raise ConnectionError('response lost after durable acceptance')
        return response
    monkeypatch.setattr(adapter,'_post',post)
    step=PlanStep('model',ToolName.CATIA,'create_or_update_model','fixture',outputs=['artifacts/model.step'],parameters={'marker':str(marker)})
    result=adapter.execute(step,AdapterContext(project,False,timeout_seconds=10))
    assert result.status=='completed',result.to_dict()
    identifier=result.details['remote_job']['request_id']
    assert marker.read_text().splitlines()==[identifier]
    assert len(manager.store.list()['jobs'])==1


def test_observation_timeout_then_collect_existing_job_atomically(job_setup,tmp_path,monkeypatch):
    from engineering_agent.software_jobs_client import ReceiptStore
    manager,request=job_setup
    project=tmp_path/'project'; project.mkdir()
    agent,adapter=remote_agent(manager,project,monkeypatch,timeout=1)
    gate=tmp_path/'gate'; marker=tmp_path/'marker'
    report=agent.run('CATIA',project_dir=str(project),parameters={'gate':str(gate),'marker':str(marker)},dry_run=False)
    assert report.status=='rolled_back'
    result=report.results[0]
    assert result.details['error']['code']=='REMOTE_JOB_PENDING'
    identifier=result.details['remote_job']['request_id']
    assert not (project/'artifacts/model.step').exists()
    assert ReceiptStore(project).read(identifier)['collected'] is False
    gate.touch()
    assert until(manager,identifier)['status']=='completed'
    collected=agent.software_job('catia','collect',identifier,project_dir=str(project))
    assert collected['status']=='collected'
    assert (project/'artifacts/model.step').is_file()
    assert ReceiptStore(project).read(identifier)['collected'] is True
    assert agent.software_job('catia','collect',identifier,project_dir=str(project))['status']=='already_collected'
    assert marker.read_text().splitlines()==[identifier]


def test_reconnect_refuses_to_overwrite_result_edited_after_submission(job_setup,tmp_path,monkeypatch):
    manager,request=job_setup
    project=tmp_path/'project'; project.mkdir()
    agent,adapter=remote_agent(manager,project,monkeypatch,timeout=1)
    gate=tmp_path/'gate'
    report=agent.run('CATIA',project_dir=str(project),parameters={'gate':str(gate)},dry_run=False)
    identifier=report.results[0].details['remote_job']['request_id']
    (project/'artifacts/model.step').write_text('engineer edit')
    gate.touch(); until(manager,identifier)
    with pytest.raises(WorkerError,match='artifacts/model.step'):
        agent.software_job('catia','collect',identifier,project_dir=str(project))
    assert (project/'artifacts/model.step').read_text()=='engineer edit'


def test_normal_remote_run_publishes_receipt_in_same_transaction(job_setup,tmp_path,monkeypatch):
    from engineering_agent.software_jobs_client import ReceiptStore
    manager,request=job_setup
    project=tmp_path/'project'; project.mkdir()
    agent,adapter=remote_agent(manager,project,monkeypatch)
    report=agent.run('CATIA',project_dir=str(project),dry_run=False)
    assert report.status=='committed',report.to_dict()
    identifier=report.results[0].details['remote_job']['request_id']
    receipt=ReceiptStore(project).read(identifier)
    assert receipt['collected'] and receipt['publication_run_id']==report.run_id
    changes=json.loads((project/'artifacts/runs'/report.run_id/'changes.json').read_text())['files']
    assert any(item['path']==f'.engineering-agent/software-jobs/{identifier}.json' for item in changes)


def test_safe_mcp_job_api_cannot_cancel_or_publish(job_setup,tmp_path,monkeypatch):
    from engineering_agent.mcp_server import MCPServer
    manager,request=job_setup
    project=tmp_path/'project'; project.mkdir()
    agent,adapter=remote_agent(manager,project,monkeypatch)
    server=MCPServer(agent,profile='safe')
    schema=next(t for t in server._tools() if t['name']=='engineering_software_job')
    assert 'cancel' not in schema['inputSchema']['properties']['action']['enum']
    result=server.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'engineering_software_job','arguments':{'tool':'catia','action':'cancel','request_id':uuid.uuid4().hex}}})
    assert result['result']['isError'] is True


def test_software_queue_follows_submission_order(job_setup,tmp_path):
    manager,request=job_setup
    marker=tmp_path/'order'; gate=tmp_path/'gate'
    first=request(marker=str(marker),gate=str(gate)); second=request(marker=str(marker)); third=request(marker=str(marker))
    manager.submit(first);until(manager,first['request_id'],lambda j:bool(j['runner_pid']))
    manager.submit(second);manager.submit(third)
    until(manager,third['request_id'],lambda j:j['status']=='queued')
    gate.touch()
    assert until(manager,third['request_id'])['status']=='completed'
    assert marker.read_text().splitlines()==[first['request_id'],second['request_id'],third['request_id']]


def test_log_pages_preserve_exact_utf8_bytes(job_setup):
    import base64
    manager,request=job_setup
    payload=request();manager.submit(payload);until(manager,payload['request_id'])
    path=manager.root/'jobs'/payload['request_id']/'_logs/stdout.txt'
    content='求解开始，求解完成。'.encode('utf-8')
    path.write_bytes(content)
    result=b'';offset=0
    while offset<len(content):
        page=manager.store.logs(payload['request_id'],offset=offset,limit=2)
        result+=base64.b64decode(page['content_base64']);offset=page['next_offset']
    assert result==content


def test_failed_publication_restores_output_and_uncollected_receipt(job_setup,tmp_path,monkeypatch):
    from engineering_agent.transactions import RunTransaction
    from engineering_agent.software_jobs_client import ReceiptStore
    manager,request=job_setup
    project=tmp_path/'project';project.mkdir()
    agent,adapter=remote_agent(manager,project,monkeypatch)
    original=RunTransaction.commit
    def failing_commit(transaction,files,**options):
        def fail(_files): raise RuntimeError('fixture commit validation failure')
        return original(transaction,files,validate=fail,**options)
    monkeypatch.setattr(RunTransaction,'commit',failing_commit)
    with pytest.raises(RuntimeError,match='fixture commit'):
        agent.run('CATIA',project_dir=str(project),dry_run=False)
    receipts=ReceiptStore(project).list()
    assert len(receipts)==1 and receipts[0]['collected'] is False
    assert not (project/'artifacts/model.step').exists()
    assert manager.store.status(receipts[0]['request_id'])['status']=='completed'


def test_http_service_process_crash_does_not_stop_accepted_job(job_setup,tmp_path):
    manager,request=job_setup
    gate=tmp_path/'gate';marker=tmp_path/'marker';payload=request(gate=str(gate),marker=str(marker))
    config={'root_dir':str(manager.root),'commands':manager.commands,'port':0,'token':'process-test'}
    config_path=tmp_path/'config.json';config_path.write_text(json.dumps(config))
    code='''import json,sys
from pathlib import Path
from engineering_agent.windows_bridge import create_bridge_server
server=create_bridge_server(json.loads(Path(sys.argv[1]).read_text()))
Path(sys.argv[2]).write_text(str(server.server_address[1]))
server.serve_forever()
'''
    def start(index):
        ready=tmp_path/f'ready-{index}'
        process=subprocess.Popen([sys.executable,'-c',code,str(config_path),str(ready)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        deadline=time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline and process.poll() is None: time.sleep(.05)
        if not ready.exists():
            process.terminate();process.wait(5)
            raise AssertionError(process.stderr.read().decode())
        return process,f'http://127.0.0.1:{ready.read_text()}'
    process,url=start(1)
    headers={'X-Bridge-Token':'process-test'}
    try:
        assert httpx.post(url,json=payload,headers=headers).json()['request_id']==payload['request_id']
        running=until(manager,payload['request_id'],lambda j:bool(j['runner_pid']))
    finally:
        process.terminate();process.wait(5);process.stderr.close()
    assert manager.store.status(payload['request_id'])['runner_pid']==running['runner_pid']
    process,url=start(2)
    try:
        repeated=httpx.post(url,json=payload,headers=headers).json()
        assert repeated['runner_pid']==running['runner_pid']
        gate.touch();assert until(manager,payload['request_id'])['status']=='completed'
        result=httpx.post(url,json={'schema_version':SCHEMA,'operation':'job.result','request_id':payload['request_id']},headers=headers).json()
        assert result['status']=='completed'
        assert marker.read_text().splitlines()==[payload['request_id']]
    finally:
        process.terminate();process.wait(5);process.stderr.close()


def test_unexpected_execution_failure_keeps_lease_when_process_state_is_unknown(job_setup,monkeypatch):
    from engineering_agent.worker_jobs import supervise
    from engineering_agent.worker_protocol import failure
    manager,request=job_setup
    payload=request()
    manager.store.register(payload,manager.commands,20)
    monkeypatch.setattr('engineering_agent.worker_jobs.run_job',lambda *a,**kw:failure(RuntimeError('unexpected monitor failure'),payload['request_id']))
    supervise(manager.root,payload['request_id'])
    state=manager.store.status(payload['request_id'])
    assert state['status']=='failed' and state['requires_review']
    assert state['software_lease']==payload['request_id']


def test_status_recovers_durably_accepted_job_after_service_died_before_spawn(job_setup,tmp_path):
    manager,request=job_setup
    marker=tmp_path/'accepted-marker'
    payload=request(marker=str(marker))
    manager.store.register(payload,manager.commands,manager.timeout)
    replacement=JobManager(manager.root,manager.commands,timeout=20)
    query={'schema_version':SCHEMA,'operation':'job.status','request_id':payload['request_id']}
    replacement.handle(query)
    assert until(replacement,payload['request_id'])['status']=='completed'
    for _ in range(3): replacement.handle(query)
    assert marker.read_text().splitlines()==[payload['request_id']]


def test_status_of_unstarted_job_does_not_contend_with_launch_guard(job_setup,monkeypatch):
    manager,request=job_setup
    payload=request();store=manager.store
    store.register(payload,manager.commands,manager.timeout)
    def forbidden(*args): raise AssertionError('Observation must not stop the startup supervisor')
    with monkeypatch.context() as patch:
        patch.setattr(ProcessGuard,'acquire',forbidden)
        assert store.status(payload['request_id'])['status']=='accepted'
