import httpx
import pytest

from orchestrator.adapters import RemoteAdapter, RemoteAuthError
from orchestrator.sanitization import REDACTED, sanitize_sensitive


def test_sanitize_sensitive_redacts_serialized_credentials():
    cases = (
        "{'password': 'super-secret'}",
        'password=super-secret',
        'Authorization: Bearer abc.def.ghi',
        'https://example.test/api?token=query-secret&x=1',
    )
    for value in cases:
        sanitized = sanitize_sensitive(value, {'super-secret', 'abc.def.ghi', 'query-secret'})
        assert 'super-secret' not in sanitized
        assert 'abc.def.ghi' not in sanitized
        assert 'query-secret' not in sanitized
        assert REDACTED in sanitized


def test_normal_http_error_redacts_all_auth_secrets_from_json_text_and_url(monkeypatch):
    secrets = {
        'access-token-secret': 'token',
        'refresh-token-secret': 'refresh',
        'password-secret': 'password',
        'identifier-secret': 'identifier',
        'email-secret@example.test': 'email',
    }
    for env_name, value in {
        'token': secrets['access-token-secret'],
        'refresh': secrets['refresh-token-secret'],
        'password': secrets['password-secret'],
        'identifier': secrets['identifier-secret'],
        'email': secrets['email-secret@example.test'],
    }.items():
        monkeypatch.setenv(env_name, value)
    calls = []
    def fake_request(method, url, **kwargs):
        calls.append(url)
        request_url = f'{url}?token={secrets["access-token-secret"]}&refresh_token={secrets["refresh-token-secret"]}'
        if len(calls) == 1:
            return response(
                method,
                request_url,
                422,
                json={
                    'message': f'password={secrets["password-secret"]}',
                    'identifier': secrets['identifier-secret'],
                    'nested': {'email': secrets['email-secret@example.test']},
                    'authorization': f'Bearer {secrets["access-token-secret"]}',
                },
            )
        return response(
            method,
            request_url,
            500,
            content=f'email={secrets["email-secret@example.test"]} password={secrets["password-secret"]}'.encode(),
        )
    monkeypatch.setattr('orchestrator.adapters.httpx.request', fake_request)
    adapter = RemoteAdapter({
        'target': {'base_url': 'https://example.test'},
        'auth': {
            'token_env': 'token',
            'refresh_token_env': 'refresh',
            'password_env': 'password',
            'identifier_env': 'identifier',
            'email_env': 'email',
        },
        'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}},
    })
    with pytest.raises(httpx.HTTPStatusError) as exc:
        adapter.get_asset('asset')
    detail = exc.value.remote_detail
    rendered = repr(detail)
    for value in secrets:
        assert value not in rendered
    assert detail['status'] == 422
    assert '[REDACTED]' in rendered


def response(method, url, status, **kwargs):
    return httpx.Response(status, request=httpx.Request(method, url), **kwargs)


def test_login_payload_and_tokens_saved(monkeypatch):
    calls = []
    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/auth/login'):
            return response(method, url, 200, json={'access_token': 'access', 'refresh_token': 'refresh'})
        return response(method, url, 200, json={'ok': True})
    monkeypatch.setattr('orchestrator.adapters.httpx.request', fake)
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'identifier_env': 'ID', 'password_env': 'PW'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    monkeypatch.setenv('ID', 'user@example.test'); monkeypatch.setenv('PW', 'password-secret')
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'identifier_env': 'ID', 'password_env': 'PW'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    assert adapter.request('asset_detail', params={'asset_id': 'a'}) == {'ok': True}
    assert calls[0][2]['json'] == {'identifier': 'user@example.test', 'password': 'password-secret'}
    assert adapter.token == 'access' and adapter.refresh_token == 'refresh'


def test_401_prefers_login_and_retries(monkeypatch):
    calls = []
    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/auth/login'):
            return response(method, url, 200, json={'access_token': 'new'})
        if len([c for c in calls if c[1].endswith('/assets/a')]) == 1:
            return response(method, url, 401, json={'code': 'expired'})
        return response(method, url, 200, json={'ok': True})
    monkeypatch.setenv('ID', 'user'); monkeypatch.setenv('PW', 'pw')
    monkeypatch.setattr('orchestrator.adapters.httpx.request', fake)
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'identifier_env': 'ID', 'password_env': 'PW'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}},)
    assert adapter.request('asset_detail', params={'asset_id': 'a'}) == {'ok': True}
    assert [c[1].rsplit('/api/v1/', 1)[-1] for c in calls] == ['auth/login', 'assets/a', 'auth/login', 'assets/a']


def test_login_failure_is_structured_and_does_not_expose_secret(monkeypatch):
    monkeypatch.setenv('ID', 'user'); monkeypatch.setenv('PW', 'super-secret')
    monkeypatch.setattr('orchestrator.adapters.httpx.request', lambda method, url, **kwargs: response(method, url, 401, json={'message': 'bad'}))
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'identifier_env': 'ID', 'password_env': 'PW'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    with pytest.raises(RemoteAuthError) as exc:
        adapter.request('asset_detail', params={'asset_id': 'a'})
    assert 'super-secret' not in str(exc.value)
    assert 'super-secret' not in repr(exc.value.detail)
    assert 'super-secret' not in repr(exc.value.retry)
    assert exc.value.detail['status'] == 401
    assert exc.value.detail['url'].endswith('/auth/login')
    assert exc.value.operation == 'auth_login'


def test_legacy_refresh_token_works_without_credentials(monkeypatch):
    monkeypatch.setenv('REFRESH', 'old-refresh')
    def fake(method, url, **kwargs):
        if url.endswith('/auth/refresh'):
            return response(method, url, 200, json={'access_token': 'new'})
        return response(method, url, 200, json={'ok': True})
    monkeypatch.setattr('orchestrator.adapters.httpx.request', fake)
    adapter = RemoteAdapter({'target': {'base_url': 'https://example.test'}, 'auth': {'refresh_token_env': 'REFRESH'}, 'remote_api': {'routes': {'asset_detail': 'GET /assets/{asset_id}'}}})
    assert adapter.request('asset_detail', params={'asset_id': 'a'}) == {'ok': True}
