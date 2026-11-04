import json
from pathlib import Path

from orchestrator.phase1_runner import (
    PHASE1_ALLOWLIST,
    build_phase1_plan,
    phase1_readiness,
    run_phase1,
)
from orchestrator.phase1_suite import load_phase1_suite

CONFIG = Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json"


def test_build_plan_contains_all_nine_chains_and_no_historical_tasks():
    plan = build_phase1_plan(load_phase1_suite(CONFIG), "r1")
    assert [item["chain_id"] for item in plan["chains"]] == list(PHASE1_ALLOWLIST)
    assert all("task_id" not in item for item in plan["case_runs"])
    assert [item["batch"] for item in plan["chains"]] == ["ingest"] * 3 + ["manual"] * 3 + ["automation"] * 3


def test_readiness_rejects_unresolved_env_refs_and_missing_upload(tmp_path):
    suite = json.loads(CONFIG.read_text())
    suite["folder"] = "env:MISSING_FOLDER"
    suite["inputs"]["DAILY_UPLOAD_FILE"] = "env:MISSING_FILE"
    result = phase1_readiness({}, suite=suite)
    assert result["status"] == "blocked"
    assert any("unresolved environment reference" in error for error in result["errors"])
    assert any("upload file is missing" in error for error in result["errors"])


def test_load_suite_resolves_env_values_into_request(tmp_path, monkeypatch):
    suite = json.loads(CONFIG.read_text())
    suite["folder"] = "env:FOLDER_ID"
    suite["inputs"]["DAILY_DOUYIN_URL"] = "env:SOURCE_URL"
    env_file = tmp_path / ".env"
    env_file.write_text("FOLDER_ID=folder-real\nSOURCE_URL=https://example.test/video\n")
    config_file = tmp_path / "suite.json"
    config_file.write_text(json.dumps(suite))
    loaded = load_phase1_suite(config_file)
    case = loaded["chains"][0]["cases"][0]
    assert case["folder_id"] == "folder-real"
    assert case["input"] == "https://example.test/video"
    assert case["request"]["input"] == "https://example.test/video"


def test_allowlist_rejects_unlisted_chain():
    suite = load_phase1_suite(CONFIG)
    suite["chains"][0]["chain_id"] = "OTHER"
    readiness = phase1_readiness({"phase1": {"suite": str(CONFIG)}}, suite=suite)
    assert readiness["status"] == "blocked"
    assert "allowlist" in readiness["reason"]


def test_readiness_failure_does_not_call_remote_or_write(tmp_path):
    class ExplodingAdapter:
        def __getattr__(self, name):
            raise AssertionError(f"remote called: {name}")

    bad = {"enabled_chains": ["I-LINK"], "phase1": {"suite": str(CONFIG)}}
    result = run_phase1(ExplodingAdapter(), bad, "r1", tmp_path)
    assert result["status"] == "blocked"
    root = tmp_path / "r1"
    assert (root / "daily-report.json").is_file()
    assert (root / "cleanup-plan.json").is_file()


def test_independent_chain_failure_does_not_block_other_chain(tmp_path):
    calls = []
    class Adapter:
        safe_write = True
        def ingest_url(self, payload):
            calls.append(payload.get("chain_id"))
            if payload.get("chain_id") == "I-LINK":
                raise RuntimeError("link down")
            return {"id": "upload-1"}
        def ingest_file(self, payload):
            calls.append(payload.get("chain_id")); return {"id": "upload-1"}
        def get_asset(self, rid): return {"id": rid, "status": "ready"}
        def get_asset_progress(self, rid): return {"status": "ready"}

    result = run_phase1(Adapter(), {"phase1": {"suite": str(CONFIG), "safe_write": True}}, "r1", tmp_path)
    by_chain = {x["chain_id"]: x for x in result["chains"]}
    assert by_chain["I-LINK"]["status"] != "passed"
    assert by_chain["I-UPLOAD"]["status"] == "passed"
    assert "I-UPLOAD" in calls
    assert {x["chain_id"] for x in result["chains"]} == set(PHASE1_ALLOWLIST)


def test_blocked_live_chains_persist_probe_and_dependency_evidence(tmp_path):
    class ReadOnlyAdapter:
        safe_write = True
    result = run_phase1(ReadOnlyAdapter(), {"phase1": {"suite": str(CONFIG), "safe_write": True}}, "r1", tmp_path)
    for chain_id in ("I-LIVE", "A-ECOM", "A-THEME"):
        item = next(x for x in result["chains"] if x["chain_id"] == chain_id)
        assert item["status"] == "blocked"
        assert item["evidence"]
        assert any(e["kind"] in {"probe", "dependency"} for e in item["evidence"])


