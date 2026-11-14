"""Machine evidence is an attestation layer, never a fixture shortcut."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from engineering_agent.machine_evidence import MachineEvidenceStore, SCHEMA
from engineering_agent.windows_bridge import WindowsBridge
from engineering_agent import EngineeringAgent
from engineering_agent.mcp_server import MCPServer
from engineering_agent.worker_protocol import SCHEMA as WORKER_SCHEMA, WorkerError


class FakeJobs:
    def __init__(self, root, *, valid=True):
        self.root = Path(root)
        data = b"verified native output"
        self.manifest = [{"path": "artifacts/model.step", "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()}]
        self._result = {
            "schema_version": WORKER_SCHEMA, "request_id": "a" * 32,
            "request_digest": "b" * 64, "tool": "catia", "action": "update_parameters",
            "verification": "artifact_and_vendor_evidence" if valid else "artifact_integrity",
            "vendor_validation": {"schema_version": "simloom-ai/vendor-evidence/v2", "passed": valid},
            "outputs": self.manifest,
        }

    def status(self, request_id):
        return {"status": "completed", "request_id": request_id}

    def result(self, request_id):
        return dict(self._result, outputs=list(self._result["outputs"]))


def payload(**overrides):
    result = {"evidence_schema": SCHEMA, "request_id": "a" * 32, "tool": "catia",
              "action": "update_parameters", "operator": "engineer",
              "software_version": "CATIA V5 R2025x", "license_status": "available",
              "smoke_test_id": "catia-parameter-step", "notes": "baseline"}
    return result | overrides


def test_record_is_windows_only_and_requires_vendor_evidence(tmp_path, monkeypatch):
    jobs = FakeJobs(tmp_path, valid=True)
    store = MachineEvidenceStore(tmp_path, jobs)
    monkeypatch.setattr("engineering_agent.machine_evidence.platform.system", lambda: "Darwin")
    with pytest.raises(WorkerError, match="MACHINE_EVIDENCE_REQUIRES_WINDOWS"):
        store.record(payload())
    monkeypatch.setattr("engineering_agent.machine_evidence.platform.system", lambda: "Windows")
    record = store.record(payload())
    assert record["valid"] is True
    assert record["worker"]["platform"] == "Windows"
    assert record["evidence_id"]


def test_record_retry_is_idempotent_and_listing_revalidates_job(tmp_path, monkeypatch):
    monkeypatch.setattr("engineering_agent.machine_evidence.platform.system", lambda: "Windows")
    jobs = FakeJobs(tmp_path, valid=True)
    store = MachineEvidenceStore(tmp_path, jobs)
    first = store.record(payload())
    second = store.record(payload())
    assert first["evidence_id"] == second["evidence_id"]
    assert len(store.list()["records"]) == 1
    jobs._result["outputs"] = [{"path": "artifacts/model.step", "bytes": 1, "sha256": "0" * 64}]
    listed = store.list()["records"]
    assert listed[0]["valid"] is False
    assert "OUTPUT_MANIFEST_CHANGED" in listed[0]["validation_errors"]


def test_record_rejects_fixture_only_job(tmp_path, monkeypatch):
    monkeypatch.setattr("engineering_agent.machine_evidence.platform.system", lambda: "Windows")
    store = MachineEvidenceStore(tmp_path, FakeJobs(tmp_path, valid=False))
    with pytest.raises(WorkerError, match="MACHINE_EVIDENCE_VENDOR_CONTRACT_REQUIRED"):
        store.record(payload())


def test_bridge_exposes_evidence_discovery_without_claiming_a_record(tmp_path):
    bridge = WindowsBridge({"root_dir": str(tmp_path), "commands": {}})
    body = bridge.handle({"schema_version": WORKER_SCHEMA, "operation": "evidence.list"})
    assert body["schema_version"] == SCHEMA
    assert body["records"] == []
    capabilities = bridge.capabilities()
    assert capabilities["real_machine_verified"] is False
    assert all(item["real_machine_verified"] is False for item in capabilities["tools"].values())


def test_controller_and_mcp_surface_forward_worker_evidence(monkeypatch):
    agent = EngineeringAgent({"tools": {"catia": {"bridge_url": "http://worker"}}})
    captured = []

    def post(payload, timeout):
        captured.append(payload)
        return {"schema_version": SCHEMA, "records": [], "count": 0}

    monkeypatch.setattr(agent.adapters[next(key for key in agent.adapters if str(key.value) == "catia")], "_post", post)
    result = agent.worker_evidence("catia", "record", request_id="a" * 32,
                                   vendor_action="update_parameters", software_version="V5",
                                   license_status="available", smoke_test_id="smoke")
    assert result["count"] == 0
    assert captured[0]["operation"] == "evidence.record"
    assert captured[0]["action"] == "update_parameters"
    safe = MCPServer(agent, profile="safe")
    full = MCPServer(agent, profile="full")
    safe_tool = next(item for item in safe._tools() if item["name"] == "engineering_worker_evidence")
    full_tool = next(item for item in full._tools() if item["name"] == "engineering_worker_evidence")
    assert safe_tool["inputSchema"]["properties"]["action"]["enum"] == ["list"]
    assert full_tool["inputSchema"]["properties"]["action"]["enum"] == ["list", "record"]

