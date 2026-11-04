import json
from pathlib import Path

from orchestrator.adapters import RemoteAdapter
from orchestrator.phase1_runner import run_phase1
from orchestrator.phase1_suite import load_phase1_suite

CONFIG = Path(__file__).parents[1] / "configs" / "daily-nine-chain-phase1.json"


class SafeAdapter:
    safe_write = True
    def ingest_url(self, payload): return {"id": "link"}
    def ingest_file(self, payload): return {"id": "upload"}
    def get_asset(self, rid): return {"id": rid, "status": "ready"}
    def get_asset_progress(self, rid): return {"status": "ready"}


def test_remote_adapter_reads_top_level_or_target_safe_write_defaulting_false():
    assert RemoteAdapter({}).safe_write is False
    assert RemoteAdapter({"target": {"safe_write": True}}).safe_write is True
    assert RemoteAdapter({"safe_write": True}).safe_write is True
def test_safe_write_defaults_to_blocked_without_config_or_adapter_consent(tmp_path):
    result = run_phase1(SafeAdapter(), {"phase1": {"suite": str(CONFIG)}}, "r", tmp_path)
    assert result["status"] == "blocked"
    assert result["writes"] == 0


def test_safe_write_requires_both_explicit_true_values(tmp_path):
    suite = load_phase1_suite(CONFIG)
    result = run_phase1(object(), {"phase1": {"safe_write": True}}, "r1", tmp_path, suite=suite, preflight={"status": "ready"})
    assert result["status"] == "blocked"
    result = run_phase1(SafeAdapter(), {"phase1": {"safe_write": True}}, "r2", tmp_path, suite=suite, preflight={"status": "ready"})
    assert result["status"] == "completed"


def test_adapter_false_blocks_even_when_config_true(tmp_path):
    adapter = SafeAdapter(); adapter.safe_write = False
    result = run_phase1(adapter, {"phase1": {"safe_write": True}}, "r", tmp_path, suite=load_phase1_suite(CONFIG), preflight={"status": "ready"})
    assert result["status"] == "blocked"
    assert result["writes"] == 0
