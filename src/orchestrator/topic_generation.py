from __future__ import annotations
import hashlib
from typing import Any, Callable
from pydantic import BaseModel, ConfigDict, Field

class TopicGenerationInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    material_facts: list[dict[str,Any]]=Field(default_factory=list)
    template: str|None=None
    brief: str|None=None
class TopicGenerationOutput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: str
    topic: str|None=None
    brief: str|None=None
    content_angle: str|None=None
    template: str|None=None
    version: str|None=None
    claim_evidence: list[Any]=Field(default_factory=list)
    risk: list[Any]=Field(default_factory=list)
    human_review: bool=False
    validation: list[dict[str,Any]]=Field(default_factory=list)
class TopicGenerationSkill:
    def __init__(self, template_provider:Callable[[str],dict[str,Any]]|None=None, model_provider:Any|None=None): self.template_provider=template_provider; self.model_provider=model_provider
    def run(self, request:TopicGenerationInput)->TopicGenerationOutput:
        if not request.material_facts: return TopicGenerationOutput(status='validation_failed',validation=[{'reason':'material_facts_missing'}])
        if not request.template: return TopicGenerationOutput(status='blocked',validation=[{'reason':'template_missing'}])
        if self.template_provider is None: return TopicGenerationOutput(status='blocked',validation=[{'reason':'template_provider_missing'}])
        if self.model_provider is None: return TopicGenerationOutput(status='blocked',validation=[{'reason':'model_provider_missing'}])
        try: tpl=self.template_provider(request.template); raw=self.model_provider.generate({'template':tpl,'facts':request.material_facts,'brief':request.brief})
        except Exception as exc: return TopicGenerationOutput(status='blocked',validation=[{'reason':'provider_error','detail':type(exc).__name__}])
        if not isinstance(raw,dict) or not raw.get('topic'): return TopicGenerationOutput(status='validation_failed',validation=[{'reason':'topic_missing'}])
        return TopicGenerationOutput(status='success',topic=raw['topic'],brief=raw.get('brief') or None,content_angle=raw.get('content_angle'),template=request.template,version=tpl.get('version') if isinstance(tpl,dict) else None,claim_evidence=raw.get('claim_evidence',[]),risk=raw.get('risk',[]),human_review=bool(raw.get('human_review',False)))
