import httpx

from orchestrator.adapters import RemoteAdapter


def test_ingest_file_posts_confirmed_multipart_fields_without_remote_write(tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'video')
    seen = {}
    request = httpx.Request('POST', 'https://example.test/api/v1/assets/upload')
    response = httpx.Response(200, json={'asset_id': 'a1', 'status': 'processing', 'message': 'queued'}, request=request)

    def fake_request(method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs)
        return response

    monkeypatch.setattr('orchestrator.adapters.httpx.request', fake_request)
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {'upload_file': 'POST /assets/upload'}}})
    result = adapter.ingest_file(file_path=path, folder_id='folder', source_type='master_tape', title='Title')
    assert result == {'id': 'a1', 'status': 'processing', 'message': 'queued', 'asset_id': 'a1'}
    assert seen['method'] == 'POST'
    assert seen['url'].endswith('/api/v1/assets/upload')
    assert seen['kwargs']['data'] == {'folder_id': 'folder', 'source_type': 'master_tape', 'title': 'Title'}
    assert seen['kwargs']['files']['file'][0] == 'clip.mp4'


def test_asset_queries_unwrap_response_model_and_lists(monkeypatch):
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {
        'asset_detail': 'GET /assets/{asset_id}', 'asset_progress': 'GET /assets/{asset_id}/progress',
    }}})
    responses = iter([
        {'code': 0, 'data': {'id': 'a1', 'status': 'processing'}},
        {'data': [{'asset_id': 'a1', 'state': 'active'}]},
    ])
    monkeypatch.setattr(adapter, 'request', lambda *args, **kwargs: next(responses))
    assert adapter.get_asset('a1')['id'] == 'a1'
    assert adapter.get_asset_progress('a1')['asset_id'] == 'a1'


def test_asset_queries_accept_list_without_matching_id(monkeypatch):
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    monkeypatch.setattr(adapter, 'request', lambda *args, **kwargs: {'data': [{'id': 'a1', 'status': 'active'}]})
    assert adapter.get_asset('a1')['status'] == 'active'


def test_ingest_file_accepts_payload_dict_and_requires_local_file(tmp_path):
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'remote_api': {'routes': {'upload_file': 'POST /assets/upload'}}})
    try:
        adapter.ingest_file({'file_path': str(tmp_path / 'missing.mp4')})
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('missing upload file must be rejected locally')
