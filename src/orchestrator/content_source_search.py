from __future__ import annotations

from .policy import SourcePolicy
from .source_probe import SourceProbeGateway, SourceProbeResult

import hashlib
import os
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field


class SearchProviderProtocol(Protocol):
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]] | dict[str, Any]: ...


class ContentSourceSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    limit: int = Field(default=5, ge=1)
    allowed_platforms: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    must_be_new_asset: bool = False
    require_authorization: bool = False
    allow_fixed_fallback: bool = False


class ContentSourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    source_url: str
    platform: str | None = None
    title: str | None = None
    content_type: str | None = None
    duration: float | int | None = None
    orientation: str | None = None
    audio_features: Any = None
    visual_features: Any = None
    business_tags: list[str] = Field(default_factory=list)
    freshness: Any = None
    duplicate_status: str | None = None
    authorization_status: str | None = None
    evidence: list[Any] = Field(default_factory=list)
    selection_reason: str | None = None
    next_action: str | None = None
    url_type: str | None = None


class ContentSourceSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    candidates: list[ContentSourceCandidate] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    validation: list[dict[str, Any]] = Field(default_factory=list)


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


class ContentSourceSearchSkill:
    def __init__(self, provider: SearchProviderProtocol | None = None, remote_lookup: Any | None = None, source_policy: SourcePolicy | None = None, probe_gateway: SourceProbeGateway | None = None):
        self.source_policy = source_policy or SourcePolicy.load()
        self.provider = provider
        self.remote_lookup = remote_lookup
        self.probe_gateway = probe_gateway

    def run(self, request: ContentSourceSearchInput) -> ContentSourceSearchOutput:
        if self.provider is None:
            if request.allow_fixed_fallback and os.getenv("TEST_SOURCE_URL"):
                source = os.environ["TEST_SOURCE_URL"]
                decision = self.source_policy.validate_probe_target(source)
                if not decision.allowed or (request.allowed_platforms and decision.platform is not None and decision.platform not in request.allowed_platforms):
                    return ContentSourceSearchOutput(status="blocked", validation=[{"reason": decision.reason}])
                url = canonical_url(source)
                candidate = self._probe_candidate({"source_url": url, "platform": decision.platform, "authorization_status": "unknown"}, url, "explicit fixed fallback")
                if candidate is None:
                    return ContentSourceSearchOutput(status="blocked", validation=[{"reason": "source_probe_failed"}])
                if request.allowed_platforms and candidate.platform not in request.allowed_platforms:
                    return ContentSourceSearchOutput(status="blocked", validation=[{"reason": "platform_not_allowed"}])
                return ContentSourceSearchOutput(status="needs_review" if request.require_authorization else "fixed_source_fallback", candidates=[candidate])
            return ContentSourceSearchOutput(status="blocked", validation=[{"reason": "search_provider_missing"}], evidence=[])
        try:
            raw = self.provider.search(request.query, request.limit)
        except Exception as exc:
            return ContentSourceSearchOutput(status="blocked", validation=[{"reason": "provider_error", "detail": type(exc).__name__}])
        rows = raw.get("results", []) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            rows = []
        candidates: list[ContentSourceCandidate] = []
        seen: set[str] = set()
        review = False
        validation: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source = row.get("source_url") or row.get("url")
            if not isinstance(source, str) or not source.strip():
                continue
            decision = self.source_policy.validate_probe_target(source, row)
            if not decision.allowed:
                validation.append({"reason": decision.reason, "status": decision.status})
                continue
            if row.get("url_type") == "live_url" or row.get("content_type") == "live":
                validation.append({"reason": "live_requires_live_source_search"})
                continue
            row = dict(row, platform=decision.platform)
            url = canonical_url(source)
            if url in seen:
                validation.append({"reason": "duplicate_url", "source_url": url})
                continue
            seen.add(url)
            platform = row.get("platform")
            if request.allowed_platforms and platform is not None and platform not in request.allowed_platforms:
                continue
            if any(not self._matches_required(row, key) for key in request.required):
                continue
            duplicate = row.get("duplicate_status")
            if request.must_be_new_asset and duplicate in {"duplicate", "known", "existing"}:
                validation.append({"reason": "duplicate", "source_url": url})
                continue
            candidate = self._probe_candidate(row, url, "provider result")
            if candidate is None:
                validation.append({"reason": "source_probe_failed", "source_url": url})
                continue
            if request.allowed_platforms and candidate.platform not in request.allowed_platforms:
                continue
            if request.require_authorization and candidate.authorization_status not in {"confirmed", "authorized"}:
                review = True
            candidates.append(candidate)
        if review and candidates:
            return ContentSourceSearchOutput(status="needs_review", candidates=candidates, validation=validation)
        return ContentSourceSearchOutput(status="success" if candidates else ("blocked" if any(v.get("reason") not in {"duplicate", "duplicate_url"} for v in validation) else "no_candidate"), candidates=candidates, validation=validation)

    @staticmethod
    def _matches_required(row: dict[str, Any], key: str) -> bool:
        value = row.get(key)
        if value is None and key == "video":
            value = row.get("content_type")
        if isinstance(value, bool):
            return value
        if isinstance(value, (list, tuple, set)):
            return key in value
        return str(value).lower() == key.lower()

    @staticmethod
    def _candidate(row: dict[str, Any], url: str, reason: str) -> ContentSourceCandidate:
        evidence = row.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = [evidence]
        return ContentSourceCandidate(
            candidate_id=str(row.get("candidate_id") or hashlib.sha256(url.encode()).hexdigest()[:16]),
            source_url=url, platform=row.get("platform"), title=row.get("title"), content_type=row.get("content_type"),
            duration=row.get("duration"), orientation=row.get("orientation"), audio_features=row.get("audio_features"),
            visual_features=row.get("visual_features"), business_tags=row.get("business_tags") or [], freshness=row.get("freshness"),
            duplicate_status=row.get("duplicate_status"), authorization_status=row.get("authorization_status"), evidence=evidence,
            url_type=row.get("url_type"),
            selection_reason=row.get("selection_reason") or reason, next_action=row.get("next_action"),
        )

    def _probe_candidate(self, row: dict[str, Any], url: str, reason: str) -> ContentSourceCandidate | None:
        if self.probe_gateway is None:
            return None
        try:
            result = self.probe_gateway.probe(url)
        except Exception:
            return None
        if not isinstance(result, SourceProbeResult) or result.success is not True:
            return None
        probed = dict(row)
        probed["platform"] = result.platform or row.get("platform")
        probed["url_type"] = "verified_ingest_platform"
        probed["authorization_status"] = row.get("authorization_status", "unknown")
        evidence = list(row.get("evidence", [])) if isinstance(row.get("evidence", []), list) else []
        evidence.append({"kind": "source_probe", "success": True})
        probed["evidence"] = evidence
        return self._candidate(probed, url, reason)
