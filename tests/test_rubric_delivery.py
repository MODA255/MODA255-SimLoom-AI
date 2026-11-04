from orchestrator.rubric import evaluate_chain


def test_missing_delivery_flags_are_unknown_warning_not_policy_violation():
    result = evaluate_chain({
        "expected_recipe": "ecom_remix",
        "task": {"status": "completed", "recipe": "ecom_remix", "completed_phases": 1, "total_phases": 1},
        "videos": [{"id": "v", "status": "created", "oss_url": "https://x/v.mp4", "duration": 1, "resolution": "1080x1920", "file_size": 1}],
        "expected_video_count": 1,
    })
    assert result["overall"] == "passed_with_warning"
    assert "DELIVERY_POLICY_UNKNOWN" in result["failure_modes"]
    assert "DELIVERY_POLICY_VIOLATION" not in result["failure_modes"]
