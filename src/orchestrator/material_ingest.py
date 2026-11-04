"""Resolve asynchronous import acknowledgements without fuzzy asset matching."""
from copy import deepcopy
import json
import time
from dataclasses import asdict, dataclass
from typing import Any
from .policy import sanitize


@dataclass(frozen=True)
class MaterialRecord:
    source_url: str | None
    asset_id: str
    folder_id: str | None

    status: str
    progress: Any = None
    clip_count: int = 0
    asr: Any = None
    ocr: Any = None
    vector: Any = None
    tags: list | None = None

    @classmethod
    def from_asset(cls, asset, progress=None, folder=None):
        asset = dict(asset or {})
        raw_status = str(asset.get('status', '')).lower()
        status = 'ready' if raw_status in {'active', 'ready'} else (raw_status or 'unknown')
        normalized_progress = progress if progress is not None else asset.get('progress')
        if normalized_progress is None: normalized_progress = {'percent': 100} if status == 'ready' else {'percent': 0}
        return cls(asset.get('source_url') or asset.get('url'), str(asset.get('asset_id') or asset.get('id') or ''), folder or asset.get('folder_id'), status, normalized_progress, int(asset.get('clip_count') or 0), asset.get('asr'), asset.get('ocr'), asset.get('vector'), list(asset.get('tags') or []))

    @property
    def folder(self): return self.folder_id

    @property
    def title(self): return None
    @property
    def source_type(self): return None
    def to_dict(self): return asdict(self)


def normalize_material_record(asset, source_type='link', progress=None, folder=None):
    return MaterialRecord.from_asset(asset, progress=progress, folder=folder)


class MaterialIngestError(RuntimeError):
    def __init__(self, code, message, evidence=None):
        super().__init__(code)
        self.code = code
        self.detail = message
        self.evidence = sanitize(evidence or [])


def _items(value):
    if isinstance(value, dict):
        for key in ('items', 'assets', 'data', 'result', 'results'):
            if key in value:
                return _items(value[key])
        return []
    return value if isinstance(value, list) else []


def _asset_dict(value, asset_id=None):
    """Normalize ResponseModel/object/list responses at the ingest boundary."""
    current = value
    for _ in range(4):
        if isinstance(current, dict):
            nested = next((current[key] for key in ('data', 'result', 'item', 'asset') if key in current), None)
            if nested is None or nested is current:
                return current
            current = nested
        elif isinstance(current, list):
            wanted = str(asset_id) if asset_id is not None else None
            match = next((item for item in current if isinstance(item, dict) and (wanted is None or str(item.get('id') or item.get('asset_id')) == wanted)), None)
            current = match or next((item for item in current if isinstance(item, dict)), {})
        else:
            return {}
    return current if isinstance(current, dict) else {}


