import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from orchestrator.contracts import SkillRequest, SkillResponse
from orchestrator.engine import Orchestrator
from orchestrator.config import load_config
from orchestrator.repository import Repository

def test_contract_requires_protocol_and_outcome(tmp_path):
    request = SkillRequest(protocol_version='1', daily_run_id='r', branch_id='live', node_run_id='n', attempt_id='a', skill_id='live-source-search', skill_version='0.1', input={}, constraints={}, input_refs=[], deadline_at='2026-01-01T00:00:00Z', config_snapshot_hash='h')
    assert request.skill_id == 'live-source-search'
    response = SkillResponse(outcome='blocked', result=None, evidence=[], checks=[])
    assert response.outcome == 'blocked'

def test_same_run_lock_blocks_second_owner(tmp_path):
    data=json.loads((Path(__file__).parents[1]/'configs/example.json').read_text()); data['state_db']=str(tmp_path/'s.db')
    c=load_config(data); repo=Repository(c.state_db); run=repo.create_run(c.snapshot())
    assert repo.acquire_lease(run,'owner-a') is True
    assert repo.acquire_lease(run,'owner-b') is False
