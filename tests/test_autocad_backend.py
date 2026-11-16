from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.autocad_backend import backend_status, build_script, locate_accoreconsole
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer


def _drawing(tmp_path: Path) -> Path:
    path = tmp_path / "source.dxf"
    write_dxf(path, entities=[{"type": "LINE", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 0}}])
    return path


def _call(server, name, arguments=None):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments or {}}})


def test_core_console_script_is_deterministic_and_quotes_windows_paths():
    script = build_script("convert", output_path=r"C:\work folder\out.dwg", audit=True)
    assert script.splitlines() == ["_.AUDIT", "_Y", "_.SAVEAS", '"C:\\work folder\\out.dwg"', "_.QUIT", "_Y"]
    assert build_script("audit").startswith("_.AUDIT\n_Y\n_.QUIT")


def test_status_is_honest_on_non_windows_and_explicit_binary_can_be_discovered(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("SIMLOOM_ACCORECONSOLE", str(executable))
    assert locate_accoreconsole() == str(executable.resolve())
    status = backend_status()
    assert status["available"] is False
    assert status["real_machine_verified"] is False
    assert status["reason"] == "AUTOCAD_REQUIRES_WINDOWS"


def test_autocad_status_and_local_audit_are_available_without_autocad(tmp_path):
    source = _drawing(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    status = agent.execute_operation("autocad.status", {})
    assert status["tool"] == "autocad" and status["real_machine_verified"] is False
    audit = agent.execute_operation("autocad.audit", {"project_dir": str(tmp_path), "input_file": source.name})
    assert audit["native"]["status"] == "not_requested"
    assert audit["local_validation"]["valid"] is True
    listed = MCPServer(agent, profile="full").handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"limit": 500}})
    assert {"operation.autocad.status", "operation.autocad.audit", "operation.autocad.convert"} <= {
        item["name"] for item in listed["result"]["tools"]}


def test_autocad_convert_dry_run_has_backend_evidence(tmp_path):
    source = _drawing(tmp_path)
    result = EngineeringAgent({"tools": {}}).execute_operation("autocad.convert", {
        "project_dir": str(tmp_path), "input_file": source.name, "output_file": "converted.dwg"})
    assert result["status"] == "planned"
    assert result["output"] == "converted.dwg"
    assert result["real_machine_verified"] is False


def test_autocad_convert_uses_transaction_and_persisted_validation(tmp_path, monkeypatch):
    source = _drawing(tmp_path)

    def fake_run(input_path, *, action, output_path=None, **_kwargs):
        assert action == "convert" and output_path is not None
        shutil.copy2(input_path, output_path)
        return {"schema_version": "simloom-ai/autocad/v1", "action": action,
                "returncode": 0, "stdout": "AUDIT OK", "stderr": "", "duration_seconds": 0,
                "input": {"path": str(input_path)}, "output": {"path": str(output_path)}}

    monkeypatch.setattr("engineering_agent.agent.run_core_console", fake_run)
    result = EngineeringAgent({"tools": {}}).execute_operation("autocad.convert", {
        "project_dir": str(tmp_path), "input_file": source.name, "output_file": "converted.dxf",
        "dry_run": False})
    assert result["status"] == "completed"
    assert (tmp_path / "converted.dxf").is_file()
    assert result["persisted_validation"]["valid"] is True
    assert result["run_id"]


def test_native_audit_returns_structured_backend_error_through_mcp(tmp_path):
    source = _drawing(tmp_path)
    response = _call(MCPServer(EngineeringAgent({"tools": {}}), profile="full"), "operation.autocad.audit", {
        "project_dir": str(tmp_path), "input_file": source.name, "native": True})
    assert response["result"]["isError"] is True
    error = response["result"]["structuredContent"]["error"]
    assert error["code"] == "AUTOCAD_REQUIRES_WINDOWS"
