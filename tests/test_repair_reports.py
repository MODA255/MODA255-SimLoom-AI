import json

from engineering_agent.queue import TaskQueue
from engineering_agent.reports import build_report, render_report
from engineering_agent.tasks import input_snapshot


def _repair_fixture():
    comparison = {
        "schema_version": "simloom-ai/repair-comparison/v1",
        "parent_workflow_id": "parent-workflow",
        "child_workflow_id": "child-workflow",
        "candidate": {"strategy_id": "reduce_load"},
        "parent": {"status": "completed", "engineering_acceptance": "failed"},
        "child": {"status": "completed", "engineering_acceptance": "failed"},
        "metrics": [{
            "step_id": "ansys_static", "name": "stress", "operator": "<=", "target": 80,
            "unit": "MPa", "parent": {"value": 120, "passed": False},
            "child": {"value": 95, "passed": False}, "delta": -25,
            "trend": "improved", "comparable": True,
        }],
        "counts": {"improved": 1, "regressed": 0, "unchanged": 0, "unknown": 0},
        "outcome": "improved", "next_action": "evaluate_next_candidate", "should_continue": True,
    }
    loop = {
        "schema_version": "simloom-ai/repair-loop/v1", "budget": 2, "attempt_count": 1,
        "attempts": [{
            "index": 1, "status": "committed", "workflow_id": "child-workflow",
            "candidate": {"strategy_id": "reduce_load"}, "comparison": comparison,
            "decision": {"status": "continue_allowed", "reason": "remaining_declared_candidates",
                          "next_action": "evaluate_next_candidate"},
        }],
        "decision": {"status": "continue_allowed", "reason": "remaining_declared_candidates",
                     "next_action": "evaluate_next_candidate"},
    }
    return {"status": "committed", "workflow_id": "parent-workflow", "validation": {
        "auto_repair": {
            "requested": True, "status": "committed", "parent_workflow_id": "parent-workflow",
            "child_workflow_id": "child-workflow", "candidate": {"strategy_id": "reduce_load",
            "reason": "切换工程师已配置的备用载荷工况"}, "comparison": comparison,
            "decision": loop["decision"], "repair_loop": loop,
        }
    }}


def test_task_report_exposes_bounded_repair_comparison_and_loop(tmp_path):
    queue = TaskQueue(tmp_path / "reports.db")
    task = queue.submit("Ansys 应力分析", str(tmp_path), inputs=input_snapshot())
    queue.update(task, "completed", _repair_fixture())

    report = build_report(queue, task)
    repair = report["repair"]
    assert repair["schema_version"] == "simloom-ai/task-repair-report/v1"
    assert repair["parent_workflow_id"] == "parent-workflow"
    assert repair["child_workflow_id"] == "child-workflow"
    assert repair["comparison"]["metrics"][0]["delta"] == -25
    assert repair["attempts"][0]["candidate"]["strategy_id"] == "reduce_load"

    html = render_report(report)
    assert "修复比较与重规划" in html
    assert "父子指标变化" in html
    assert "reduce_load" in html
    assert "remaining_declared_candidates" in html


def test_task_report_keeps_repair_section_empty_without_persisted_evidence(tmp_path):
    queue = TaskQueue(tmp_path / "reports.db")
    task = queue.submit("排练", str(tmp_path), inputs=input_snapshot())
    queue.update(task, "completed", {"status": "committed", "results": []})
    report = build_report(queue, task)
    assert report["repair"] is None
    assert "修复比较与重规划" not in render_report(report)


def test_task_report_limits_repair_attempts_and_metrics(tmp_path):
    queue = TaskQueue(tmp_path / "reports.db")
    task = queue.submit("限制大小", str(tmp_path), inputs=input_snapshot())
    result = _repair_fixture()
    comparison = result["validation"]["auto_repair"]["comparison"]
    comparison["metrics"] = [{"name": str(index), "trend": "unknown"} for index in range(300)]
    result["validation"]["auto_repair"]["repair_loop"]["attempts"] = [
        {"index": index, "workflow_id": str(index)} for index in range(20)
    ]
    queue.update(task, "completed", result)

    repair = build_report(queue, task)["repair"]
    assert len(repair["comparison"]["metrics"]) == 256
    assert len(repair["attempts"]) == 8
    json.dumps(repair, ensure_ascii=False)