def ingest_and_wait_ready(adapter, payload, folder_id, source_url=None, title=None, timeout=1800, poll=20, operation_key=None, operation_store=None, progress_callback=None):
    if timeout < 0 or poll < 0 or not folder_id:
        raise MaterialIngestError('INVALID_INPUT', 'folder and nonnegative timeout/poll required')
    payload = deepcopy(payload)
    source_url = source_url or payload.get('url')
    title = title or payload.get('title')
    if not source_url and not title:
        raise MaterialIngestError('INVALID_INPUT', 'source URL or exact title required')
    if payload.get('folder_id') not in (None, folder_id):
        raise MaterialIngestError('INVALID_INPUT', 'folder differs from payload')
    payload['folder_id'] = folder_id
    evidence = []
    def report(step, **snapshot):
        if progress_callback:
            progress_callback({"step": step, **snapshot})
    store = operation_store if operation_store is not None else {}
    record = store.get(operation_key) if operation_key else None
    if record and isinstance(record, str):
        try: record = json.loads(record)
        except (TypeError, ValueError):
            # Legacy bare sentinel means submission was already attempted;
            # reconcile it through asset discovery without posting again.
            record = {'status': record, 'payload': payload}
    if record and record.get('payload') != payload:
        raise MaterialIngestError('OPERATION_KEY_CONFLICT', 'key already used with different payload')
    if record and record.get('status') == 'ready':
        asset = deepcopy(record['asset'])
        return {'status': 'ready', 'asset': asset, 'record': normalize_material_record(asset, folder=folder_id), 'evidence': [{'operation': 'idempotent_reuse', 'operation_key': operation_key}]}
    started = time.monotonic()
    if not record:
        candidate = {'status': 'submission_unknown', 'payload': payload}
        if operation_key and hasattr(store, 'reserve'):
            _, record = store.reserve(operation_key, candidate)
            if record and record.get('payload') != payload:
                raise MaterialIngestError('OPERATION_KEY_CONFLICT', 'key already used with different payload')
        else:
            record = candidate
            if operation_key: store[operation_key] = record
        if record.get('status') == 'submission_unknown':
            try:
                accepted = adapter.ingest_url(payload)
            except Exception as exc:
                detail = getattr(exc, "remote_detail", None) or {"status": getattr(getattr(exc, "response", None), "status_code", None), "url": None, "detail": str(exc)}
                observation = {"operation": "ingest_url", "status": detail.get("status"), "url": detail.get("url"), "detail": detail.get("detail")}
                raise MaterialIngestError('SUBMISSION_UNKNOWN', type(exc).__name__, evidence + [observation]) from exc
            record.update(status='accepted', response=accepted)
            if operation_key: store[operation_key] = record
            evidence.append({'operation': 'ingest_url', 'response': sanitize(accepted)})
        else:
            evidence.append({'operation': 'reconcile_without_resubmission', 'operation_key': operation_key})
    else:
        evidence.append({'operation': 'reconcile_without_resubmission', 'operation_key': operation_key})
    accepted = record.get('response', {})
    accepted = _asset_dict(accepted)
    asset_id = record.get('asset_id') or accepted.get('id') or accepted.get('asset_id')
    while True:
        if not asset_id:
            candidates = _items(adapter.list_assets(folder_id)); matches = {}
            for asset in candidates:
                if asset.get('folder_id') not in (None, folder_id): continue
                url = asset.get('source_url') or asset.get('url')
                if asset.get('id') or asset.get('asset_id'):
                    if (source_url and url == source_url) or (title and asset.get('title') == title and not url):
                        matches[asset.get('id') or asset.get('asset_id')] = asset
            observation = {'operation': 'list_assets', 'folder_id': folder_id, 'candidate_count': len(candidates), 'match_ids': list(matches)}
            evidence.append(observation)
            report('list_assets', **observation)
            if len(matches) > 1: raise MaterialIngestError('AMBIGUOUS_MATCH', 'multiple exact matches; refusing selection', evidence)
            if matches: asset_id = next(iter(matches)); record['asset_id'] = asset_id
        if asset_id:
            asset = _asset_dict(adapter.get_asset(asset_id), asset_id)
            if (asset.get('id') or asset.get('asset_id')) != asset_id: raise MaterialIngestError('ASSET_ID_MISMATCH', 'detail did not confirm selected ID', evidence)
            progress = _asset_dict(adapter.get_asset_progress(asset_id), asset_id)
            observation = {'operation': 'asset_status', 'asset_id': asset_id, 'status': asset.get('status'), 'progress': sanitize(progress)}
            evidence.append(observation)
            report('asset_status', **observation)
            states = {str(x).lower() for x in (asset.get('status'), progress.get('status'), progress.get('state')) if x}
            if states & {'failed', 'error', 'rejected'}:
                record['status'] = 'failed'; raise MaterialIngestError('ASSET_FAILED', 'asset reported failure', evidence)
            if states & {'active', 'ready'}:
                asset = {**asset, 'asset_id': asset_id}; record.update(status='ready', asset=deepcopy(asset))
                return {'status': 'ready', 'asset': asset, 'record': normalize_material_record(asset, progress=progress, folder=folder_id), 'evidence': sanitize(evidence)}
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            # An accepted submission without a returned ID is not invalid input:
            # discovery itself exhausted its deadline and must remain inconclusive.
            raise MaterialIngestError('ASSET_TIMEOUT' if asset_id else 'NO_UNIQUE_MATCH', 'readiness or asset discovery deadline exceeded', evidence)
        time.sleep(min(poll, remaining))
