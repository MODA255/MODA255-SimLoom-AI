import json
from pathlib import Path

from orchestrator.cli import main
from orchestrator.phase1_runner import build_phase1_plan, phase1_readiness, run_phase1
from orchestrator.phase1_suite import load_phase1_suite

CONFIG = Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json"


def test_direct_phase1_config_is_accepted_without_nested_orchestrator_mode():
    raw = json.loads(CONFIG.read_text())
    assert phase1_readiness(raw)["status"] == "ready"


def test_malformed_phase1_config_is_structured_blocked_without_attribute_error():
    raw = json.loads(CONFIG.read_text())
    malformed = [
        {"phase1": []},
        {"phase1": {"suite": 123}},
        {"phase1": {"suite": {**raw, "chains": "not-a-list"}}},
        {"phase1": {"suite": {**raw, "chains": [{"chain_id": "I-LINK", "cases": "not-a-list"}]}}},
        {"phase1": {"suite": {**raw, "chains": []}}, "enabled_chains": "not-a-list"},
    ]
    for config in malformed:
        result = phase1_readiness(config)
        assert result["status"] == "blocked"
        assert result["writes"] == 0
        assert result.get("reason") or result.get("errors")


def test_each_case_expands_to_nonempty_executable_request_and_missing_input_blocks():
    suite = load_phase1_suite(CONFIG)
    plan = build_phase1_plan(suite, "r-contract")
    assert plan["case_runs"]
    assert all(run.get("request") for run in plan["case_runs"])
    broken = json.loads(CONFIG.read_text())
    broken["inputs"].pop("DAILY_DOUYIN_URL")
    assert phase1_readiness(broken)["status"] == "blocked"
    assert "input" in phase1_readiness(broken)["reason"]


def test_run_phase1_writes_actual_result_to_every_case_run(tmp_path):
    raw = json.loads(CONFIG.read_text())
    class Adapter:
        safe_write = True
        def ingest_url(self, payload): return {"id": "asset-1"}
        def ingest_file(self, payload): return {"id": "asset-2"}
        def get_asset(self, rid): return {"id": rid, "status": "ready"}
        def get_asset_progress(self, rid): return {"status": "ready"}
    raw["phase1"] = {"safe_write": True}
    result = run_phase1(Adapter(), raw, "r-writeback", tmp_path,
                        runners={chain: (lambda adapter, spec, output_dir: ("passed", {"test_status": "passed", "request": spec["request"]}))
                                  for chain in ("M-ECOM", "M-IP", "M-THEME", "A-ECOM", "A-IP", "A-THEME")})
    assert result["case_runs"]
    assert all(item["status"] != "planned" for item in result["case_runs"])
    assert all("result" in item for item in result["case_runs"])


def test_phase1_has_six_non_live_mappings_and_three_live_evidence_contracts():
    raw = json.loads(CONFIG.read_text())
    assert set(raw["evidence_mapping"]["non_live"]) == {"I-LINK", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME", "A-IP"}
    assert set(raw["evidence_mapping"]["live"]) == {"I-LIVE", "A-ECOM", "A-THEME"}
    assert all(raw["evidence_mapping"]["live"][key]["evidence"] for key in raw["evidence_mapping"]["live"])


def test_cli_phase1_readiness_bypasses_ordinary_load_config(monkeypatch, capsys):
    import orchestrator.cli as cli
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ordinary config loader called")))
    assert cli.main(["daily-run", "--config", str(CONFIG), "--phase1"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_each_case_is_dispatched_and_only_its_case_run_is_updated(tmp_path):
    raw = json.loads(CONFIG.read_text())
    calls = []
    def runner(adapter, spec, output_dir):
        calls.append(spec["case_id"])
        return "passed", {"case_id": spec["case_id"], "test_status": "passed"}
    class Adapter:
        safe_write = True
        def ingest_url(self, payload): return {"id": "asset"}
        def ingest_file(self, payload): return {"id": "asset"}
        def get_asset(self, rid): return {"id": rid, "status": "ready"}
        def get_asset_progress(self, rid): return {"status": "ready"}
    raw["phase1"] = {"safe_write": True}
    result = run_phase1(Adapter(), raw, "r-case-granularity", tmp_path,
                        runners={"M-ECOM": runner, "M-IP": runner, "M-THEME": runner,
                                 "A-ECOM": runner, "A-IP": runner, "A-THEME": runner})
    assert "M-ECOM-001" in calls and "M-ECOM-002" in calls
    rows = [row for row in result["case_runs"] if row["chain_id"] == "M-ECOM"]
    assert {row["result"]["case_id"] for row in rows} == {"M-ECOM-001", "M-ECOM-002"}


def test_remote_adapter_upload_capability_is_explicitly_missing():
    from orchestrator.adapters import RemoteAdapter
    adapter = RemoteAdapter({"target": {"base_url": "https://example.test"}})
    assert adapter._route("upload_file") is None


def test_upload_without_adapter_is_structured_blocked(tmp_path):
    raw = json.loads(CONFIG.read_text())
    class NoUploadAdapter:
        safe_write = True
        def ingest_url(self, payload): return {"id": "asset"}
        def get_asset(self, rid): return {"id": rid, "status": "ready"}
        def get_asset_progress(self, rid): return {"status": "ready"}
    raw["phase1"] = {"safe_write": True}
    result = run_phase1(NoUploadAdapter(), raw, "r-no-upload", tmp_path)
    row = next(item for item in result["case_runs"] if item["case_id"] == "I-UPLOAD-001")
    assert row["status"] == "blocked"
    assert row["result"]["failure"]["code"] == "ADAPTER_CAPABILITY_MISSING"
