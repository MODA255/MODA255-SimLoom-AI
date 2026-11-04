import httpx
import pytest

from orchestrator.adapters import RemoteAdapter, RemoteAuthError
from orchestrator.phase1_ingest_runners import run_upload_ingest


def test_upload_fallback_route_and_local_root_guard(tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'; path.write_bytes(b'x')
    seen = []
    response = httpx.Response(200, json={'id': 'a1', 'status': 'processing'}, request=httpx.Request('POST', 'https://example.test/api/v1/assets/upload'))
    monkeypatch.setattr('orchestrator.adapters.httpx.request', lambda method, url, **kwargs: (seen.append((method, url, kwargs)) or response))
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'local_input_roots': [str(tmp_path)], 'remote_api': {'routes': {'ingest_file': 'POST /assets/upload'}}})
    assert adapter.ingest_file({'path': path})['id'] == 'a1'
    assert seen[0][2]['files']['file'][0] == 'clip.mp4'
    with pytest.raises(ValueError, match='outside allowed'):
        adapter.ingest_file({'path': __file__})


def test_401_refresh_failure_is_structured(monkeypatch, tmp_path):
    path = tmp_path / 'clip.mp4'; path.write_bytes(b'x')
    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        return httpx.Response(401, json={'code': 'expired'}, request=req)
    monkeypatch.setattr('orchestrator.adapters.httpx.request', request)
    monkeypatch.setenv('REFRESH', 'r')
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'refresh_token_env': 'REFRESH'}, 'remote_api': {'routes': {'upload_file': 'POST /upload'}}})
    with pytest.raises(RemoteAuthError) as exc:
        adapter.ingest_file(path)
    assert exc.value.retry['attempted'] is True
    assert exc.value.retry['operation'] == 'upload_file'


def test_401_after_successful_refresh_is_structured_for_upload(monkeypatch, tmp_path):
    path = tmp_path / 'clip.mp4'; path.write_bytes(b'x')
    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        if url.endswith('/auth/refresh'):
            return httpx.Response(200, json={'access_token': 'new'}, request=req)
        return httpx.Response(401, json={'code': 'still-expired'}, request=req)
    monkeypatch.setattr('orchestrator.adapters.httpx.request', request)
    monkeypatch.setenv('REFRESH', 'r')
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'refresh_token_env': 'REFRESH'}, 'remote_api': {'routes': {'upload_file': 'POST /upload'}}})
    with pytest.raises(RemoteAuthError) as exc:
        adapter.ingest_file(path)
    assert exc.value.retry['attempted'] is True
    assert exc.value.retry['succeeded'] is False


def test_401_without_refresh_is_structured_for_upload(monkeypatch, tmp_path):
    path = tmp_path / 'clip.mp4'; path.write_bytes(b'x')
    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        return httpx.Response(401, json={'code': 'expired'}, request=req)
    monkeypatch.setattr('orchestrator.adapters.httpx.request', request)
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {'upload_file': 'POST /upload'}}})
    with pytest.raises(RemoteAuthError) as exc:
        adapter.ingest_file(path)
    assert exc.value.retry['attempted'] is False
    assert exc.value.retry['succeeded'] is False


def test_401_after_successful_refresh_is_structured_for_request(monkeypatch):
    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        if url.endswith('/auth/refresh'):
            return httpx.Response(200, json={'access_token': 'new'}, request=req)
        return httpx.Response(401, json={'code': 'still-expired'}, request=req)
    monkeypatch.setattr('orchestrator.adapters.httpx.request', request)
    monkeypatch.setenv('REFRESH', 'r')
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'refresh_token_env': 'REFRESH'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    with pytest.raises(RemoteAuthError) as exc:
        adapter.get_asset('asset-1')
    assert exc.value.retry['attempted'] is True
    assert exc.value.retry['succeeded'] is False


def test_401_without_refresh_is_structured_for_request(monkeypatch):
    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        return httpx.Response(401, json={'code': 'expired'}, request=req)
    monkeypatch.setattr('orchestrator.adapters.httpx.request', request)
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    with pytest.raises(RemoteAuthError) as exc:
        adapter.get_asset('asset-1')
    assert exc.value.retry['attempted'] is False
    assert exc.value.retry['succeeded'] is False


def test_upload_processing_history_is_retained(tmp_path):
    class Adapter:
        def ingest_file(self, payload): return {'id': 'a1', 'status': 'processing'}
        def get_asset(self, rid): return {'id': rid, 'status': 'processing'}
        def get_asset_progress(self, rid): return {'status': 'processing'}
    result = run_upload_ingest(Adapter(), {'chain_id': 'I-UPLOAD', 'payload': {'path': str(tmp_path / 'x')}, 'timeout_seconds': 0, 'poll_interval_seconds': 0}, tmp_path)
    assert result['processing_history'][0]['status'] == 'processing'


def test_upload_resume_observes_existing_asset_without_reposting(tmp_path):
    calls = []

    class Adapter:
        def ingest_file(self, payload):
            calls.append(payload)
            raise AssertionError('resume path must not upload again')
        def get_asset(self, rid):
            return {'id': rid, 'status': 'active', 'clip_count': 23}
        def get_asset_progress(self, rid):
            return {'status': 'active', 'percent': 100}

    result = run_upload_ingest(Adapter(), {
        'chain_id': 'I-UPLOAD', 'existing_asset_id': 'asset-existing',
        'payload': {'path': str(tmp_path / 'same.mp4')},
        'timeout_seconds': 0, 'poll_interval_seconds': 0,
    }, tmp_path)
    assert result['test_status'] == 'completed'
    assert result['submit']['resumed'] is True
    assert result['submit']['resource_id'] == 'asset-existing'
    assert result['poll']['asset']['clip_count'] == 23
    assert calls == []


def test_upload_submission_unknown_requires_reconciliation_before_retry(tmp_path):
    from orchestrator.repository import Repository
    from orchestrator.resource_registry import ResourceRegistry

    registry = ResourceRegistry(Repository(tmp_path / 'state.db'))
    registry.mark_submission_unknown('run-1', 'I-UPLOAD-001', 'upload', 'request timed out', case_run_id='cr-1')
    calls = []

    class Adapter:
        def ingest_file(self, payload):
            calls.append(payload)
            raise AssertionError('unknown submission must not be retried')

    result = run_upload_ingest(Adapter(), {
        'run_id': 'run-1', 'case_id': 'I-UPLOAD-001', 'case_run_id': 'cr-1',
        'chain_id': 'I-UPLOAD', 'payload': {'path': 'same.mp4'},
        'resource_registry': registry,
    }, tmp_path)
    assert result['test_status'] == 'inconclusive'
    assert result['failure']['code'] == 'RECONCILIATION_REQUIRED'
    assert calls == []

@pytest.mark.parametrize('name', ['clip.txt', 'clip.png'])
def test_ingest_file_rejects_non_video_extensions(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b'x')
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {'upload_file': 'POST /upload'}}})
    with pytest.raises(ValueError, match='supported video extension'):
        adapter.ingest_file(path)
