from __future__ import annotations
from .policy import SourcePolicy
import hashlib
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field

class LiveProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]] | dict[str, Any]: ...
class LiveSourceSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    limit: int = Field(5, ge=1)
    allowed_platforms: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    authorization_required: bool = False
class LiveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    url: str
    platform: str | None = None
    platform_content_id: str | None = None
    title: str | None = None
    authorization_status: str | None = None
    evidence: list[Any] = Field(default_factory=list)
class LiveSourceSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    results: list[LiveResult] = Field(default_factory=list)
    validation: list[dict[str, Any]] = Field(default_factory=list)

def canonical_url(value: str) -> str:
    p=urlsplit(value.strip()); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/") or "/",urlencode(q),""))
class LiveSourceSearchSkill:
    def __init__(self, provider: LiveProvider|None=None, source_policy: SourcePolicy|None=None):
        self.provider=provider
        self.source_policy=source_policy or SourcePolicy.load()
    def run(self, request: LiveSourceSearchInput) -> LiveSourceSearchOutput:
        if self.provider is None: return LiveSourceSearchOutput(status="blocked", validation=[{"reason":"provider_missing"}])
        try: raw=self.provider.search(request.query,request.limit)
        except Exception as exc: return LiveSourceSearchOutput(status="blocked",validation=[{"reason":"provider_error","detail":type(exc).__name__}])
        rows=raw.get("results",[]) if isinstance(raw,dict) else raw; results=[]; seen=set(); review=False; validation=[]
        for row in rows if isinstance(rows,list) else []:
            if not isinstance(row,dict) or not isinstance(row.get("url"),str): continue
            decision=self.source_policy.validate_live(row["url"], row)
            if not decision.allowed:
                validation.append({"reason":decision.reason,"status":decision.status})
                continue
            row=dict(row,platform=decision.platform)
            url=canonical_url(row["url"])
            if url in seen: continue
            seen.add(url)
            if request.allowed_platforms and row.get("platform") not in request.allowed_platforms: continue
            required_unknown = any(row.get(k) is None for k in request.required)
            if any(not self._match(row,k) for k in request.required) and not required_unknown: continue
            if required_unknown: review = True
            auth=row.get("authorization_status")
            if request.authorization_required and auth == "unknown": review=True
            results.append(LiveResult(candidate_id=str(row.get("candidate_id") or hashlib.sha256(url.encode()).hexdigest()[:16]),url=url,platform=row.get("platform"),platform_content_id=row.get("platform_content_id"),title=row.get("title"),authorization_status=auth,evidence=row.get("evidence") if isinstance(row.get("evidence"),list) else []))
        return LiveSourceSearchOutput(status="needs_review" if review else ("success" if results else "no_candidate"),results=results,validation=validation + ([{"reason":"candidate_only", "recording_verified":False}] if results else []))
    @staticmethod
    def _match(row,key):
        value=row.get(key, row.get("content_type") if key=="live" else None)
        return value is True or (isinstance(value,(list,tuple,set)) and key in value) or str(value).lower()==key.lower()
