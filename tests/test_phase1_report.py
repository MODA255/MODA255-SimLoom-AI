import json
from pathlib import Path

from orchestrator.phase1_report import build_phase1_report, write_phase1_report
from orchestrator.repository import Repository
from orchestrator.resource_registry import ResourceRegistry


CHAIN_IDS = ["I-LINK", "I-LIVE", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME", "A-ECOM", "A-IP", "A-THEME"]


def test_phase1_report_has_nine_chain_status_contract():
    report = build_phase1_report({"run_id": "run-1", "case_runs": [
        {"chain_id": "I-LINK", "status": "planned"},
        {"chain_id": "M-IP", "status": "completed", "hard_acceptance": True},
        {"chain_id": "A-IP", "status": "blocked"},
        {"chain_id": "A-THEME", "status": "inconclusive"},
        {"chain_id": "I-LIVE", "status": "disabled"},
    ]})
    assert report["chain_ids"] == CHAIN_IDS
    assert {"planned", "passed", "poll_timeout", "running", "unknown", "submitted", "terminal", "executed", "blocked", "inconclusive", "disabled"} <= set(report["counts"])
    assert report["counts"]["planned"] == 5
    assert report["counts"]["submitted"] == 1
    assert report["counts"]["terminal"] == 1
    assert report["counts"]["executed"] == 1
    assert report["counts"]["blocked"] == 1
    assert report["counts"]["inconclusive"] == 1
    assert report["counts"]["disabled"] == 1


def test_write_phase1_report_directory_contract_and_protected_cleanup(tmp_path):
    repo = Repository(tmp_path / "state.db")
    registry = ResourceRegistry(repo)
    pending = registry.register_pending("run-1", "case-1", "upload", case_run_id="cr-1")
    unknown = registry.mark_submission_unknown("run-1", "case-2", "upload", "timeout", case_run_id="cr-2")
    paths = write_phase1_report(
        {"run_id": "run-1", "case_runs": [{"case_run_id": "cr-1", "chain_id": "I-UPLOAD", "status": "submitted"}]},
        tmp_path / "runs", resource_registry=registry, preflight={"status": "ready"}, run={"id": "run-1"},
    )
    root = Path(paths["root"])
    assert (root / "run.json").is_file()
    assert (root / "preflight.json").is_file()
    assert (root / "resource-registry.json").is_file()
    assert (root / "cleanup-plan.json").is_file()
    assert (root / "daily-report.json").is_file()
    assert (root / "daily-report.md").is_file()
    assert (root / "cases" / "cr-1" / "case.json").is_file()
    cleanup = json.loads((root / "cleanup-plan.json").read_text())
    assert cleanup["remote_deletes_executed"] is False
    assert all(item["protected"] for item in cleanup["actions"] if item["resource_id"] in {pending["id"], unknown["id"]})


def test_resource_report_matches_registry_and_never_deletes(tmp_path):
    repo = Repository(tmp_path / "state.db")
    registry = ResourceRegistry(repo)
    registry.register_pending("run-2", "case-1", "material", case_run_id="cr-1")
    paths = write_phase1_report({"run_id": "run-2", "case_runs": []}, tmp_path / "runs", resource_registry=registry)
    exported = json.loads(Path(paths["resource_registry"]).read_text())
    assert exported == registry.list_resources("run-2")
    assert not hasattr(registry, "delete_remote")
