from .runner import utc_now


def classify_execution_result(chain_id, *, created=None, lifecycle=None, error=None, blocked_reason=None, expected_video_count=1):
    lifecycle = lifecycle or {}; created = created or {}; task = lifecycle.get('task') or {}
    if blocked_reason:
        return {'chain_id': chain_id, 'execution_status': 'not_started', 'test_status': 'blocked', 'task_id': None, 'blocked': {'code': blocked_reason}, 'failure': None}
    status = str(task.get('status') or '').lower()
    videos = lifecycle.get('videos') or []
    if error:
        execution_status = 'timeout' if 'timeout' in str(error).lower() else 'failed'
        return {'chain_id': chain_id, 'execution_status': execution_status, 'test_status': 'failed', 'task_id': created.get('id') or created.get('task_id'), 'failure': {'code': type(error).__name__, 'message': str(error), 'last_status': status, 'last_phase': task.get('current_phase')}, 'blocked': None}
    final = 'completed' if status in {'completed', 'success', 'succeeded'} else 'timeout' if lifecycle.get('poll_status') == 'timeout' else 'failed'
    passed = final == 'completed' and len(videos) >= expected_video_count
    return {'chain_id': chain_id, 'execution_status': final, 'test_status': 'passed' if passed else 'failed', 'task_id': created.get('id') or created.get('task_id'), 'failure': None if passed else {'code': 'OUTPUT_INCOMPLETE' if final == 'completed' else 'TASK_NOT_COMPLETED', 'last_status': status}, 'blocked': None, 'started_at': lifecycle.get('poll_started_at'), 'finished_at': lifecycle.get('poll_finished_at'), 'poll_events': lifecycle.get('poll_events', []), 'video_count': len(videos)}
