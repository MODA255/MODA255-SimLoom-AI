import json
import os
from pathlib import Path
from urllib.parse import urlparse, quote
import httpx

from .sanitization import sanitize_sensitive


def http_error_detail(exc, *, secrets=()):
    """Return structured HTTP failure details with credentials recursively redacted."""
    response = getattr(exc, "response", None)
    if response is None:
        return sanitize_sensitive({"status": None, "url": None, "detail": str(exc)}, secrets)
    try:
        body = response.json()
    except Exception:
        body = response.text[:4000]
    return sanitize_sensitive(
        {"status": response.status_code, "url": str(response.request.url) if response.request is not None else None, "detail": body},
        secrets,
    )

class RemoteRouteError(RuntimeError):
    def __init__(self, operation, detail):
        super().__init__(f'REMOTE_ROUTE_UNCONFIGURED:{operation}' if detail == 'unconfigured route' else f'REMOTE_ROUTE_ERROR:{operation}:{detail}')
        self.operation, self.detail = operation, detail


class RemoteAuthError(RuntimeError):
    """Structured authentication failure after an optional refresh retry."""
    def __init__(self, operation, detail, retry=None, *, secrets=()):
        self.operation = operation
        self.detail = sanitize_sensitive(detail, secrets)
        self.retry = sanitize_sensitive(retry or {"attempted": False, "succeeded": False}, secrets)
        super().__init__(f"REMOTE_AUTH_ERROR:{operation}")


def _auth_retry(operation, response, *, attempted=False, succeeded=False, secrets=()):
    detail = http_error_detail(httpx.HTTPStatusError(
        "remote authentication failed", request=response.request, response=response,
    ), secrets=secrets)
    return RemoteAuthError(operation, detail, {
        "attempted": attempted,
        "succeeded": succeeded,
        "original_status": response.status_code,
        "original_url": detail.get("url"),
        "original_detail": detail.get("detail"),
        "operation": operation,
    })

def _unwrap_response(value):
    """Unwrap common API envelopes without assuming one response model.

    The backend's ``ResponseModel`` uses ``data`` and some deployments return
    ``result``/``item`` or a one-element list.  Keep the adapter boundary
    stable for callers that consume asset dictionaries.
    """
    current = value
    for _ in range(4):
        if isinstance(current, dict):
            nested = next((current[key] for key in ('data', 'result', 'item', 'asset') if key in current), None)
            if nested is None or nested is current:
                return current
            current = nested
            continue
        return current
    return current


def _asset_response(value, asset_id=None):
    value = _unwrap_response(value)
    if isinstance(value, list):
        if asset_id is not None:
            wanted = str(asset_id)
            for item in value:
                if isinstance(item, dict) and str(item.get('id') or item.get('asset_id') or item.get('resource_id')) == wanted:
                    return item
        return next((item for item in value if isinstance(item, dict)), {})
    return value if isinstance(value, dict) else {}


