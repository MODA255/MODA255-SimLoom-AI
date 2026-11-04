import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone


_SENSITIVE = {'token', 'cookie', 'authorization', 'auth', 'secret', 'access_token', 'refresh_token'}
_TERMINAL = {'completed', 'failed', 'cancelled', 'success', 'error'}


def _sanitize(value, key=''):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if any(s in k.lower() for s in _SENSITIVE) else _sanitize(v, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, key) for v in value]
    if isinstance(value, str) and re.search(r'(?i)(bearer\s+|(?:access[_-]?token|refresh[_-]?token|authorization|cookie|secret|password)\s*[:=])', value):
        return '[REDACTED]'
    return value


def _utc(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ResourceRegistry:
    """Durable local registry; it never deletes remote resources."""
    def __init__(self, repository, cleanup_days=7):
        if isinstance(cleanup_days, bool) or not isinstance(cleanup_days, int) or cleanup_days < 0:
            raise ValueError('cleanup_days must be a non-negative integer')
        self.repository = repository
        self.cleanup_days = cleanup_days

    def register_pending(self, run_id, case_id, resource_type, *, metadata=None, case_run_id=None, operation_key=None, payload=None):
        if not case_run_id:
            raise ValueError('case_run_id is required')
        record = self.repository.get_resource_by_key(run_id, case_id, resource_type)
        if record:
            if record['case_run_id'] != case_run_id:
                raise ValueError('case_run_id cannot be changed')
            return record
        now = datetime.now(timezone.utc)
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if payload is not None else None
        return self.repository.insert_resource({
            'id': 'resource-' + uuid.uuid4().hex[:12], 'run_id': run_id, 'case_id': case_id,
            'case_run_id': case_run_id, 'resource_type': resource_type, 'resource_id': None,
            'status': 'submission_pending', 'created_at': now.isoformat(),
            'cleanup_after': (now + timedelta(days=self.cleanup_days)).date().isoformat(),
            'protected': True, 'protection_reason': 'submission_pending', 'terminal_status': None,
            'cleanup_status': 'pending', 'operation_key': operation_key, 'payload_hash': payload_hash,
            'metadata': _sanitize(metadata or {}),
        })

    def mark_submitted(self, record_id, resource_id, *, created_at=None, terminal_status=None):
        if not resource_id: raise ValueError('resource_id is required')
        record = next(r for r in self.repository.list_resources() if r['id'] == record_id)
        if record['status'] == 'registered':
            if record['resource_id'] == resource_id:
                return record
            raise ValueError('resource_id cannot be overwritten')
        if record['resource_id'] and record['resource_id'] != resource_id:
            raise ValueError('resource_id cannot be overwritten')
        if record['status'] == 'submission_unknown':
            raise ValueError('submission_unknown requires explicit reconcile')
        created = created_at or record['created_at']
        cleanup_after = (_utc(created) + timedelta(days=self.cleanup_days)).isoformat()
        updates = {'resource_id': resource_id, 'status': 'registered', 'protected': False, 'protection_reason': None, 'cleanup_after': cleanup_after, 'terminal_status': terminal_status}
        if created_at is not None:
            updates['created_at'] = _utc(created_at).isoformat()
        return self.repository.update_resource(record_id, updates)

    def mark_submission_unknown(self, run_id, case_id, resource_type, reason, *, case_run_id=None):
        if not case_run_id:
            raise ValueError('case_run_id is required')
        record = self.register_pending(run_id, case_id, resource_type, case_run_id=case_run_id)
        if record['status'] == 'registered':
            raise ValueError('registered resource cannot become submission_unknown')
        return self.repository.update_resource(record['id'], {'status': 'submission_unknown', 'protected': True, 'protection_reason': reason or 'submission outcome unknown'})

    def reconcile(self, record_id, resource_id, *, terminal_status=None, created_at=None, evidence=None):
        if not resource_id:
            raise ValueError('resource_id is required')
        record = next((r for r in self.repository.list_resources() if r['id'] == record_id), None)
        if record is None:
            raise KeyError(record_id)
        if record['resource_id'] and record['resource_id'] != resource_id:
            raise ValueError('resource_id cannot be overwritten')
        if record['status'] != 'submission_unknown':
            raise ValueError('record is not submission_unknown')
        updates = {'resource_id': resource_id, 'status': 'registered', 'protected': False, 'protection_reason': 'reconciled', 'terminal_status': terminal_status, 'cleanup_after': (_utc(created_at or record['created_at']) + timedelta(days=self.cleanup_days)).isoformat()}
        if created_at is not None: updates['created_at'] = _utc(created_at).isoformat()
        if evidence is not None:
            updates['metadata'] = {**record['metadata'], 'reconcile_evidence': _sanitize(evidence)}
        return self.repository.update_resource(record_id, updates)

    def mark_terminal(self, record_id, terminal_status, *, created_at=None):
        if terminal_status not in _TERMINAL: raise ValueError('terminal_status must be a terminal state')
        record = next(r for r in self.repository.list_resources() if r['id'] == record_id)
        created = created_at or record['created_at']
        return self.repository.update_resource(record_id, {'terminal_status': terminal_status, 'cleanup_after': (_utc(created) + timedelta(days=self.cleanup_days)).isoformat()})

    def list_resources(self, run_id=None): return self.repository.list_resources(run_id)

    def cleanup_candidates(self, now=None):
        at = _utc(now)
        return [
            item for item in self.list_resources()
            if item['resource_id'] and not item['protected']
            and item['terminal_status'] in _TERMINAL
            and _utc(item['cleanup_after']) <= at
            and item['cleanup_status'] != 'submitted'
            and (not item['cleanup_next_retry_at'] or _utc(item['cleanup_next_retry_at']) <= at)
        ]

    def record_cleanup_error(self, record_id, error, *, next_retry_at=None):
        record = self.repository.get_resource(record_id)
        return self.repository.update_resource(record_id, {'cleanup_status': 'error', 'cleanup_retry_count': record['cleanup_retry_count'] + 1, 'cleanup_last_error': str(error), 'cleanup_next_retry_at': _utc(next_retry_at).isoformat() if next_retry_at else None})

    def mark_cleanup_submitted(self, record_id):
        return self.repository.update_resource(record_id, {'cleanup_status': 'submitted'})

    def export(self): return json.dumps(self.list_resources(), ensure_ascii=False, indent=2, sort_keys=True)
    export_registry = export
