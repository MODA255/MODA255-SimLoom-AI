import json

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer
from engineering_agent.transactions import RunTransaction


def source(root):
    path = root / "events.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}],
              circles=[{"x": 5, "y": 5, "radius": 2}])
    return path


def test_cad_batch_persists_step_phases_and_cursor_pages(tmp_path):
    path = source(tmp_path)
    result = EngineeringAgent().execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(path), "output_file": str(path),
        "entity_ids": ["e00001"], "delta": [2, 0, 0], "auto_repair": False})
    run_id = result["run_id"]
    page = RunTransaction.event_page(tmp_path, run_id, limit=4)
    assert page["run_id"] == run_id
    assert page["state"] == "committed"
    assert page["events"]
    assert page["events"][0]["phase"] == "queued"
    assert page["next_cursor"] < page["latest_cursor"]
    phases = {item["phase"] for item in RunTransaction.event_page(tmp_path, run_id, after=0, limit=500)["events"]}
    assert {"queued", "running", "checkpoint", "validation", "committed"} <= phases
    resumed = RunTransaction.event_page(tmp_path, run_id, after=page["next_cursor"], limit=500)
    assert resumed["events"]
    assert all(item["sequence"] > page["next_cursor"] for item in resumed["events"])
    assert resumed["next_cursor"] == resumed["latest_cursor"]
    assert resumed["has_more"] is False


def test_cad_event_page_reads_a_live_transaction_without_writer_lock(tmp_path):
    transaction = RunTransaction(tmp_path)
    transaction.begin({"operation": "cad.batch_apply"})
    try:
        live = RunTransaction.event_page(tmp_path, transaction.run_id, limit=20)
        assert live["state"] == "running"
        assert [item["phase"] for item in live["events"][:2]] == ["queued", "running"]
        transaction.record("checkpoint", {"label": "live-checkpoint"})
        after = RunTransaction.event_page(tmp_path, transaction.run_id, after=live["latest_cursor"])
        assert after["events"][0]["event"] == "checkpoint"
    finally:
        transaction.rollback("test cleanup")


def test_cad_events_is_available_through_mcp_and_rejects_bad_cursor(tmp_path):
    path = source(tmp_path)
    result = EngineeringAgent().execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(path), "output_file": str(path),
        "entity_ids": ["e00001"], "delta": [1, 0, 0], "auto_repair": False})
    server = MCPServer(EngineeringAgent(), profile="full")
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "operation.cad.events", "arguments": {
            "project_dir": str(tmp_path), "run_id": result["run_id"], "limit": 2}}})
    assert "result" in response and response["result"].get("isError") is not True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["events"] and payload["next_cursor"]
    with pytest.raises(ValueError, match="INVALID_OPERATION_ARGUMENTS:after:MINIMUM"):
        EngineeringAgent().execute_operation("cad.events", {
            "project_dir": str(tmp_path), "run_id": result["run_id"], "after": -1})


def test_cad_events_resource_supports_read_subscribe_and_update_notification(tmp_path):
    path = source(tmp_path)
    agent = EngineeringAgent()
    first = agent.execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(path), "output_file": str(path),
        "entity_ids": ["e00001"], "delta": [1, 0, 0], "auto_repair": False})
    server = MCPServer(agent, profile="full")
    uri = f"engineering://cad-events?root={tmp_path}&run_id={first['run_id']}&after=0"
    subscribed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/subscribe", "params": {"uri": uri}})
    assert subscribed["result"]["subscribed"] is True
    resource = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}})
    value = json.loads(resource["result"]["contents"][0]["text"])
    assert value["run_id"] == first["run_id"] and value["events"]
    second_response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "operation.cad.entity_move", "arguments": {
            "project_dir": str(tmp_path), "input_file": str(path), "output_file": str(path),
            "entity_ids": ["e00001"], "delta": [1, 0, 0], "auto_repair": False}}})
    assert second_response["result"]["structuredContent"]["run_id"] != first["run_id"]
    notifications = server.drain_notifications()
    assert any(item["method"] == "notifications/resources/updated" and item["params"]["uri"] == uri
               for item in notifications)

