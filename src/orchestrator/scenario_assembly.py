from __future__ import annotations
from .policy import SourcePolicy
from .feature_validation import validate_features
import hashlib, json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
class ScenarioAssemblyInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assets:list[dict[str,Any]]=Field(default_factory=list)
    topic:dict[str,Any]=Field(default_factory=dict)
    fixed_config:dict[str,Any]=Field(default_factory=dict)
    feature_profile_id:str='F0'
    dynamic_config:dict[str,Any]=Field(default_factory=dict)
class ScenarioAssemblyOutput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:str
    scenario_id:str|None=None
    assets:list[dict[str,Any]]=Field(default_factory=list)
    topic:dict[str,Any]=Field(default_factory=dict)
    config:dict[str,Any]=Field(default_factory=dict)
    task:Any=None
    validation:list[dict[str,Any]]=Field(default_factory=list)
    profile_id:str='F0'
class ScenarioAssemblySkill:
    def __init__(self, source_policy: SourcePolicy | None = None):
        self.source_policy = source_policy or SourcePolicy.load()

    def run(self, request:ScenarioAssemblyInput)->ScenarioAssemblyOutput:
        if not request.assets: return ScenarioAssemblyOutput(status='validation_failed',validation=[{'reason':'assets_missing'}])
        if any(a.get('status')!='ready' for a in request.assets): return ScenarioAssemblyOutput(status='blocked',validation=[{'reason':'asset_not_ready'}])
        if not request.topic.get('topic'): return ScenarioAssemblyOutput(status='validation_failed',validation=[{'reason':'topic_missing'}])
        if any(not a.get('asset_id') for a in request.assets): return ScenarioAssemblyOutput(status='validation_failed',validation=[{'reason':'asset_id_missing'}])
        if request.fixed_config.get('publish') is True or request.fixed_config.get('delivery') is True: return ScenarioAssemblyOutput(status='validation_failed',validation=[{'reason':'delivery_fields_must_be_disabled'}])
        for asset in request.assets:
            source_type = asset.get('source_type')
            url = asset.get('source_url') or asset.get('url')
            if source_type not in ('live', 'link', 'content'):
                return ScenarioAssemblyOutput(status='blocked', validation=[{'reason':'source_type_unverified'}])
            decision = (self.source_policy.validate_live if source_type == 'live' else self.source_policy.validate_content)(url, asset)
            if not decision.allowed:
                return ScenarioAssemblyOutput(status='blocked', validation=[{'reason':decision.reason, 'status':decision.status}])
        feature_result = validate_features(request.fixed_config, request.assets, request.topic, request.feature_profile_id, request.dynamic_config)
        if feature_result['status'] == 'blocked':
            return ScenarioAssemblyOutput(status='blocked', assets=request.assets, topic=request.topic, config=request.fixed_config, profile_id=request.feature_profile_id, validation=[{'reason':'feature_validation', **feature_result}])
        payload={'assets':request.assets,'topic':request.topic,'config':request.fixed_config,'profile_id':request.feature_profile_id}
        sid='scenario-'+hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        return ScenarioAssemblyOutput(status='success',scenario_id=sid,assets=request.assets,topic=request.topic,config=request.fixed_config, profile_id=request.feature_profile_id, validation=[feature_result])
