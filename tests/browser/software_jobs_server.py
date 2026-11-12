"""Browser fixture: controlled processes, never commercial software."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from engineering_agent import EngineeringAgent
from engineering_agent.adapters.base import AdapterContext
from engineering_agent.models import AgentRequest, ExecutionPlan, PlanStep, ToolName
from engineering_agent.tasks import TaskRunner, input_snapshot
from engineering_agent.workflows import WorkflowStore
from engineering_agent.queue import TaskQueue
from engineering_agent.software_jobs_client import ReceiptStore
from engineering_agent.web import create_server
from engineering_agent.windows_bridge import create_bridge_server
from engineering_agent.worker_jobs import JobManager, TERMINAL
from engineering_agent.workspace import Workspace

root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
commands = {'catia.create_or_update_model': [sys.executable, '{script}', '{request_file}']}
bridge = create_bridge_server({'root_dir': str(root / 'worker'), 'commands': commands, 'port': 0, 'token': 'browser-fixture'})
threading.Thread(target=bridge.serve_forever, daemon=True).start()
agent = EngineeringAgent({'generate_scripts': False, 'tools': {'catia': {
    'bridge_url': f'http://127.0.0.1:{bridge.server_address[1]}', 'bridge_token': 'browser-fixture',
    'actions': {'create_or_update_model': {'script': 'runner.py'}}}}})
queue = TaskQueue(root / 'workspace.sqlite3')
project = Workspace(queue, root / 'projects').create_project({'name': '软件任务恢复验收', 'template': 'custom'})
project_dir = Path(project['project_dir'])
(project_dir / 'runner.py').write_text('''import json, sys, time
from pathlib import Path
request=json.loads(Path(sys.argv[1]).read_text())
print("fixture runner started",flush=True)
time.sleep(request["parameters"].get("delay",0))
for name in request["outputs"]:
    target=Path(name);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({"fixture":True,"request_id":request["request_id"]}))
print("fixture runner finished",flush=True)
''', encoding='utf-8')
manager = JobManager(root / 'worker', commands, timeout=60)
adapter = agent.adapters[ToolName.CATIA]
binding = adapter.config['actions']['create_or_update_model']

def submit(output, delay=0):
    step = PlanStep('browser', ToolName.CATIA, 'create_or_update_model', 'fixture', outputs=[output], parameters={'delay': delay})
    payload = adapter._prepare(step, AdapterContext(project_dir), binding)
    ReceiptStore(project_dir).create(payload, adapter.bridge_url)
    manager.submit(payload)
    return payload['request_id']

complete = submit('artifacts/completed.json')
deadline = time.monotonic() + 15
while manager.store.status(complete)['status'] not in TERMINAL and time.monotonic() < deadline:
    time.sleep(.05)
assert manager.store.status(complete)['status'] == 'completed'
original_plan = agent.plan
workflow_plan = ExecutionPlan(AgentRequest('已有工程链恢复 · browser fixture', str(project_dir)), [
    PlanStep('first', ToolName.CATIA, 'create_or_update_model', '生成原生模型副本', outputs=['artifacts/workflow-first.json']),
    PlanStep('second', ToolName.CATIA, 'create_or_update_model', '读取模型并汇总结果', depends_on=['first'],
             inputs=['artifacts/workflow-first.json'], outputs=['artifacts/workflow-final.json'], parameters={'delay': 2})])
agent.plan = lambda *args, **kwargs: workflow_plan
agent.config['timeouts'] = {'step_seconds': 1}
workflow_task = queue.submit('已有工程链恢复 · browser fixture', str(project_dir), project['id'], inputs=input_snapshot())
runner = TaskRunner(agent, queue)
workflow_report = runner.run(workflow_task, execute=True)
workflow_id = workflow_report['workflow_id']
for _ in range(10):
    saved = WorkflowStore(project_dir).read(workflow_id)
    if saved['steps']['second'].get('request_id'): break
    workflow_report = runner.resume(workflow_task)
assert workflow_report['status'] == 'waiting'
second_id = saved['steps']['second']['request_id']
deadline = time.monotonic() + 15
while manager.handle({'operation': 'job.status', 'request_id': second_id})['status'] not in TERMINAL and time.monotonic() < deadline:
    time.sleep(.05)
assert manager.store.status(second_id)['status'] == 'completed'
assert not (project_dir / 'artifacts/workflow-final.json').exists()
agent.plan = original_plan
agent.config['timeouts']['step_seconds'] = 10
active = submit('artifacts/active.json', delay=45)
server = create_server(agent, queue, port=0)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(json.dumps({'url': f'http://127.0.0.1:{server.server_address[1]}', 'project': project['id'],
                  'project_dir': str(project_dir), 'completed': complete, 'active': active, 'workflow_id': workflow_id, 'workflow_task': workflow_task}), flush=True)
try:
    sys.stdin.read()
finally:
    for job in manager.store.list()['jobs']:
        if job['status'] not in TERMINAL: manager.store.cancel(job['request_id'])
    deadline = time.monotonic() + 5
    while any(j['status'] not in TERMINAL for j in manager.store.list()['jobs']) and time.monotonic() < deadline:
        time.sleep(.05)
    server.shutdown(); server.server_close(); bridge.shutdown(); bridge.server_close()