class RemoteAdapter:
    """Configured remote MatrixGen HTTP adapter; paths and mappings stay configurable."""
    def __init__(self, config):
        self.config = config.data if hasattr(config, 'data') else config
        target = self.config.get('target', {})
        # Write permission is opt-in and may be declared at either config
        # level for backwards compatibility.  Missing values are denied.
        self.safe_write = (
            self.config.get('safe_write')
            if 'safe_write' in self.config
            else target.get('safe_write', False)
        )
        self.base_url = target.get('base_url', '').rstrip('/')
        self.api_prefix = target.get('api_prefix', '/api/v1').strip('/')
        self.allowed_hosts = set(target.get('allowed_hosts', []))
        self.timeout = self.config.get('timeouts', {}).get('http_response_seconds', 60)
        auth = self.config.get('auth', {})
        self.token = os.getenv(auth.get('token_env') or auth.get('matrixgen_token_env', 'MATRIXGEN_TOKEN'), '')
        self.refresh_token = os.getenv(auth.get('refresh_token_env') or auth.get('matrixgen_refresh_token_env', 'MATRIXGEN_REFRESH_TOKEN'), '')
        self.identifier = os.getenv(auth.get('identifier_env', 'MATRIXGEN_IDENTIFIER'), '')
        self.email = os.getenv(auth.get('email_env', 'MATRIXGEN_EMAIL'), '')
        self.password = os.getenv(auth.get('password_env', 'MATRIXGEN_PASSWORD'), '')

        credentials = getattr(config, 'credentials', {})
        self.token = credentials.get('token') or self.token
        self.refresh_token = credentials.get('refresh_token') or self.refresh_token
        self.identifier = credentials.get('identifier') or self.identifier
        self.email = credentials.get('email') or self.email
        self.password = credentials.get('password') or self.password
        self._secrets = (self.token, self.refresh_token, self.identifier, self.email, self.password)

    def __getattribute__(self, name):
        if name == 'ingest_file':
            config = object.__getattribute__(self, '__dict__').get('config', {})
            routes = config.get('remote_api', {}).get('routes', {})
            if not routes.get('upload_file') and not routes.get('ingest_file'):
                return None
        return object.__getattribute__(self, name)

    def _route(self, name):
        raw = self.config.get('remote_api', {}).get('routes', {}).get(name)
        if not raw:
            return None
        if isinstance(raw, (list, tuple)):
            if len(raw) != 2:
                raise RemoteRouteError(name, 'invalid route shape')
            return str(raw[0]).upper(), str(raw[1])
        try:
            method, path = str(raw).split(' ', 1)
        except ValueError as exc:
            raise RemoteRouteError(name, 'invalid route shape') from exc
        return method.upper(), path

    def _url(self, path):
        url = f'{self.base_url}/{self.api_prefix}/{path.lstrip("/")}'
        host = urlparse(url).hostname
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise RuntimeError('REMOTE_HOST_NOT_ALLOWED')
        return url

    def _headers(self):
        return {'Accept': 'application/json', 'Authorization': f'Bearer {self.token}'} if self.token else {'Accept': 'application/json'}

    def _login(self):
        if not self.password or not (self.identifier or self.email):
            return False
        payload = {'password': self.password}
        payload['identifier' if self.identifier else 'email'] = self.identifier or self.email
        secrets = (self.token, self.refresh_token, self.password, self.identifier, self.email)
        try:
            response = httpx.request('POST', self._url('/auth/login'), json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            detail = http_error_detail(exc, secrets=secrets) if isinstance(exc, httpx.HTTPStatusError) else sanitize_sensitive({'status': getattr(getattr(exc, 'response', None), 'status_code', None), 'url': None, 'detail': str(exc)}, secrets)
            raise RemoteAuthError('auth_login', detail, {'attempted': True, 'succeeded': False, 'original_status': detail.get('status'), 'original_url': detail.get('url'), 'original_detail': detail.get('detail')}, secrets=secrets) from exc
        if not body.get('access_token'):
            detail = {'status': response.status_code, 'url': str(response.request.url) if response.request is not None else None, 'detail': 'access_token missing'}
            raise RemoteAuthError('auth_login', detail, {'attempted': True, 'succeeded': False, 'original_status': detail['status'], 'original_url': detail['url'], 'original_detail': detail['detail']}, secrets=secrets)
        self.token = body['access_token']
        self.refresh_token = body.get('refresh_token', self.refresh_token)
        return True

    def _authenticate(self):
        if self.token:
            return True
        return self._login()

    def _refresh(self):
        if not self.refresh_token:
            return False
        method, path = ('POST', '/auth/refresh')
        try:
            response = httpx.request(method, self._url(path), json={'refresh_token': self.refresh_token}, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            detail = getattr(exc, 'remote_detail', None) or http_error_detail(exc, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email)) if isinstance(exc, httpx.HTTPStatusError) else sanitize_sensitive({'status': getattr(getattr(exc, 'response', None), 'status_code', None), 'url': None, 'detail': str(exc)}, (self.token, self.refresh_token, self.password, self.identifier, self.email))
            raise RemoteAuthError('auth_refresh', detail, {'attempted': True, 'succeeded': False, 'original_status': detail.get('status'), 'original_url': detail.get('url'), 'original_detail': detail.get('detail')}, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email)) from exc
        if not body.get('access_token'):
            detail = {'status': response.status_code, 'url': str(response.request.url) if response.request is not None else None, 'detail': 'access_token missing'}
            raise RemoteAuthError('auth_refresh', detail, {'attempted': True, 'succeeded': False, 'original_status': detail['status'], 'original_url': detail['url'], 'original_detail': detail['detail']}, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email))
        self.token = body['access_token']
        self.refresh_token = body.get('refresh_token', self.refresh_token)
        return True

    def request(self, operation, payload=None, params=None, response_type='json'):
        route = self._route(operation)
        if not route:
            raise RemoteRouteError(operation, 'unconfigured route')
        if not self.token:
            try:
                self._authenticate()
            except RemoteAuthError:
                raise
        try:
            method, path = route
        except (TypeError, ValueError) as exc:
            raise RemoteRouteError(operation, 'invalid route shape') from exc
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'} or not path:
            raise RemoteRouteError(operation, 'invalid method or path')
        path_params = {}
        for key, value in (params or {}).items():
            marker = '{' + key + '}'
            if marker in path:
                path = path.replace(marker, quote(str(value), safe=''))
                path_params[key] = value
        if '{' in path or '}' in path:
            raise RemoteRouteError(operation, 'missing path parameters')
        params = {k: v for k, v in (params or {}).items() if k not in path_params}
        kwargs = {'headers': self._headers(), 'timeout': self.timeout}
        if method in {'POST', 'PUT', 'PATCH'}:
            kwargs['json'] = payload or {}
        else:
            kwargs['params'] = params or payload or {}
        response = httpx.request(method, self._url(path), **kwargs)
        if response.status_code == 401:
            try:
                refreshed = self._login() if self.password and (self.identifier or self.email) else self._refresh()
            except RemoteAuthError as exc:
                exc.retry.update({'operation': operation, 'original_status': 401})
                raise
            if not refreshed:
                raise _auth_retry(operation, response, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email))
            if refreshed:
                kwargs['headers'] = self._headers()
                response = httpx.request(method, self._url(path), **kwargs)
                if response.status_code == 401:
                    raise _auth_retry(operation, response, attempted=True, succeeded=False, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email))
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.remote_detail = http_error_detail(
                exc,
                secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email),
            )
            raise
        return response.content if response_type == 'bytes' else response.json()

    def _automation_route(self, name):
        raw = self.config.get('automation_routes', {}).get(name)
        if not raw:
            return None
        if isinstance(raw, (list, tuple)):
            if len(raw) != 2: raise RemoteRouteError(name, 'invalid route shape')
            return str(raw[0]).upper(), str(raw[1])
        try: method, path = str(raw).split(' ', 1)
        except ValueError as exc: raise RemoteRouteError(name, 'invalid route shape') from exc
        return method.upper(), path

    def automation_request(self, operation, params=None):
        route = self._automation_route(operation)
        if not route: raise RemoteRouteError(operation, 'unconfigured route')
        method, path = route
        if method != 'GET': raise RemoteRouteError(operation, 'read-only route required')
        return self._request_route(method, path, params=params)

    def _request_route(self, method, path, params=None):
        path_params = {}
        for key, value in (params or {}).items():
            marker = '{' + key + '}'
            if marker in path: path = path.replace(marker, quote(str(value), safe='')); path_params[key] = value
        if '{' in path or '}' in path: raise RemoteRouteError('automation', 'missing path parameters')
        response = httpx.request(method, self._url(path), headers=self._headers(), params={k:v for k,v in (params or {}).items() if k not in path_params}, timeout=self.timeout)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.remote_detail = http_error_detail(
                exc,
                secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email),
            )
            raise
        return response.json()

    def diagnose_route(self, operation, *, params=None):
        """Probe a write route without sending its write method or payload.

        GET is used against the POST route so the server returns its real
        method/validation error without creating an asset.  The returned object
        always contains status, URL and response detail.
        """
        route = self._route(operation)
        if not route:
            raise RemoteRouteError(operation, 'unconfigured route')
        _, path = route
        path_params = {}
        for key, value in (params or {}).items():
            marker = '{' + key + '}'
            if marker in path:
                path = path.replace(marker, quote(str(value), safe=''))
                path_params[key] = value
        if '{' in path or '}' in path:
            raise RemoteRouteError(operation, 'missing path parameters')
        query = {k: v for k, v in (params or {}).items() if k not in path_params}
        url = self._url(path)
        try:
            response = httpx.request('GET', url, headers=self._headers(), params=query, timeout=self.timeout)
            detail = http_error_detail(
                httpx.HTTPStatusError('route diagnostic', request=response.request, response=response),
                secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email),
            ) if response.status_code >= 400 else None
            return {'operation': operation, 'method': route[0], 'diagnostic_method': 'GET', 'status': response.status_code, 'url': str(response.request.url) if response.request else url, 'detail': detail, 'allow': response.headers.get('allow')}
        except Exception as exc:
            detail = getattr(exc, 'remote_detail', None) or http_error_detail(
                exc,
                secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email),
            )
            return {'operation': operation, 'method': route[0], 'diagnostic_method': 'GET', 'status': detail.get('status'), 'url': detail.get('url') or url, 'detail': detail.get('detail')}

    def get_automation_task(self, task_id): return self.automation_request('get_task', {'task_id': task_id})
    def probe_automation_tasks(self): return self.automation_request('list_tasks')

    def probe_capabilities(self):
        return self.request('vfc_config')

    def ingest_url(self, payload): return self.request('import_url', payload)
    import_url = ingest_url

    def ingest_file(self, file_path=None, folder_id=None, source_type=None, title=None, **kwargs):
        """Upload a local file using the confirmed multipart assets endpoint.

        This method only performs the request when explicitly called; constructing
        an adapter or probing capabilities never writes remotely.
        """
        if isinstance(file_path, dict):
            payload = dict(file_path)
            file_path = payload.pop('file_path', payload.pop('path', None))
            folder_id = payload.pop('folder_id', folder_id)
            source_type = payload.pop('source_type', source_type)
            title = payload.pop('title', title)
            kwargs = {**payload, **kwargs}
        if not file_path:
            raise ValueError('file_path is required')
        if not isinstance(file_path, (str, os.PathLike)):
            raise TypeError('file_path must be a local filesystem path')
        raw_path = os.fspath(file_path)
        if '\x00' in raw_path or '://' in raw_path:
            raise ValueError('file_path must be a local filesystem path')
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ValueError('file_path cannot be resolved') from exc
        if not resolved.is_file():
            raise FileNotFoundError(str(path))
        allowed_roots = self.config.get('local_input_roots') or self.config.get('input_roots')
        if allowed_roots:
            roots = [Path(root).expanduser().resolve() for root in allowed_roots]
            if not any(resolved == root or root in resolved.parents for root in roots):
                raise ValueError('file_path is outside allowed local input roots')
        allowed_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.mpeg', '.mpg', '.wmv', '.flv', '.3gp', '.ts'}
        if resolved.suffix.lower() not in allowed_extensions:
            raise ValueError('file_path must use a supported video extension')
        path = resolved
        operation = 'upload_file' if self._route('upload_file') else 'ingest_file'
        route = self._route(operation)
        if not route:
            raise RemoteRouteError(operation, 'unconfigured route')
        method, route_path = route
        if method != 'POST':
            raise RemoteRouteError(operation, 'write route must be POST')
        fields = {key: value for key, value in {
            'folder_id': folder_id, 'source_type': source_type, 'title': title,
            **kwargs,
        }.items() if value is not None}
        with path.open('rb') as stream:
            response = httpx.request(
                'POST', self._url(route_path), headers=self._headers(),
                files={'file': (path.name, stream)}, data=fields, timeout=self.timeout,
            )
        if response.status_code == 401:
            try:
                refreshed = self._login() if self.password and (self.identifier or self.email) else self._refresh()
            except RemoteAuthError as exc:
                exc.retry.update({'operation': operation, 'original_status': 401})
                raise
            if not refreshed:
                raise _auth_retry(operation, response, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email))
            if refreshed:
                with path.open('rb') as stream:
                    response = httpx.request(
                        'POST', self._url(route_path), headers=self._headers(),
                        files={'file': (path.name, stream)}, data=fields, timeout=self.timeout,
                    )
                    if response.status_code == 401:
                        raise _auth_retry(operation, response, attempted=True, succeeded=False, secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email))
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.remote_detail = http_error_detail(
                exc,
                secrets=(self.token, self.refresh_token, self.password, self.identifier, self.email),
            )
            raise
        body = _asset_response(response.json())
        if not body:
            raise TypeError('upload response must contain an asset object')
        return {
            'id': body.get('id') or body.get('asset_id') or body.get('resource_id'),
            'status': body.get('status') or body.get('state'),
            'message': body.get('message'),
            **body,
        }
    def list_assets(self, folder_id):
        return _unwrap_response(self.request('list_assets', params={'folder_id': folder_id}))
    def get_asset(self, asset_id):
        return _asset_response(self.request('asset_detail', params={'asset_id': asset_id}), asset_id)
    def get_asset_progress(self, asset_id):
        return _asset_response(self.request('asset_progress', params={'asset_id': asset_id}), asset_id)
    def get_asset_progress_phases(self, asset_id):
        return _asset_response(self.request('asset_progress_phases', params={'asset_id': asset_id}), asset_id)
    progress_phases = get_asset_progress_phases
    def create_vfc_task(self, payload): return self.request('create_vfc_task', payload)
    def create_daily_task(self, kind, payload, execute=False):
        if not execute: raise RuntimeError('EXECUTE_REQUIRED')
        routes = {'ecom_mix': ('automation_task', '/automation/tasks'), 'theme_discovery': ('automation_task', '/automation/tasks'), 'ip_promo': ('ip_promo', '/ip-promo-automations')}
        if kind not in routes: raise RemoteRouteError(kind, 'unsupported automation kind')
        operation, path = routes[kind]
        configured = self.config.get('automation_routes', {}).get(operation) or self.config.get('automation_routes', {}).get(kind)
        if configured:
            method, configured_path = self._automation_route(operation if self.config.get('automation_routes', {}).get(operation) else kind)
        else:
            method, configured_path = 'POST', path
        if method != 'POST': raise RemoteRouteError(kind, 'write route must be POST')
        return self._request_write_route(method, configured_path, payload)
    def _request_write_route(self, method, path, payload):
        response = httpx.request(method, self._url(path), headers=self._headers(), json=payload or {}, timeout=self.timeout)
        response.raise_for_status(); return response.json()
    def get_daily_task(self, kind, task_id):
        operation = {'ecom_mix': 'get_task', 'theme_discovery': 'get_task', 'ip_promo': 'get_ip_promo_task'}.get(kind, 'get_task')
        route = self.config.get('automation_routes', {}).get(operation)
        if route:
            method, path = self._route_from_config(operation, route)
            if method != 'GET': raise RemoteRouteError(operation, 'read-only route required')
            path = path.replace('{task_id}', quote(str(task_id), safe=''))
            return self._request_route(method, path, params={})
        return self.request(operation, params={'task_id': task_id})
    def get_task(self, task_id): return self.request('get_vfc_task', params={'task_id': task_id})
    def get_phases(self, task_id): return self.request('get_vfc_phases', params={'task_id': task_id})
    def get_stage_snapshot(self, kind, task_id):
        operation = 'get_ip_promo_phases' if kind == 'ip_promo' else 'get_automation_stages'
        route = self.config.get('automation_routes', {}).get(operation)
        if not route: raise RemoteRouteError(operation, 'unconfigured route')
        method, path = self._route_from_config(operation, route)
        if method != 'GET': raise RemoteRouteError(operation, 'read-only route required')
        path = path.replace('{task_id}', quote(str(task_id), safe=''))
        return self._request_route(method, path, params={})
    def cleanup_resource(self, kind, resource_id):
        routes = self.config.get('cleanup_routes', {})
        route = routes.get(kind)
        if not route: raise RemoteRouteError(kind, 'cleanup route unconfigured')
        method, path = self._route_from_config(kind, route)
        if method not in {'DELETE', 'POST'}: raise RemoteRouteError(kind, 'cleanup route must be DELETE or POST')
        path = path.replace('{id}', quote(str(resource_id), safe=''))
        response = httpx.request(method, self._url(path), headers=self._headers(), timeout=self.timeout)
        if response.status_code in {404, 410}: return {'status':'already_absent'}
        response.raise_for_status()
        return response.json() if response.content else {'status':'deleted'}
    def _route_from_config(self, name, raw):
        if isinstance(raw, (list, tuple)):
            if len(raw) != 2: raise RemoteRouteError(name, 'invalid route shape')
            return str(raw[0]).upper(), str(raw[1])
        try: method, path = str(raw).split(' ', 1)
        except ValueError as exc: raise RemoteRouteError(name, 'invalid route shape') from exc
        return method.upper(), path
    def get_videos(self, task_id): return self.request('get_vfc_videos', params={'task_id': task_id})
    def download_video(self, task_id, video_id): return self.request('download_vfc_video', params={'task_id': task_id, 'video_id': video_id}, response_type='bytes')
    download = download_video

    def get_video_metadata(self, task_id, video_id):
        """Return remote video metadata without downloading the media file."""
        videos = self.get_videos(task_id)
        items = videos if isinstance(videos, list) else (videos.get('items', []) if isinstance(videos, dict) else [])
        return next((item for item in items if str(item.get('id') or item.get('video_id')) == str(video_id)), None)

    def get_evidence(self, task_id):
        return self.request('get_vfc_evidence', params={'task_id': task_id})

    def get_diagnostics(self, task_id, names):
        result = {}
        for name in names or []:
            try:
                result[name] = {"status": 200, "content": self.request("get_vfc_diagnostic", params={"task_id": task_id, "name": name})}
            except Exception as exc:
                result[name] = {"status": getattr(getattr(exc, "response", None), "status_code", None) or 0, "error": str(exc)}
        return result

    def get_node(self, task_id, node_key):
        return self.request('get_vfc_node', params={'task_id': task_id, 'node_key': node_key})

    def get_transcript(self, task_id, video_id):
        """Fetch transcript JSON from the remote transcript URL, never downloading the video."""
        video = self.get_video_metadata(task_id, video_id) or {}
        url = video.get('transcript_oss_url')
        if not url:
            return video.get('transcript_json')
        parsed = urlparse(str(url))
        if parsed.scheme != 'https' or (self.allowed_hosts and parsed.hostname not in self.allowed_hosts and not parsed.hostname.endswith('.aliyuncs.com')):
            raise RuntimeError('TRANSCRIPT_HOST_NOT_ALLOWED')
        response = httpx.get(str(url), headers={'Accept': 'application/json'}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
