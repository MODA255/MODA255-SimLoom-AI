import json

from engineering_agent.action_profile import SCHEMA, WORKER_CONFIG_SCHEMA, build_action_profile, build_worker_config_template


def _config(tmp_path):
    script = tmp_path / "scripts" / "catia_task.py"
    script.parent.mkdir()
    script.write_text("print('fixture')\n", encoding="utf-8")
    return {
        "tools": {
            "catia": {
                "bridge_url": "http://worker:8099",
                "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN",
                "actions": {
                    "update_parameters": {
                        "script": "scripts/catia_task.py",
                        "inputs": ["inputs/template.CATPart"],
                        "outputs": ["artifacts/model.step"],
                        "capability_requirements": {
                            "adapter_capabilities": ["parametric-geometry"],
                            "worker_features": ["durable_jobs"],
                        },
                        "recovery_policy": {"on_timeout": "require_human", "max_retries": 1},
                        "validation": {"kind": "catia_parameters"},
                    }
                },
            }
        },
        "windows_bridge": {
            "host": "127.0.0.1",
            "port": 8099,
            "root_dir": "C:/SimLoom/jobs",
            "timeout_seconds": 1800,
            "commands": {
                "catia.update_parameters": [
                    "C:/SimLoom/python/python.exe", "{script}", "{request_file}"
                ],
            },
            "com": {"catia.application": {"prog_id": "CATIA.Application"}},
            "token": "do-not-export",
        },
    }


def test_profile_reports_action_binding_and_defers_windows_checks(tmp_path):
    profile = build_action_profile(_config(tmp_path), project_root=tmp_path)
    assert profile["schema_version"] == SCHEMA
    action = profile["tools"]["catia"]["actions"][0]
    assert action["status"] == "deferred_to_windows_worker"
    assert action["script"]["status"] == "ready"
    assert action["command_check"]["code"] == "WINDOWS_CHECK_DEFERRED"
    assert action["requirements"]["status"] == "declared"
    assert action["artifacts"]["outputs"] == ["artifacts/model.step"]
    assert profile["summary"]["windows_check_deferred"] == 1


def test_profile_is_redacted_and_digest_is_stable(tmp_path):
    config = _config(tmp_path)
    first = build_action_profile(config, project_root=tmp_path)
    second = build_action_profile(config, project_root=tmp_path)
    assert first["profile_digest"] == second["profile_digest"]
    encoded = json.dumps(first, ensure_ascii=False)
    assert "do-not-export" not in encoded
    assert first["windows_bridge"]["com_prog_ids"] == ["catia.application"]
    assert first["tools"]["catia"]["bridge_token_env"] == "SIMLOOM_BRIDGE_TOKEN"


def test_worker_config_template_contains_commands_without_a_secret(tmp_path):
    template = build_worker_config_template(_config(tmp_path))
    assert template["schema_version"] == WORKER_CONFIG_SCHEMA
    assert template["template_only"] is True
    assert template["commands"]["catia.update_parameters"][1] == "{script}"
    assert template["token"] == "<set-on-windows>"
    assert template["token_env"] == "SIMLOOM_BRIDGE_TOKEN"
    assert "do-not-export" not in json.dumps(template, ensure_ascii=False)


def test_profile_flags_missing_script_and_invalid_local_command(tmp_path):
    config = {
        "tools": {"ansys": {"actions": {"solve": {
            "script": "missing.py", "command": ["missing-program", "{script}"],
            "outputs": ["result.json"],
        }}}}
    }
    profile = build_action_profile(config, project_root=tmp_path)
    action = profile["tools"]["ansys"]["actions"][0]
    codes = {issue["code"] for issue in action["issues"]}
    assert action["status"] == "needs_configuration"
    assert "SCRIPT_MISSING" in codes
    assert "COMMAND_EXECUTABLE_NOT_FOUND" in codes
