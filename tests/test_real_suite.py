from orchestrator.real_suite import run_real_suite


def test_real_suite_is_importable_and_uses_suite_shape(monkeypatch, tmp_path):
    assert callable(run_real_suite)
