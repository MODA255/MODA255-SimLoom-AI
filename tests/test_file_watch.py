import json
import time
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.file_watch import FileWatchStore
from engineering_agent.mcp_server import MCPServer
from engineering_agent.formats import write_dxf


def test_watch_detects_added_modified_touched_deleted_and_unstable(tmp_path):
    output = tmp_path / "result.csv"
    output.write_text("step,value\n0,1\n", encoding="utf-8")
    agent = EngineeringAgent({"tools": {}})
    started = agent.execute_operation("project.watch_start", {
        "project_dir": str(tmp_path), "paths": ["result.csv", "missing.rst"], "label": "solver outputs"})
    watch_id = started["watch_id"]
    assert started["status"] == "active" and started["changes"] == []

    output.write_text("step,value\n0,2\n", encoding="utf-8")
    added = tmp_path / "missing.rst"
    added.write_bytes(b"RST")
    changed = agent.execute_operation("project.watch_poll", {"project_dir": str(tmp_path), "watch_id": watch_id})
    assert {item["change"] for item in changed["changes"]} == {"modified", "added"}
    cursor = changed["sequence"]

    before = output.stat().st_mtime_ns
    now = max(time.time_ns(), before + 2_000_000)
    import os
    os.utime(output, ns=(now, now))
    touched = agent.execute_operation("project.watch_poll", {
        "project_dir": str(tmp_path), "watch_id": watch_id, "since": cursor})
    assert touched["changes"][0]["change"] == "touched"
    assert touched["history"][-1]["sequence"] == touched["sequence"]

    output.unlink()
    added.unlink()
    deleted = agent.execute_operation("project.watch_poll", {"project_dir": str(tmp_path), "watch_id": watch_id})
    assert {item["change"] for item in deleted["changes"]} == {"deleted"}


def test_watch_expands_directory_and_survives_restart(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "a.txt").write_text("a", encoding="utf-8")
    agent = EngineeringAgent({"tools": {}})
    started = agent.execute_operation("project.watch_start", {"project_dir": str(tmp_path), "paths": ["outputs"]})
    assert started["paths"] == ["outputs"]
    (outputs / "new.rst").write_bytes(b"new")
    (outputs / "a.txt").write_text("b", encoding="utf-8")
    restarted = EngineeringAgent({"tools": {}})
    polled = restarted.execute_operation("project.watch_poll", {"project_dir": str(tmp_path), "watch_id": started["watch_id"]})
    assert {item["path"] for item in polled["changes"]} == {"outputs/a.txt", "outputs/new.rst"}
    assert restarted.execute_operation("project.watch_status", {"project_dir": str(tmp_path), "watch_id": started["watch_id"]})["sequence"] == 1


def test_watch_rejects_internal_and_outside_paths(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    for path in [".engineering-agent/project.lock", "../outside.txt", str(tmp_path.parent / "outside.txt")]:
        with pytest.raises(ValueError):
            agent.execute_operation("project.watch_start", {"project_dir": str(tmp_path), "paths": [path]})


def test_stopped_watch_is_retained_and_mcp_safe_profile_hides_mutation(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    started = agent.execute_operation("project.watch_start", {"project_dir": str(tmp_path), "paths": ["result.rst"]})
    stopped = agent.execute_operation("project.watch_stop", {"project_dir": str(tmp_path), "watch_id": started["watch_id"]})
    assert stopped["stopped"] is True and stopped["status"] == "stopped"
    assert agent.execute_operation("project.watch_poll", {"project_dir": str(tmp_path), "watch_id": started["watch_id"]})["stopped"] is True
    server = MCPServer(agent, profile="safe")
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "operation.project.watch_start", "arguments": {"project_dir": str(tmp_path), "paths": ["x.rst"]}}})
    assert response["error"]["code"] == "INVALID_ARGUMENTS"


def test_watch_is_available_through_mcp_full_and_does_not_mutate_vendor_files(tmp_path):
    output = tmp_path / "result.rst"
    output.write_bytes(b"before")
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "operation.project.watch_start", "arguments": {"project_dir": str(tmp_path), "paths": ["result.rst"]}}})
    payload = json.loads(response["result"]["content"][0]["text"])
    output.write_bytes(b"after")
    poll = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "operation.project.watch_poll", "arguments": {"project_dir": str(tmp_path), "watch_id": payload["watch_id"]}}})
    result = json.loads(poll["result"]["content"][0]["text"])
    assert result["changes"][0]["change"] == "modified"
    assert output.read_bytes() == b"after"
    resource = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {
        "uri": f"engineering://watch?root={tmp_path}&watch_id={payload['watch_id']}"}})
    assert json.loads(resource["result"]["contents"][0]["text"])["watch_id"] == payload["watch_id"]
