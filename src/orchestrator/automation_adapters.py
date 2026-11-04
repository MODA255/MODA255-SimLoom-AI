from .adapters import RemoteRouteError
from .runner import poll_task


class AutomationTaskAdapter:
    def __init__(self, adapter): self.adapter = adapter
    def create(self, payload): return self.adapter.create_daily_task(payload.get('scene_type'), payload, execute=True)
    def get(self, task_id): return self.adapter.get_daily_task('ecom_mix', task_id)
    def poll(self, task_id, **kwargs): return poll_task(self, task_id, **kwargs)
    def get_task(self, task_id): return self.get(task_id)
    def child_poll(self, task_id, **kwargs): return poll_task(self, task_id, **kwargs)
    def stages(self, task_id): return self.adapter.get_stage_snapshot('ecom_mix', task_id)


class IPPromoAutomationAdapter(AutomationTaskAdapter):
    def create(self, payload): return self.adapter.create_daily_task('ip_promo', payload, execute=True)
    def get(self, task_id): return self.adapter.get_daily_task('ip_promo', task_id)
    def get_task(self, task_id): return self.get(task_id)
    def stages(self, task_id): return self.adapter.get_stage_snapshot('ip_promo', task_id)
