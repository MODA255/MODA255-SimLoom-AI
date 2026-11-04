from orchestrator.cleanup_plan import build_cleanup_plan
from orchestrator.replay import replay_report


def test_cleanup_plan_dry_run_cancels_waiting_parent_and_retains_completed_output():
    plan = build_cleanup_plan({"chains":[
        {"chain_id":"auto_ecom","task":{"task_id":"child","status":"completed"},"parent_task_status":"waiting_live","videos":{"created":1},"cleanup_status":"not_started"},
        {"chain_id":"manual_theme","task":{"task_id":"task","status":"completed"},"parent_task_status":None,"videos":{"created":1},"cleanup_status":"not_started"},
    ], "dry_run": True})
    assert plan["status"] == "planned"
    assert plan["actions"][0]["action"] == "cancel_parent"
    assert any(a["action"] == "retain" and a["chain_id"] == "manual_theme" for a in plan["actions"])
    assert plan["remote_writes"] is False


def test_replay_report_recomputes_without_remote_writes():
    result = replay_report({"chains":[{"chain_id":"manual_theme","task":{"id":"t","status":"completed","recipe":"auto_theme_discovery","delivery_enabled":False,"auto_delivery_enabled":False,"completed_phases":1,"total_phases":1},"videos":[],"expected_video_count":0}]})
    assert result["mode"] == "offline_replay"
    assert result["remote_writes"] is False
    assert result["chain_count"] == 1