def test_non_live_cases_get_independent_resource_payloads_and_dispatch(tmp_path):
    calls = []

    def runner(adapter, case, output_dir):
        calls.append((case["case_id"], case["payload"]))
        assert case["payload"]["master_asset_ids"]
        assert case["payload"]["resource_ids"]
        return "passed", {"test_status": "passed", "resource_id": "generated"}

    class Adapter:
        safe_write = True
        def ingest_url(self, payload): return {"id": "asset-link"}
        def get_asset(self, rid): return {"id": rid, "status": "ready"}
        def get_asset_progress(self, rid): return {"status": "ready"}
        def ingest_file(self, payload): return {"id": "asset-upload"}

    suite = load_phase1_suite(CONFIG)
    suite["safe_write"] = True
    result = run_phase1(Adapter(), {"phase1": {"safe_write": True}}, "r1", tmp_path,
                        suite=suite, preflight={"status": "ready"}, runners={"M-ECOM": runner, "M-IP": runner, "M-THEME": runner, "A-IP": runner})
    assert {case_id.split("-")[0] for case_id, _ in calls} == {"M", "A"}
    assert all(payload["chain_id"] in {"M-ECOM", "M-IP", "M-THEME", "A-IP"} for _, payload in calls)


def test_safe_write_false_blocks_all_adapters_without_any_write_calls(tmp_path):
    calls = []
    class Adapter:
        def __getattribute__(self, name):
            if name in {"ingest_url", "ingest_file", "create_vfc_task", "automation_request", "request"}:
                calls.append(name)
                raise AssertionError(f"write called: {name}")
            return object.__getattribute__(self, name)
    suite = load_phase1_suite(CONFIG)
    result = run_phase1(Adapter(), {"phase1": {"safe_write": False}}, "r-safe", tmp_path,
                        suite=suite, preflight={"status": "ready"})
    assert result["status"] == "blocked"
    assert result["writes"] == 0
    assert calls == []


def test_ready_payload_overwrites_historical_resource_ids(tmp_path):
    captured = []
    def runner(adapter, case, output_dir):
        captured.append(case["payload"])
        return "passed", {"test_status": "passed", "resource_id": "generated"}
    suite = load_phase1_suite(CONFIG)
    suite["safe_write"] = True
    suite["chains"][0]["cases"][0]["payload"] = {"resource_ids": ["old"], "master_asset_ids": ["old"], "master_asset_id": "old"}
    class Adapter:
        safe_write = True
        def ingest_url(self, payload): return {"id": "new"}
        def get_asset(self, rid): return {"id": rid, "status": "ready", "downstream_ready": True}
        def get_asset_progress(self, rid): return {"status": "ready", "downstream_ready": True}
    def ingest_runner(adapter, case, output_dir):
        return "passed", {"test_status": "passed", "downstream_ready": True,
                           "poll": {"status": "ready"}, "resource_id": "new"}
    run_phase1(Adapter(), {"phase1": {"safe_write": True}}, "r-overwrite", tmp_path, suite=suite,
               preflight={"status": "ready"}, runners={"I-LINK": ingest_runner, "M-ECOM": runner})
    assert captured
    assert captured[0]["resource_ids"] == ["new"]
    assert captured[0]["master_asset_ids"] == ["new"]
    assert captured[0]["master_asset_id"] == "new"


def test_non_ready_resource_blocks_downstream_before_runner(tmp_path):
    calls = []
    def runner(adapter, case, output_dir):
        calls.append(case["case_id"])
        return "passed", {}
    suite = load_phase1_suite(CONFIG); suite["safe_write"] = True
    class Adapter:
        safe_write = True
        def ingest_url(self, payload): return {"id": "not-ready"}
        def get_asset(self, rid): return {"id": rid, "status": "processing", "downstream_ready": False}
        def get_asset_progress(self, rid): return {"status": "processing", "downstream_ready": False}
    result = run_phase1(Adapter(), {"phase1": {"safe_write": True}}, "r-not-ready", tmp_path,
                        suite=suite, preflight={"status": "ready"}, runners={"M-ECOM": runner})
    assert calls == []
    rows = [x for x in result["case_runs"] if x["chain_id"] == "M-ECOM"]
    assert rows and all(x["result"]["failure"]["code"] == "READY_RESOURCE_MISSING" for x in rows)


def test_missing_ready_resource_blocks_case_structurally(tmp_path):
    calls = []
    def runner(adapter, case, output_dir):
        calls.append(case["case_id"])
        return "passed", {}
    suite = load_phase1_suite(CONFIG)
    suite["safe_write"] = True
    class ReadOnlyAdapter:
        safe_write = True
    result = run_phase1(ReadOnlyAdapter(), {"phase1": {"safe_write": True}}, "r1", tmp_path,
                        suite=suite, preflight={"status": "ready"}, runners={"M-ECOM": runner})
    cases = [x for x in result["case_runs"] if x["chain_id"] == "M-ECOM"]
    assert cases and all(x["status"] == "blocked" for x in cases)
    assert all(x["result"]["failure"]["code"] == "READY_RESOURCE_MISSING" for x in cases)
    assert calls == []
