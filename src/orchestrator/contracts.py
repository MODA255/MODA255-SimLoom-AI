from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class SkillRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    protocol_version: str
    daily_run_id: str
    branch_id: str
    node_run_id: str
    attempt_id: str
    skill_id: str
    skill_version: str
    input: dict[str, Any]
    constraints: dict[str, Any]
    input_refs: list[str]
    deadline_at: str
    config_snapshot_hash: str

class SkillResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['ok','no_candidate','rejected','needs_review','blocked','error']
    result: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
