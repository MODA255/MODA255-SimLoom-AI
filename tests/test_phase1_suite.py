import json
from pathlib import Path

import pytest

from orchestrator.phase1_suite import (
    CONDITIONAL_CHAINS,
    MANDATORY_REAL_CHAINS,
    PHASE1_CHAIN_IDS,
    derive_case_runs,
    load_phase1_suite,
    summarize_case_runs,
    validate_phase1_config,
    dependency_satisfied,
)


CONFIG = Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json"


def test_phase1_config_covers_nine_chains_and_release_policy():
    config = load_phase1_suite(CONFIG)

    assert set(config["enabled_chains"]) == set(PHASE1_CHAIN_IDS)
    assert set(config["mandatory_real_chains"]) == set(MANDATORY_REAL_CHAINS)
    assert set(config["conditional_chains"]) == set(CONDITIONAL_CHAINS)
    assert config["min_p0_cases"] == 10
    assert config["release_mode"] == "limited_production"
    assert config["delivery_enabled"] is False
    assert config["auto_delivery_enabled"] is False
    assert config["cleanup_mode"] == "register_and_protect"


def test_phase1_planned_scope_and_p0_distribution():
    config = load_phase1_suite(CONFIG)
    cases = [case for chain in config["chains"] for case in chain["cases"]]

    assert 15 <= len(cases) <= 25
    assert sum(case["priority"] == "P0" for case in cases) == 10
    assert sum(case["priority"] == "P0" and case["chain_id"] == "I-LINK" for case in cases) >= 2
    assert all(sum(case["priority"] == "P0" and case["chain_id"] == chain_id for case in cases) >= 1 for chain_id in PHASE1_CHAIN_IDS if chain_id != "I-LINK")
    assert {case["chain_id"] for case in cases} == set(PHASE1_CHAIN_IDS)


def test_p0_distribution_rejects_missing_non_i_link_chain():
    config = load_phase1_suite(CONFIG)
    config["chains"] = [chain for chain in config["chains"] if chain["chain_id"] != "A-IP"]
    config["enabled_chains"].remove("A-IP")
    config["mandatory_real_chains"].remove("A-IP")
    errors = validate_phase1_config(config)
    assert any("P0" in error and "A-IP" in error for error in errors)


def test_nested_historical_task_ids_are_rejected():
    config = load_phase1_suite(CONFIG)
    config["chains"][0]["cases"][0]["metadata"] = {"nested": [{"task_id": "old"}]}
    errors = validate_phase1_config(config)
    assert any("historical task ID" in error for error in errors)
def test_case_runner_must_match_chain_runner():
    raw = json.loads(CONFIG.read_text())
    raw["chains"][0]["cases"][0]["runner"] = "wrong_runner"
    with pytest.raises(ValueError, match="runner"):
        load_phase1_suite(raw)


    config = load_phase1_suite(CONFIG)
    duplicate = dict(config["chains"][1]["cases"][0])
    config["chains"][0]["cases"].append(duplicate)

    errors = validate_phase1_config(config)

    assert any("duplicate case_id" in error for error in errors)
    with pytest.raises(ValueError, match="duplicate case_id"):
        load_phase1_suite(config)


def test_case_run_ids_remain_unique_for_duplicate_case_ids():
    config = load_phase1_suite(CONFIG)
    duplicate = dict(config["chains"][1]["cases"][0])
    config["chains"][0]["cases"].append(duplicate)

    runs = derive_case_runs(config, run_id="r", batch_id="b")

    assert len({item["case_run_id"] for item in runs}) == len(runs)


def test_phase1_config_has_no_historical_task_ids():
    config = load_phase1_suite(CONFIG)

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in {"task_id", "parent_task_id", "child_task_id"}
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    list(walk(config))
    assert validate_phase1_config(config) == []


def test_case_run_ids_are_unique_across_batch_and_run_dimensions():
    config = load_phase1_suite(CONFIG)
    first = derive_case_runs(config, run_id="same", batch_id="batch-a")
    second = derive_case_runs(config, run_id="same", batch_id="batch-b")
    third = derive_case_runs(config, run_id="other", batch_id="batch-a")
    assert {item["case_run_id"] for item in first}.isdisjoint({item["case_run_id"] for item in second})
    assert {item["case_run_id"] for item in first}.isdisjoint({item["case_run_id"] for item in third})
    assert all("batch-a" in item["case_run_id"] for item in first)


def test_case_runs_derive_fresh_run_batch_and_case_ids():
    config = load_phase1_suite(CONFIG)
    runs = derive_case_runs(config, run_id="run-20261029-001", batch_id="batch-001")

    assert len(runs) == sum(len(chain["cases"]) for chain in config["chains"])
    assert {item["run_id"] for item in runs} == {"run-20261029-001"}
    assert {item["batch_id"] for item in runs} == {"batch-001"}
    assert len({item["case_run_id"] for item in runs}) == len(runs)
    assert all(item["case_run_id"].startswith("batch-001:run-20261029-001:") for item in runs)
    assert all(item["status"] == "planned" for item in runs)
    assert all("task_id" not in item for item in runs)


def test_case_run_stats_exclude_poll_timeout_from_terminal():
    config = load_phase1_suite(CONFIG)
    runs = derive_case_runs(config, run_id="r", batch_id="b")
    runs[0]["status"] = "poll_timeout"
    stats = summarize_case_runs(runs)
    assert stats["submitted"] == 0
    assert stats["terminal"] == 0


def test_case_run_stats_distinguish_planned_submitted_terminal_and_executed():
    config = load_phase1_suite(CONFIG)
    runs = derive_case_runs(config, run_id="r", batch_id="b")
    runs[0]["status"] = "submitted"
    runs[1]["status"] = "completed"
    runs[1]["hard_acceptance"] = True
    runs[2]["status"] = "blocked"
    runs[3]["status"] = "inconclusive"
    runs[4]["status"] = "failed"

    stats = summarize_case_runs(runs)

    assert stats == {
        "planned": len(runs),
        "submitted": 3,
        "terminal": 2,
        "executed": 1,
        "blocked": 1,
        "inconclusive": 1,
        "disabled": 0,
    }


def test_dependency_groups_mean_any_of_not_all_of():
    config = load_phase1_suite(CONFIG)
    case = next(case for chain in config["chains"] for case in chain["cases"] if case["case_id"] == "M-ECOM-001")
    assert case["depends_on"] == [{"any_of": ["I-LINK", "I-UPLOAD"]}]
    assert dependency_satisfied(case, {"I-LINK"})
    assert dependency_satisfied(case, {"I-UPLOAD"})
    assert not dependency_satisfied(case, set())


def test_invalid_phase1_config_reports_scope_errors():
    config = load_phase1_suite(CONFIG)
    config["enabled_chains"] = ["I-LINK"]

    errors = validate_phase1_config(config)

    assert any("enabled_chains" in error for error in errors)
    with pytest.raises(ValueError, match="enabled_chains"):
        load_phase1_suite(config)
