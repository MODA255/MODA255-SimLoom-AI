

def assess_delivery_safety(submission, task=None, delivery_events=None):
    submission = submission or {}; task = task or {}; events = delivery_events or []
    request_disabled = submission.get('delivery_enabled') is False or submission.get('auto_delivery_enabled') is False
    task_value = task.get('delivery_enabled')
    task_disabled = task_value is False or task.get('auto_delivery_enabled') is False
    if request_disabled and task_disabled and not events:
        verdict = 'passed'
    elif any(events):
        verdict = 'failed'
    else:
        verdict = 'insufficient_evidence'
    return {'request_disabled': request_disabled, 'task_disabled': task_disabled, 'delivery_task_count': len(events), 'delivery_event_count': len(events), 'verdict': verdict}


CRITICAL_DELIVERY_FIELDS = (
    "delivery_enabled", "auto_delivery_enabled", "advertiser_id", "awemes",
    "products", "promotion_goal", "adlab_scene",
)


def strict_delivery_check(before, after):
    """Check both snapshots; missing fields are unknown, never safe by default."""
    snapshots = (before if isinstance(before, dict) else {}, after if isinstance(after, dict) else {})
    missing = sorted({key for snapshot in snapshots for key in CRITICAL_DELIVERY_FIELDS if key not in snapshot})
    enabled = False
    for snapshot in snapshots:
        if snapshot.get("delivery_enabled") is not False or snapshot.get("auto_delivery_enabled") is not False:
            enabled = True
        if any(snapshot.get(key) not in (None, [], "") for key in CRITICAL_DELIVERY_FIELDS[2:]):
            enabled = True
    return {"complete": not missing, "missing": missing, "enabled": enabled}
