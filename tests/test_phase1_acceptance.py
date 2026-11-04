import pytest

from orchestrator.phase1_acceptance import (
    RESULT_STATUSES,
    aggregate_acceptance,
    assess_case_acceptance,
)


def delivery_snapshot(**overrides):
    value = {
        "delivery_enabled": False,
        "auto_delivery_enabled": False,
        "advertiser_id": None,
        "awemes": [],
        "products": [],
        "promotion_goal": None,
        "adlab_scene": None,
    }
    value.update(overrides)
    return value


def test_result_statuses_are_uniform_and_poll_timeout_is_not_terminal():
    assert RESULT_STATUSES == {"passed", "failed", "blocked", "inconclusive", "poll_timeout"}
    result = assess_case_acceptance(
        submission={"accepted": True},
        lifecycle={"status": "poll_timeout"},
        delivery_before=delivery_snapshot(),
        delivery_after=delivery_snapshot(),
    )
    assert result["status"] == "poll_timeout"
    assert result["terminal"] is False
    assert result["passed"] is False


def test_complete_case_requires_delivery_safety_before_and_after():
    result = assess_case_acceptance(
        submission={"accepted": True, "task_id": "t1"},
        lifecycle={"status": "completed"},
        delivery_before=delivery_snapshot(),
        delivery_after=delivery_snapshot(),
        phase_integrity=True,
        business_output=True,
    )
    assert result["status"] == "passed"


def test_delivery_enabled_or_nonempty_target_fails_even_if_task_completed():
    result = assess_case_acceptance(
        submission={"accepted": True, "task_id": "t1"},
        lifecycle={"status": "completed"},
        delivery_before=delivery_snapshot(delivery_enabled=True),
        delivery_after=delivery_snapshot(products=[{"id": "p"}]),
        phase_integrity=True,
        business_output=True,
    )
    assert result["status"] == "failed"


@pytest.mark.parametrize("missing", ["delivery_enabled", "advertiser_id", "awemes", "products", "promotion_goal", "adlab_scene"])
def test_missing_critical_delivery_field_is_inconclusive(missing):
    before = delivery_snapshot()
    after = delivery_snapshot()
    before.pop(missing)
    result = assess_case_acceptance(
        submission={"accepted": True, "task_id": "t1"}, lifecycle={"status": "completed"},
        delivery_before=before, delivery_after=after,
        phase_integrity=True, business_output=True,
    )
    assert result["status"] == "inconclusive"


def test_lifecycle_contract_is_closed_and_unknown_is_inconclusive():
    from orchestrator.phase1_acceptance import LIFECYCLE_STATUSES
    assert LIFECYCLE_STATUSES == {
        "planned", "preflight", "submitted", "running", "poll_timeout",
        "completed", "failed", "blocked", "cancelled", "cleanup_pending",
        "cleaned", "cleanup_failed",
    }
    result = assess_case_acceptance(
        submission={"accepted": True, "task_id": "t1"}, lifecycle={"status": "mystery"},
        delivery_before=delivery_snapshot(), delivery_after=delivery_snapshot(),
        phase_integrity=True, business_output=True,
    )
    assert result["status"] == "inconclusive"


def test_unsafe_delivery_precedes_poll_timeout():
    result = assess_case_acceptance(
        submission={"accepted": True, "task_id": "t1"}, lifecycle={"status": "poll_timeout"},
        delivery_before=delivery_snapshot(delivery_enabled=True), delivery_after=delivery_snapshot(),
    )
    assert result["status"] == "failed"


def test_submission_requires_acceptance_and_task_or_resource_id():
    base = dict(lifecycle={"status": "completed"}, delivery_before=delivery_snapshot(), delivery_after=delivery_snapshot(), phase_integrity=True, business_output=True)
    assert assess_case_acceptance(submission={"accepted": False, "task_id": "t1"}, **base)["status"] == "failed"
    assert assess_case_acceptance(submission={"accepted": True}, **base)["status"] == "inconclusive"
    assert assess_case_acceptance(submission={"accepted": True, "resource_id": "r1"}, **base)["status"] == "passed"


def test_aggregate_does_not_turn_inconclusive_or_timeout_into_pass():
    result = aggregate_acceptance([
        {"status": "passed"}, {"status": "inconclusive"}, {"status": "poll_timeout"}
    ])
    assert result["status"] == "inconclusive"
    assert result["passed"] is False
    assert result["poll_timeout"] == 1
