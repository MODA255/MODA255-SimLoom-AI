import json
from pathlib import Path

from orchestrator.cli import main


def test_daily_evaluate_command_runs_configured_suite(tmp_path, capsys, monkeypatch):
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"chains": [{"chain_id":"manual_theme","task_id":"task-1","execution_mode":"manual","scene_type":"auto_theme_discovery","expected_recipe":"auto_theme_discovery","expected_video_count":1}]}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mode":"simulation","state_db":":memory:","profiles":{"publish":False}}), encoding="utf-8")
    monkeypatch.setattr("orchestrator.cli.run_daily_pipeline", lambda adapter, specs, output_dir: {"chain_count": len(specs), "report_files": {"json": str(Path(output_dir)/"six-chain-report.json")}})
    assert main(["daily-evaluate", "--config", str(config), "--suite", str(suite), "--output-dir", str(tmp_path / "report")]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["chain_count"] == 1


def test_daily_evaluate_rejects_suite_without_six_chain_ids(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"chains": [{"chain_id":"unknown"}]}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mode":"simulation","state_db":":memory:"}), encoding="utf-8")
    try:
        main(["daily-evaluate", "--config", str(config), "--suite", str(suite)])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("invalid suite should be rejected")
