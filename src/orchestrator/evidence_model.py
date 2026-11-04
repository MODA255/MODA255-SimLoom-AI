from typing import Any, Literal
from pydantic import BaseModel, Field


class ExecutionState(BaseModel):
    status: Literal['not_started', 'created', 'running', 'completed', 'failed', 'timeout', 'cancelled'] = 'not_started'
    task_id: str | None = None
    parent_task_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class EvidenceState(BaseModel):
    status: Literal['complete', 'partial', 'missing', 'collection_failed'] = 'missing'
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    node_source: str = 'none'
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    videos: list[dict[str, Any]] = Field(default_factory=list)


class DeliverySafety(BaseModel):
    request_disabled: bool | None = None
    task_disabled: bool | None = None
    delivery_task_count: int | None = None
    delivery_event_count: int | None = None
    verdict: Literal['passed', 'failed', 'insufficient_evidence', 'not_checked'] = 'not_checked'


class ChainVerdict(BaseModel):
    execution_status: str = 'not_started'
    test_status: Literal['passed', 'failed', 'warning', 'blocked', 'inconclusive', 'not_run'] = 'not_run'
    evidence_status: Literal['complete', 'partial', 'missing', 'collection_failed'] = 'missing'
    business_verdict: Literal['pass', 'fail', 'insufficient_evidence', 'not_applicable'] = 'not_applicable'
    issues: list[dict[str, Any]] = Field(default_factory=list)


class ChainEvidence(BaseModel):
    chain_id: str
    execution_mode: str
    scene_type: str | None = None
    submission: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionState = Field(default_factory=ExecutionState)
    polling: list[dict[str, Any]] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    evidence: EvidenceState = Field(default_factory=EvidenceState)
    delivery_safety: DeliverySafety = Field(default_factory=DeliverySafety)
    cleanup: dict[str, Any] = Field(default_factory=dict)
    verdict: ChainVerdict = Field(default_factory=ChainVerdict)
