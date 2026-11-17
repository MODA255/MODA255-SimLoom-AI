import json

from engineering_agent import cli


def test_cli_capabilities_emits_same_readiness_matrix(capsys):
    assert cli.main(["--config", str(cli._default_config()), "capabilities"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "simloom-ai/capabilities/v1"
    assert {item["id"] for item in output["modes"]} >= {"offline", "rehearsal", "connected", "execute"}
    assert set(output["tools"]) == {"catia", "ansys", "adams", "vi-grade"}

