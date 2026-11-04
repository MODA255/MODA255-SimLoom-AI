import time
from datetime import datetime, timezone
from .polling import build_poll_snapshot

_TERMINAL = {'completed', 'success', 'succeeded', 'failed', 'error', 'cancelled', 'canceled', 'stopped'}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def poll_task(adapter, task_id, *, timeout_seconds=3600, interval_seconds=20, sleep=time.sleep, clock=time.monotonic, on_event=None):
    """轮询一个远端任务并返回可复用的原始事件证据；不下载视频。"""
    started = clock()
    events = []
    while True:
        task = adapter.get_task(task_id)
        observed_at = utc_now()
        snapshot = build_poll_snapshot(task, observed_at)
        events.append(snapshot)
        if on_event:
            on_event(snapshot)
        status = str(snapshot.get('status') or '').lower()
        if status in _TERMINAL:
            return {'task': task, 'poll_events': events, 'poll_status': 'completed', 'poll_started_at': events[0]['observed_at'], 'poll_finished_at': events[-1]['observed_at']}
        if clock() - started >= timeout_seconds:
            return {'task': task, 'poll_events': events, 'poll_status': 'timeout', 'poll_started_at': events[0]['observed_at'], 'poll_finished_at': events[-1]['observed_at'], 'poll_error': 'POLL_TIMEOUT'}
        sleep(interval_seconds)
