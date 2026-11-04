import json
import subprocess
import sys
from pathlib import Path

from orchestrator.phase1_report import build_phase1_report
from orchestrator.phase1_runner import run_phase1
from orchestrator.repository import Repository

CONFIG = Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json"


def test_memory_repository_keeps_resources_table_and_rows():
    repo = Repository(":memory:")
    repo.insert_resource({
        "id": "resource-1", "run_id": "run-1", "case_id": "case-1", "case_run_id": "cr-1",
        "resource_type": "upload", "resource_id": None, "status": "submission_pending",
        "created_at": "2026-01-01T00:00:00+00:00", "cleanup_after": "2026-01-08",
        "protected": True, "protection_reason": "pending", "metadata": {},
    })
    assert repo.list_resources("run-1")[0]["case_run_id"] == "cr-1"


def test_report_top_level_counts_match_case_counts_for_all_statuses():
    statuses = ["passed", "poll_timeout", "running", "unknown", "blocked"]
    report = build_phase1_report({
        "run_id": "run-1",
        "case_runs": [{"chain_id": "I-LINK", "case_run_id": f"cr-{i}", "status": status,
                        "hard_acceptance": status == "passed"} for i, status in enumerate(statuses)],
    })
    for key in ("passed", "poll_timeout", "running", "unknown", "blocked"):
        assert report["counts"][key] == report["case_counts"][key]


def test_blocked_phase1_writes_complete_report_without_remote_calls(tmp_path):
    class ExplodingAdapter:
        def __getattr__(self, name):
            raise AssertionError(f"remote called: {name}")

    result = run_phase1(ExplodingAdapter(), {"enabled_chains": ["I-LINK"], "phase1": {"suite": str(CONFIG)}}, "run-blocked", tmp_path)
    assert result["status"] == "blocked"
    root = tmp_path / "run-blocked"
    assert all((root / name).is_file() for name in ("run.json", "preflight.json", "resource-registry.json", "cleanup-plan.json", "daily-report.json", "daily-report.md"))
    cleanup = json.loads((root / "cleanup-plan.json").read_text())
    assert cleanup["remote_deletes_executed"] is False
    daily_report = json.loads((root / "daily-report.json").read_text())
    assert daily_report["counts"]["blocked"] == len(daily_report["case_runs"])
    assert daily_report["case_counts"]["blocked"] == len(daily_report["case_runs"])
    assert len(daily_report["case_runs"]) >= 15
    assert {item["chain_id"] for item in daily_report["case_runs"]} == {
        "I-LINK", "I-LIVE", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME",
        "A-ECOM", "A-IP", "A-THEME",
    }
    assert all(item.get("reason") or item.get("result", {}).get("failure", {}).get("reason") for item in daily_report["case_runs"])
    assert all((root / "cases" / str(item["case_run_id"]) / "case.json").is_file() for item in daily_report["case_runs"])


def test_cli_phase1_execute_defaults_missing_state_db_to_run_scoped_path(tmp_path, monkeypatch):
    config = json.loads(CONFIG.read_text())
    config["output_dir"] = str(tmp_path / "runs")
    config["phase1"] = {"suite": str(CONFIG), "output_dir": str(tmp_path / "runs")}
    config.pop("state_db", None)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.chdir(tmp_path)

    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator", "daily-run", "--config", str(config_path),
         "--phase1", "--execute", "--run-id", "cli-missing-state"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "runs" / ".phase1-state-cli-missing-state.db").is_file()
    assert json.loads(proc.stdout)["status"] == "blocked"


def test_cli_phase1_blocked_writes_report_directory(tmp_path):
    config = json.loads((CONFIG).read_text())
    config["output_dir"] = str(tmp_path / "runs")
    config["enabled_chains"] = ["I-LINK"]
    config["phase1"] = {"suite": str(CONFIG), "output_dir": str(tmp_path / "runs")}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    proc = subprocess.run([sys.executable, "-m", "orchestrator", "daily-run", "--config", str(config_path), "--phase1", "--execute", "--run-id", "cli-blocked"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    root = tmp_path / "runs" / "cli-blocked"
    assert (root / "resource-registry.json").is_file()
    report = json.loads((root / "daily-report.json").read_text())
    assert report["counts"]["blocked"] == len(report["case_runs"])
    assert report["case_counts"]["blocked"] == len(report["case_runs"])
    assert len(report["case_runs"]) >= 15
    assert {case["chain_id"] for case in report["case_runs"]} == {
        "I-LINK", "I-LIVE", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME",
        "A-ECOM", "A-IP", "A-THEME",
    }
    assert all((root / "cases" / str(case["case_run_id"]) / "case.json").is_file() for case in report["case_runs"])
    assert all(case.get("evidence") for case in report["case_runs"])
