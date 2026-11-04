from .automation_lifecycle import run_automation_parent_child
from .automation_adapters import AutomationTaskAdapter


def execute_automation_children(adapter, parent_id, *, timeout_seconds=3600, interval_seconds=20, on_event=None):
    lifecycle_adapter = AutomationTaskAdapter(adapter)
    return run_automation_parent_child(adapter, parent_id, child_poller=lambda child_id: lifecycle_adapter.child_poll(child_id, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds, on_event=on_event))
