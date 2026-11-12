"""Actual subprocess export runners with fake vendor programs, never licensed solves."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.adapters.base import AdapterContext
from engineering_agent.adapters.vendor import AdamsAdapter, ViGradeAdapter
from engineering_agent.models import PlanStep, ToolName
from engineering_agent.vendor_evidence import read_export
from engineering_agent.worker_protocol import WorkerError

ROOT = Path(__file__).resolve().parents[1] / 'examples' / 'vendor_runners'


def setup_export(tmp_path, tool='adams', contents=None):
    kind = 'adams_export' if tool == 'adams' else 'vi_grade_export'
    action = 'build_and_run_multibody' if tool == 'adams' else 'run_vehicle_dynamics'
    filename = 'adams_command_runner.py' if tool == 'adams' else 'vi_grade_scenario_runner.py'
    project = tmp_path / 'project'
    (project / 'scripts').mkdir(parents=True)
    shutil.copy2(ROOT / filename, project / 'scripts/runner.py')
    contents = contents if contents is not None else {'metrics': {'peak': 12.5}, 'units': {'peak': 'm/s^2'}}
    (project / 'scripts/vendor.py').write_text('from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text(' + repr(json.dumps(contents)) + ')\n')
    (project / 'scenario.txt').write_text('Fixture only; no licensed software')
    binding = {'script': 'scripts/runner.py', 'command': [sys.executable, '{script}', '{request_file}'],
               'inputs': ['scripts/vendor.py'], 'validation': {'kind': kind},
               'vendor_options': {'command': [sys.executable, 'scripts/vendor.py', '{result_output}' if tool == 'adams' else '{metrics_output}']}}
    params = {'command_file' if tool == 'adams' else 'scenario_file': 'scenario.txt',
              'result_output' if tool == 'adams' else 'metrics_output': 'artifacts/result.json',
              'report_output': 'artifacts/report.json', 'required_metrics': ['peak'],
              'metric_units': {'peak': 'm/s^2'}, 'scenario': 'fixture',
              'metric_rules': [{'name': 'peak', 'operator': '<=', 'target': 10, 'unit': 'm/s^2'}]}
    cfg = {'actions': {action: binding}}
    adapter = (AdamsAdapter if tool == 'adams' else ViGradeAdapter)(cfg)
    step = PlanStep('export', ToolName(tool), action, 'Fixture export', parameters=params)
    return project, adapter, step, binding


@pytest.mark.parametrize('tool', ['adams', 'vi-grade'])
def test_export_preserves_failed_design_verdict_without_repeating_successful_solve(tmp_path, tool):
    project, adapter, step, _ = setup_export(tmp_path, tool)
    result = adapter.execute(step, AdapterContext(project, False, workspace_dir=tmp_path / 'staging'))
    assert result.status == 'completed', result.to_dict()
    verdict = result.details['vendor_validation']
    assert result.details['verification'] == 'artifact_and_vendor_evidence'
    assert verdict['engineering_acceptance'] == 'failed'
    assert verdict['metric_verdicts'][0]['value'] == 12.5
    assert not verdict['real_machine_verified']


@pytest.mark.parametrize('contents', [
    {'metrics': {'peak': True}, 'units': {'peak': 'm/s^2'}},
    {'metrics': {'peak': 'NaN'}, 'units': {'peak': 'm/s^2'}},
    {'metrics': {'peak': 1}, 'units': {'peak': 'MPa'}},
    {'metrics': {'peak': 1}}, {'metrics': {}, 'units': {'peak': 'm/s^2'}}])
def test_malformed_or_dimensionally_wrong_metrics_never_publish(tmp_path, contents):
    project, adapter, step, _ = setup_export(tmp_path, contents=contents)
    agent = EngineeringAgent({'generate_scripts': False, 'tools': {'adams': adapter.config}})
    result = agent.execute_operation('adams.build_and_run_multibody', {**step.parameters, 'project_dir': str(project), 'dry_run': False})
    assert result['status'] == 'failed'
    assert result['details']['transaction_status'] == 'rolled_back'
    assert not (project / 'artifacts/result.json').exists()


def test_model_parameters_cannot_replace_trusted_executable(tmp_path):
    project, adapter, step, _ = setup_export(tmp_path)
    step.parameters['vendor_command'] = [sys.executable, '-c', 'raise Exception("unexpected")']
    result = adapter.execute(step, AdapterContext(project, False, workspace_dir=tmp_path / 'stage'))
    assert result.status == 'failed'
    assert 'COMMAND_MUST_BE_CONFIGURED' in result.details['stderr']


@pytest.mark.parametrize('data', [b'time,acc\n0,3\n1,NaN\n', b'time,acc\n0,3\n1,\n', b'time,acc,acc\n0,3,4\n', b'time,acc\n0,3\n1\n'])
def test_csv_does_not_silently_choose_an_earlier_valid_sample(data):
    expected = {'required_metrics': ['peak'], 'metric_columns': {'peak': {'column': 'acc', 'reducer': 'max', 'unit': 'm/s^2'}}}
    with pytest.raises(WorkerError):
        read_export(data, '.csv', expected)


def test_csv_reducer_is_explicit():
    expected = {'required_metrics': ['peak'], 'metric_columns': {'peak': {'column': 'acc', 'reducer': 'abs_max', 'unit': 'm/s^2'}}}
    assert read_export(b'time,acc\n0,3\n1,-9\n2,2\n', '.csv', expected)['peak']['value'] == 9
