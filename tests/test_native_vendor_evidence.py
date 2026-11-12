"""Fake COM/Mechanical observations exercise the runners, not real installations."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from engineering_agent.adapters.base import AdapterContext
from engineering_agent.adapters.vendor import AnsysAdapter, CatiaAdapter
from engineering_agent.models import PlanStep, ToolName
from engineering_agent.software_worker import run_job, validate_job
from engineering_agent.software_jobs_client import ReceiptStore
from engineering_agent.vendor_evidence import (SCHEMA, build_contract, converted, requirements,
                                               strict_json, validate_evidence)
from engineering_agent.vendor_runners import Job, run_catia, run_mechanical
from engineering_agent.worker_jobs import request_digest
from engineering_agent.worker_protocol import SCHEMA as WORKER_SCHEMA, WorkerError, decode_files, pack_file


def job(tmp_path, tool='catia'):
    root = tmp_path / 'job'
    root.mkdir()
    if tool == 'catia':
        kind, action, source = 'catia_parameters', 'update_parameters', 'input.CATPart'
        params = {'input_model': source, 'native_output': 'artifacts/new.CATPart', 'step_output': 'artifacts/new.step',
                  'parameter_updates': [{'name': 'Length', 'value': '25mm'}], 'report_output': 'artifacts/report.json'}
    else:
        kind, action, source = 'mechanical_solve', 'solve_configured_model', 'input.mechdat'
        params = {'db_file': source, 'native_output': 'artifacts/new.mechdat', 'result_output': 'artifacts/results.json',
                  'analysis_name': 'Static', 'required_metrics': ['total_deformation', 'equivalent_stress'],
                  'metric_units': {'total_deformation': 'mm', 'equivalent_stress': 'MPa'},
                  'metric_rules': [{'name': 'total_deformation', 'operator': '<=', 'target': 1, 'unit': 'mm'}],
                  'report_output': 'artifacts/report.json'}
    (root / source).write_text('original input fixture')
    (root / 'runner.py').write_text('# fixture')
    outputs = requirements(kind, params)[1]
    records = [pack_file(root, name) for name in ['runner.py', source]]
    contract = build_contract(kind, params, records, outputs, tool, action)
    request = {'schema_version': WORKER_SCHEMA, 'request_id': 'a'*32, 'tool': tool, 'action': action,
               'script': 'runner.py', 'inputs': [source], 'outputs': outputs, 'files': records,
               'parameters': params, 'validation': contract}
    return Job(request, root)


def check(job, report):
    outputs = {name: job.path(name).read_bytes() for name in job.request['outputs']}
    return validate_evidence(report, job.contract, outputs, request_id=job.request['request_id'],
                             tool=job.request['tool'], action=job.request['action'])


class Parameter:
    Name = 'Length'
    ReadOnly = False
    OptionalRelation = None
    value = '10mm'
    def ValueAsString(self):
        return self.value
    def ValuateFromString(self, value):
        self.value = value


class Documents:
    def __init__(self, parameter=None, persisted='0.025m'):
        self.events, self.opened = [], []
        self.parameter = parameter or Parameter()
        self.persisted = persisted
    @property
    def Count(self):
        return len(self.opened)
    def Item(self, index):
        return self.opened[index-1]
    def Open(self, path):
        self.events.append(('open', path))
        assert not self.opened, 'Reopen must follow Close'
        if len(self.events) > 1:
            parameter = Parameter()
            parameter.value = self.persisted
        else:
            parameter = self.parameter
        parent = self
        class Document:
            FullName = path
            Part = NS(Parameters=NS(Count=1, Item=lambda index: parameter), Update=lambda: parent.events.append(('update', path)))
            def SaveAs(self, target):
                parent.events.append(('save', target))
                Path(target).write_text('native saved fixture')
            def Close(self):
                parent.events.append(('close', path))
                parent.opened.remove(self)
            def ExportData(self, target, fmt):
                assert fmt == 'stp'
                parent.events.append(('export', target))
                Path(target).write_text('ISO-10303-21;\nEND-ISO-10303-21;')
        document = Document()
        self.opened.append(document)
        return document


def test_catia_closes_before_reopen_and_compares_units_to_requested_value(tmp_path):
    j = job(tmp_path)
    documents = Documents()
    report = run_catia(j, connect=lambda: NS(Documents=documents))
    assert check(j, report)['passed']
    assert [event[0] for event in documents.events] == ['open','update','save','close','open','export','close']
    assert j.path('input.CATPart').read_text() == 'original input fixture'
    assert not documents.opened


def test_catia_changed_persisted_value_is_rejected_even_with_passed_flags(tmp_path):
    j = job(tmp_path)
    report = run_catia(j, connect=lambda: NS(Documents=Documents(persisted='12mm')))
    report['passed'] = True
    report['checks'] = [{'name': 'parameter_readback', 'passed': True}]
    with pytest.raises(WorkerError) as exc:
        check(j, report)
    assert exc.value.code == 'PARAMETER_READBACK_MISMATCH'


@pytest.mark.parametrize('attribute', ['ReadOnly', 'OptionalRelation'])
def test_catia_read_only_and_formula_parameters_are_never_written(tmp_path, attribute):
    j = job(tmp_path)
    parameter = Parameter()
    setattr(parameter, attribute, True if attribute == 'ReadOnly' else object())
    documents = Documents(parameter)
    with pytest.raises(WorkerError) as exc:
        run_catia(j, connect=lambda: NS(Documents=documents))
    assert exc.value.code == 'CATIA_PARAMETER_NOT_WRITABLE'
    assert parameter.value == '10mm' and not documents.opened
    assert not any(e[0] == 'save' for e in documents.events)


class Mechanical:
    version = 'fixture'
    def __init__(self, status='Done', solved=True, fail_evaluate=False):
        self.events = []
        self.status, self.solved, self.fail_evaluate = status, solved, fail_evaluate
        self.solution = NS(Status=status, ClearGeneratedData=lambda: self.events.append('clear'),
                           AddTotalDeformation=lambda: NS(IsSolved=solved, Maximum=NS(Value=.002, Unit='m')),
                           AddEquivalentStress=lambda: NS(IsSolved=solved, Maximum=NS(Value=5e6, Unit='Pa')),
                           Solve=lambda wait: self.events.append(('solve', wait)), EvaluateAllResults=self.evaluate)
        self.Model = NS(Analyses=[NS(Name='Static', Solution=self.solution)])
    def evaluate(self):
        self.events.append('evaluate')
        if self.fail_evaluate:
            raise RuntimeError('native evaluation failed')
    def open(self, path):
        self.events.append('open')
    def save_as(self, path, overwrite):
        assert not overwrite
        Path(path).write_text('Mechanical archive fixture')
        self.events.append('save')
    def exit(self):
        self.events.append('exit')


def test_mechanical_uses_native_quantity_and_keeps_failed_engineering_verdict(tmp_path):
    j = job(tmp_path, 'ansys')
    app = Mechanical()
    verdict = check(j, run_mechanical(j, lambda: app))
    assert verdict['metrics']['total_deformation'] == {'value': 2, 'unit': 'mm'}
    assert verdict['metrics']['equivalent_stress'] == {'value': 5, 'unit': 'MPa'}
    assert verdict['engineering_acceptance'] == 'failed'
    assert app.events == ['open','clear',('solve',True),'evaluate','save','exit']


@pytest.mark.parametrize('status', ['SolveRequired', 'NotSolved', 'Solving', 'Pending', 'Interrupted', 'Failed', ''])
def test_mechanical_rejects_every_noncompletion_state(tmp_path, status):
    j = job(tmp_path, 'ansys')
    app = Mechanical(status=status)
    with pytest.raises(WorkerError) as exc:
        run_mechanical(j, lambda: app)
    assert exc.value.code == 'MECHANICAL_SOLUTION_INCOMPLETE'
    assert app.events[-1] == 'exit'
    assert 'save' not in app.events


@pytest.mark.parametrize('kwargs', [{'solved': False}, {'fail_evaluate': True}])
def test_native_result_evaluation_failures_are_not_swallowed(tmp_path, kwargs):
    j = job(tmp_path, 'ansys')
    app = Mechanical(**kwargs)
    with pytest.raises((WorkerError, RuntimeError)):
        run_mechanical(j, lambda: app)
    assert app.events[-1] == 'exit' and 'save' not in app.events


@pytest.mark.parametrize('corruption', ['input','artifact','request','unit','reopen'])
def test_controller_recomputes_evidence_instead_of_trusting_checks(tmp_path, corruption):
    j = job(tmp_path)
    report = run_catia(j, connect=lambda: NS(Documents=Documents()))
    if corruption == 'input': report['inputs_after']['input.CATPart'] = '0'*64
    if corruption == 'artifact': report['artifacts']['artifacts/new.step'] = '0'*64
    if corruption == 'request': report['request_id'] = 'b'*32
    if corruption == 'unit': report['observations']['parameters']['Length']['after_reopen'] = '25MPa'
    if corruption == 'reopen': report['observations']['reopened_from_disk'] = False
    j.path(j.contract['report']).write_text(json.dumps(report))
    body = {'schema_version': WORKER_SCHEMA, 'request_id': j.request['request_id'], 'tool': 'catia', 'action': 'update_parameters',
            'status': 'completed', 'returncode': 0, 'verification': 'perfect', 'vendor_validation': {'passed': True},
            'outputs': [pack_file(j.root, name) for name in j.request['outputs']]}
    stage = tmp_path / 'collect'; stage.mkdir()
    step = PlanStep('c', ToolName.CATIA, 'update_parameters', 'collect')
    with pytest.raises(WorkerError):
        CatiaAdapter({}).collect_response(step, body, j.request['request_id'], j.request['outputs'], stage, j.contract)


def test_expectations_survive_receipt_and_affect_idempotency(tmp_path):
    j = job(tmp_path)
    receipt = ReceiptStore(tmp_path / 'project').create(j.request, 'http://worker')
    assert receipt['validation'] == j.contract
    changed = copy.deepcopy(j.request)
    changed['validation']['expectations']['parameter_updates'][0]['value'] = '50mm'
    assert request_digest(j.request) != request_digest(changed)
    changed = dict(j.request, vendor_options={'command': ['changed']})
    assert request_digest(j.request) != request_digest(changed)


def test_typed_actions_infer_transfer_and_output_lists(tmp_path):
    j = job(tmp_path)
    step = PlanStep('c', ToolName.CATIA, 'update_parameters', 'native change', parameters=j.request['parameters'])
    payload = CatiaAdapter({})._prepare(step, AdapterContext(j.root), {'script': 'runner.py'})
    assert payload['inputs'] == ['input.CATPart']
    assert set(payload['outputs']) == set(j.request['outputs'])
    validate_job(payload, {'catia.update_parameters': ['python', '{script}']})


def test_worker_rejects_expectations_detached_from_parameters(tmp_path):
    j = job(tmp_path)
    j.request['parameters']['parameter_updates'] = [{'name': 'Length','value': '123mm'}]
    with pytest.raises(WorkerError) as exc:
        validate_job(j.request, {'catia.update_parameters': ['python','{script}']})
    assert exc.value.code == 'VENDOR_CONTRACT_MISMATCH'


@pytest.mark.parametrize('value', ['25NaN', 'Infinitymm', '1fortnight', {'value': True, 'unit':'mm'}, {'value': float('nan'), 'unit':'mm'}])
def test_bad_quantities_fail_closed(value):
    with pytest.raises(WorkerError):
        converted(value, 'mm')


def test_duplicate_report_fields_rejected():
    with pytest.raises(WorkerError):
        strict_json(b'{"passed": false, "passed": true}')


def test_worker_detects_source_mutation_even_if_report_claims_original_hash(tmp_path):
    import sys
    j = job(tmp_path)
    script = '''import json,hashlib,sys
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text());v=r['validation'];p=v['expectations']
Path(p['native_output']).write_text('native fixture')
Path(p['step_output']).write_text('ISO-10303-21;\\nEND-ISO-10303-21;')
report={'schema_version':v['schema_version'],**{k:r[k] for k in ['request_id','tool','action']},'inputs_after':v['input_hashes'],
'artifacts':{n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in r['outputs'] if n!=v['report']},
'observations':{'reopened_from_disk':True,'parameters':{'Length':{'after_update':'25mm','after_reopen':'25mm'}}}}
Path(v['report']).write_text(json.dumps(report));Path(p['input_model']).write_text('changed by runner')
'''
    j.path('runner.py').write_text(script)
    j.request['files'] = [pack_file(j.root,n) for n in ['runner.py','input.CATPart']]
    j.request['validation']['input_hashes']['runner.py'] = j.request['files'][0]['sha256']
    result = run_job(tmp_path/'worker',j.request, {'catia.update_parameters':[sys.executable,'{script}','{request_file}']})
    assert result['status']=='failed' and result['error']['code']=='VENDOR_INPUT_CHANGED'


@pytest.mark.parametrize('corrupt', [False, True])
def test_reconnect_revalidates_saved_expectations_and_publishes_atomically(tmp_path, monkeypatch, corrupt):
    import base64
    from engineering_agent import EngineeringAgent
    from test_four_vendor_runners import setup_export
    project, adapter, step, binding = setup_export(tmp_path)
    adapter.bridge_url = 'http://worker'
    adapter.config['bridge_url'] = adapter.bridge_url
    payload = adapter._prepare(step, AdapterContext(project), binding)
    receipt = ReceiptStore(project).create(payload, adapter.bridge_url)
    body = run_job(tmp_path/'remote',payload, {'adams.build_and_run_multibody':binding['command']})
    assert body['status']=='completed',body
    body['request_digest'] = receipt['request_digest']
    if corrupt:
        record = next(r for r in body['outputs'] if r['path']=='artifacts/result.json')
        data=json.dumps({'metrics':{'peak':1},'units':{'peak':'MPa'}}).encode()
        record.update(content_base64=base64.b64encode(data).decode(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        # Change both raw export and report hashes; dimensional check must still reject.
        record = next(r for r in body['outputs'] if r['path']=='artifacts/report.json')
        report=json.loads(base64.b64decode(record['content_base64']))
        report['artifacts']['artifacts/result.json']=hashlib.sha256(data).hexdigest()
        data=json.dumps(report).encode()
        record.update(content_base64=base64.b64encode(data).decode(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    state = {**{k:payload[k] for k in ('schema_version','request_id','tool','action')},'request_digest':receipt['request_digest'],'status':'completed'}
    monkeypatch.setattr(adapter,'_post',lambda request,timeout: state if request['operation']=='job.status' else body)
    agent = EngineeringAgent()
    if corrupt:
        with pytest.raises(WorkerError) as exc:
            agent._collect_software_job(adapter,str(project),payload['request_id'])
        assert exc.value.code=='INCOMPATIBLE_UNITS'
        assert not ReceiptStore(project).read(payload['request_id'])['collected']
        assert not (project/'artifacts/result.json').exists()
    else:
        result=agent._collect_software_job(adapter,str(project),payload['request_id'])
        assert result['vendor_validation']['engineering_acceptance']=='failed'
        assert ReceiptStore(project).read(payload['request_id'])['collected']
        assert (project/'artifacts/result.json').is_file()


@pytest.mark.parametrize('path', ['../outside.CATPart','/absolute.CATPart','C:/outside.CATPart'])
def test_native_input_path_rejected_before_launch(tmp_path,path):
    j=job(tmp_path)
    j.request['parameters']['input_model']=path
    with pytest.raises(WorkerError):
        requirements('catia_parameters',j.request['parameters'])


def test_safe_mcp_profile_does_not_expose_session_launching_inspection():
    from engineering_agent import EngineeringAgent
    from engineering_agent.mcp_server import MCPServer
    tools=MCPServer(EngineeringAgent(),profile='safe')._tools()
    assert not any(t['name']=='operation.catia.inspect_model' for t in tools)
    full=MCPServer(EngineeringAgent(),profile='full')._tools()
    assert any(t['name']=='operation.catia.inspect_model' for t in full)


@pytest.mark.parametrize('patch', [{'parameter_updates': 'bad'}, {'parameter_updates': [{'name':'Length','value':'25mm','tolerance':-1}]},
    {'parameter_updates': [{'name':'Length','value':'25mm'},{'name':'Length','value':'26mm'}]}, {'parameter_updates': [{'name':'Length','value':'25mm','allow_relation':True}]}])
def test_bad_parameter_contracts_fail_before_software_launch(tmp_path,patch):
    j=job(tmp_path)
    with pytest.raises(WorkerError):
        requirements('catia_parameters',j.request['parameters']|patch)


@pytest.mark.parametrize('quantity_value', [{'value':1,'unit':[]}, {'value':1,'unit':{}}, {'value':1,'unit':None}])
def test_malformed_unit_types_produce_structured_error(quantity_value):
    with pytest.raises(WorkerError) as exc:
        converted(quantity_value,'mm')
    assert exc.value.code=='UNSUPPORTED_UNIT'


def test_examples_match_the_execution_contract():
    root=Path(__file__).resolve().parents[1]
    config=json.loads((root/'configs/vendor-actions.example.json').read_text())
    for tool,item in config['tools'].items():
        binding=next(iter(item['actions'].values()))
        params=json.loads((root/'examples/vendor_runners/parameters'/f'{tool}.json').read_text())
        source,outputs,_=requirements(binding['validation']['kind'],params)
        assert source and outputs and params['report_output'] in outputs
