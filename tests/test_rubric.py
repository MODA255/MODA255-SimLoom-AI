from orchestrator.rubric import evaluate_chain


def test_evaluate_chain_passes_completed_task_with_remote_output_and_delivery_off():
    result = evaluate_chain({
        "chain_id": "manual_theme",
        "execution_mode": "manual",
        "scene_type": "auto_theme_discovery",
        "expected_recipe": "auto_theme_discovery",
        "task": {
            "id": "task-1",
            "status": "completed",
            "recipe": "auto_theme_discovery",
            "delivery_enabled": False,
            "auto_delivery_enabled": False,
            "completed_phases": 13,
            "total_phases": 13,
        },
        "videos": [{
            "id": "video-1",
            "status": "created",
            "oss_url": "https://storage.example/video.mp4",
            "duration": 33.2,
            "resolution": "1080x1920",
            "file_size": 100,
        }],
        "expected_video_count": 1,
        "input_valid": True,
    })

    assert result["overall"] == "passed"
    assert result["passed"] is True
    assert all(item["status"] == "passed" for item in result["rubric"].values())
    assert result["failure_modes"] == []


def test_evaluate_chain_distinguishes_waiting_parent_and_delivery_block():
    result = evaluate_chain({
        "chain_id": "auto_ecom",
        "execution_mode": "automation",
        "scene_type": "ecom_mix",
        "expected_recipe": "ecom_remix_viral_learning",
        "parent_task": {"id": "parent-1", "status": "waiting_live"},
        "task": {
            "id": "child-1",
            "status": "completed",
            "recipe": "ecom_remix_viral_learning",
            "delivery_enabled": False,
            "auto_delivery_enabled": False,
            "completed_phases": 21,
            "total_phases": 21,
        },
        "videos": [{
            "id": "video-1",
            "status": "created",
            "oss_url": "https://storage.example/video.mp4",
            "duration": 30,
            "resolution": "1080x1920",
            "file_size": 100,
        }],
        "expected_video_count": 1,
        "input_valid": True,
        "promotion_status": "blocked",
        "promotion_block_reason": "delivery_disabled",
    })

    assert result["overall"] == "passed_with_warning"
    assert result["passed"] is True
    assert "PARENT_WAITING_LIVE" in result["failure_modes"]
    assert "PROMOTION_BLOCKED_BY_POLICY" in result["failure_modes"]


def test_evaluate_chain_rejects_delivery_policy_violation_and_missing_output():
    result = evaluate_chain({
        "chain_id": "bad",
        "expected_recipe": "ecom_remix",
        "task": {
            "id": "task-2",
            "status": "completed",
            "recipe": "wrong",
            "delivery_enabled": True,
            "auto_delivery_enabled": True,
            "completed_phases": 20,
            "total_phases": 21,
        },
        "videos": [],
        "expected_video_count": 1,
        "input_valid": True,
    })

    assert result["overall"] == "failed"
    assert result["passed"] is False
    assert "DELIVERY_POLICY_VIOLATION" in result["failure_modes"]
    assert "OUTPUT_MISSING" in result["failure_modes"]
    assert "SCENE_ROUTING_MISMATCH" in result["failure_modes"]
