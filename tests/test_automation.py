import json

import httpx
import pytest

from orchestrator.automation import (
    AutomationVerification, extract_frontend_evidence, run_automation,
    verify_automation_result,
)
from orchestrator.adapters import RemoteAdapter, RemoteRouteError
from orchestrator.cli import main


@pytest.mark.parametrize('task,parent,status', [
    ({'creation_source': 'manual_vfc', 'parent_task_id': 'p'}, {'id': 'p'}, 'automation_mismatch'),
    ({'creation_source': 'automation_task'}, None, 'automation_success'),
    ({'creation_source': 'automation'}, None, 'automation_success'),
    ({'parent_task_id': 'p'}, None, 'automation_success'),
    ({'parent_task_id': '  '}, None, 'blocked'),
    ({'status': 'success'}, None, 'blocked'),
    ({}, {}, 'blocked'),
    ({}, {'id': 'p', 'creation_source': 'automation_task'}, 'automation_success'),
    ({'parent_task_id': 'p'}, {'id': 'different'}, 'blocked'),
    ({}, {'id': 'p', 'creation_source': 'manual_vfc'}, 'blocked'),
])
def test_verification(task, parent, status):
    result = verify_automation_result(task, parent)
    assert isinstance(result, AutomationVerification)
    assert result.status == status


def test_separate_routes_and_no_writes(monkeypatch):
    adapter = RemoteAdapter({'remote_api': {'routes': {'get_automation_task': ['GET', '/wrong']}}})
    with pytest.raises(RemoteRouteError):
        adapter.get_automation_task('t')
    adapter.config['automation_routes'] = {'get_task': ['POST', '/create']}
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: pytest.fail('remote write attempted'))
    with pytest.raises(RemoteRouteError):
        adapter.get_automation_task('t')


def test_read_only_401_never_refreshes(monkeypatch):
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'},
                             'automation_routes': {'get_task': ['GET', '/automation/{task_id}']}})
    adapter.refresh_token = 'secret'
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url))
        return httpx.Response(401, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, 'request', request)
    with pytest.raises(httpx.HTTPStatusError):
        adapter.get_automation_task('a/b')
    assert calls == [('GET', 'https://example.test/api/v1/automation/a%2Fb')]


def test_evidence_and_blocked_probe(tmp_path, monkeypatch):
    artifacts = tmp_path / 'frontend'; artifacts.mkdir()
    (artifacts / 'app.js').write_text('router.push(`/automation/create`); api.post(`/automation/tasks`, payload)')
    evidence = extract_frontend_evidence(artifacts)
    assert evidence['status'] == 'found'
    assert evidence['creation_api_confirmed'] is False
    assert any('/automation/create' in item['snippet'] for item in evidence['matches'])
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: pytest.fail('unexpected remote call'))
    for kind in ('live', 'link', 'ecom'):
        result = run_automation(RemoteAdapter({}), kind, artifacts, tmp_path / 'runs')
        assert result['status'] == 'blocked'
        assert result['read_only'] is True
        assert result['remote_write_performed'] is False
        assert json.loads((tmp_path / result['artifact_path']).read_text())['status'] == 'blocked'


def test_probe_get_and_failure_persisted(tmp_path, monkeypatch):
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'},
                             'automation_routes': {'list_tasks': ['GET', '/automation']}})
    calls = []
    def request(method, url, **kwargs):
        calls.append(method)
        return httpx.Response(200, json={'items': []}, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, 'request', request)
    result = run_automation(adapter, 'live', tmp_path / 'absent', tmp_path / 'runs')
    assert calls == ['GET']
    assert result['status'] == 'blocked'
    assert result['probe']['status'] == 'ok'
    assert result['frontend_evidence']['status'] == 'missing'
    assert result['creation_api_confirmed'] is False


def test_cli_commands(tmp_path, capsys, monkeypatch):
    config = tmp_path / 'config.json'; config.write_text('{}')
    task = tmp_path / 'task.json'; task.write_text('{"creation_source":"manual_vfc"}')
    assert main(['verify-automation-result', '--task-json', str(task), '--runs-dir', str(tmp_path / 'runs')]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'automation_mismatch'
    monkeypatch.setattr(httpx, 'request', lambda *a, **k: pytest.fail('unexpected remote call'))
    assert main(['run-automation', '--config', str(config), '--kind', 'live', '--runs-dir', str(tmp_path / 'runs'), '--frontend-artifacts', str(tmp_path / 'absent')]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'blocked'
    assert out['read_only'] is True
    assert list((tmp_path / 'runs').glob('*/result.json'))
