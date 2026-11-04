import json
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.repository import Repository
from orchestrator.resource_registry import ResourceRegistry


def test_registration_is_pending_before_write_and_completed_with_resource_id(tmp_path):
    repo = Repository(tmp_path / "state.db")
    registry = ResourceRegistry(repo)

    pending = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")
    assert pending["status"] == "submission_pending"
    assert pending["resource_id"] is None
    assert pending["cleanup_after"] == (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()

    completed = registry.mark_submitted(pending["id"], "mat-123")
    assert completed["status"] == "registered"
    assert completed["resource_id"] == "mat-123"


def test_unknown_submission_is_protected_and_cannot_be_marked_clean(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))

    record = registry.mark_submission_unknown("run-2", "case-2", "upload", "timeout after write", case_run_id="cr-legacy")

    assert record["status"] == "submission_unknown"
    assert record["protected"] is True
    assert "timeout" in record["protection_reason"]
    assert registry.cleanup_candidates() == []


def test_duplicate_registration_is_idempotent(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))

    first = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")
    second = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")

    assert second == first
    assert len(registry.list_resources()) == 1


def test_case_run_id_is_required_and_part_of_idempotency_key(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))

    with pytest.raises(ValueError, match="case_run_id is required"):
        registry.register_pending("run-1", "case-1", "material")
    first = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")
    second = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")
    assert second == first
    with pytest.raises(ValueError, match="case_run_id"):
        registry.register_pending("run-1", "case-1", "material", case_run_id="cr-2")


def test_reconcile_requires_id_is_non_overwriting_idempotent_and_records_evidence(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    record = registry.mark_submission_unknown("run-1", "case-1", "upload", "timeout", case_run_id="cr-1")

    with pytest.raises(ValueError, match="resource_id is required"):
        registry.reconcile(record["id"], "")
    reconciled = registry.reconcile(record["id"], "remote-1", evidence={"source": "lookup"})
    assert reconciled["resource_id"] == "remote-1"
    assert reconciled["metadata"]["reconcile_evidence"] == {"source": "lookup"}
    with pytest.raises(ValueError, match="submission_unknown"):
        registry.reconcile(record["id"], "remote-1")


def test_reconcile_requires_unknown_status_and_updates_status_created_at_and_redacts_evidence(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    pending = registry.register_pending("run-1", "case-1", "upload", case_run_id="cr-1")
    with pytest.raises(ValueError, match="submission_unknown"):
        registry.reconcile(pending["id"], "remote-1")

    unknown = registry.mark_submission_unknown("run-1", "case-2", "upload", "timeout", case_run_id="cr-2")
    created_at = "2025-01-02T03:04:05+00:00"
    reconciled = registry.reconcile(
        unknown["id"], "remote-2", created_at=created_at,
        evidence={"authorization": "Bearer very-secret", "nested": {"cookie": "x"}},
    )
    assert reconciled["status"] == "registered"
    assert reconciled["created_at"] == created_at
    assert reconciled["metadata"]["reconcile_evidence"] == {"authorization": "[REDACTED]", "nested": {"cookie": "[REDACTED]"}}


def test_mark_submission_unknown_requires_case_run_id_and_cannot_regress_registered(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    with pytest.raises(ValueError, match="case_run_id is required"):
        registry.mark_submission_unknown("run-1", "case-1", "upload", "timeout")
    pending = registry.register_pending("run-1", "case-1", "upload", case_run_id="cr-1")
    registry.mark_submitted(pending["id"], "remote-1")
    with pytest.raises(ValueError, match="registered"):
        registry.mark_submission_unknown("run-1", "case-1", "upload", "late timeout", case_run_id="cr-1")


def test_cleanup_candidates_respect_status_and_retry_time(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"), cleanup_days=0)
    now = datetime.now(timezone.utc)
    pending = registry.register_pending("run-1", "case-1", "upload", case_run_id="cr-1")
    registry.mark_submitted(pending["id"], "remote-1")
    registry.mark_terminal(pending["id"], "completed")
    registry.mark_cleanup_submitted(pending["id"])
    assert registry.cleanup_candidates(now) == []

    retry = registry.register_pending("run-1", "case-2", "upload", case_run_id="cr-2")
    registry.mark_submitted(retry["id"], "remote-2")
    registry.mark_terminal(retry["id"], "completed")
    registry.record_cleanup_error(retry["id"], "network", next_retry_at=now + timedelta(hours=1))
    assert registry.cleanup_candidates(now) == []
    assert registry.cleanup_candidates(now + timedelta(hours=1))[0]["id"] == retry["id"]


def test_cleanup_candidates_use_utc_datetime_boundary(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"), cleanup_days=7)
    record = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")
    registry.mark_submitted(record["id"], "remote-1")
    registry.mark_terminal(record["id"], "completed")
    stored = registry.list_resources()[0]
    boundary = datetime.fromisoformat(stored["cleanup_after"])
    assert registry.cleanup_candidates(boundary - timedelta(microseconds=1)) == []
    assert registry.cleanup_candidates(boundary)[0]["id"] == record["id"]


def test_nested_metadata_redacts_sensitive_strings_and_lists(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    record = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1", metadata={"items": [{"AccessToken": "abc", "note": "secret sauce"}]})
    assert record["metadata"]["items"][0]["AccessToken"] == "[REDACTED]"
    assert record["metadata"]["items"][0]["note"] == "secret sauce"


def test_export_registry_returns_all_records_without_deleting_remote_resources(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    pending = registry.register_pending("run-1", "case-1", "material", case_run_id="cr-1")
    registry.mark_submitted(pending["id"], "mat-1")
    registry.mark_submission_unknown("run-1", "case-2", "upload", "ambiguous", case_run_id="cr-2")

    exported = registry.export()
    payload = json.loads(exported)

    assert {item["resource_id"] for item in payload} == {"mat-1", None}
    assert {item["status"] for item in payload} == {"registered", "submission_unknown"}
    assert not hasattr(registry, "delete_remote")


def test_mark_submitted_registered_same_resource_is_idempotent_and_preserves_lifecycle(tmp_path):
    registry = ResourceRegistry(Repository(tmp_path / "state.db"))
    pending = registry.register_pending("run-1", "case-1", "upload", case_run_id="cr-1")
    registry.mark_submitted(pending["id"], "remote-1")
    terminal = registry.mark_terminal(pending["id"], "completed")

    repeated = registry.mark_submitted(pending["id"], "remote-1", terminal_status=None)

    assert repeated == terminal
    assert repeated["status"] == "registered"
    assert repeated["terminal_status"] == "completed"
    with pytest.raises(ValueError, match="resource_id cannot be overwritten"):
        registry.mark_submitted(pending["id"], "remote-2")


def test_repository_update_resource_rejects_case_run_id_updates(tmp_path):
    repo = Repository(tmp_path / "state.db")
    record = ResourceRegistry(repo).register_pending("run-1", "case-1", "upload", case_run_id="cr-1")

    with pytest.raises(KeyError, match="case_run_id"):
        repo.update_resource(record["id"], {"case_run_id": "cr-2"})

    assert repo.get_resource(record["id"])["case_run_id"] == "cr-1"
