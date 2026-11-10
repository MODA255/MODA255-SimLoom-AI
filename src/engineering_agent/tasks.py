"""Shared task execution for the workbench and queue worker."""
from dataclasses import asdict

from .offline_workflow import DEFAULT_RULES
from .script_gen import ModelSpec


def input_snapshot(metadata=None, overrides=None):
    metadata, overrides = metadata or {}, overrides or {}
    spec = overrides.get("model_spec", metadata.get("model_spec", {}))
    rules = overrides.get("rules", metadata.get("rules", DEFAULT_RULES))
    if not isinstance(spec, dict) or not isinstance(rules, list):
        raise ValueError("模型参数必须为对象，指标规则必须为数组")
    return {"project_name": metadata.get("name", "未命名项目"),
            "model_spec": asdict(ModelSpec(**spec)), "rules": rules}


class TaskRunner:
    def __init__(self, agent, queue):
        self.agent, self.queue = agent, queue

    def run(self, task_id, *, execute=False):
        task = self.queue.get(task_id)
        if not task:
            raise FileNotFoundError("任务不存在")
        if not self.queue.claim(task_id):
            raise RuntimeError("TASK_ALREADY_RUNNING_OR_FINISHED")
        try:
            snapshot = task["input"]
            if snapshot is None:
                # Old queued tasks can still run, but capture inputs before execution.
                project = self.queue.get_project(task["project_id"]) if task["project_id"] else None
                snapshot = input_snapshot((project or {}).get("metadata"))
                self.queue.set_input(task_id, snapshot)
            def progress(event, payload):
                self.queue.record_event(task_id, event, payload)
            mode = "offline" if task["mode"] == "offline" else ("external" if execute else "rehearsal")
            progress("execution_mode", {"mode": mode})
            if task["mode"] == "offline":
                result = self.agent.run_offline_vehicle_chain(
                    task["project_dir"], snapshot["model_spec"], snapshot["rules"], on_event=progress)
            else:
                result = self.agent.run(task["prompt"], project_dir=task["project_dir"],
                                        parameters={"model_spec": snapshot["model_spec"]},
                                        dry_run=not execute, on_event=progress).to_dict()
            result["task_id"] = task_id
            result["execution_mode"] = mode
            if "verdicts" in result:
                result["validation_passed"] = bool(result["verdicts"]) and all(v["passed"] for v in result["verdicts"])
            self.queue.update(task_id, "completed" if result["status"] == "committed" else "failed", result)
            return result
        except Exception as exc:
            self.queue.update(task_id, "failed", {"error": str(exc), "task_id": task_id})
            raise
