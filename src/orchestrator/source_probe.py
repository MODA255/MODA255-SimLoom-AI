from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SourceProbeResult:
    """Read-only accessibility/ingest probe; it does not establish authorization."""

    success: bool
    platform: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceProbeGateway(Protocol):
    """Abstract gateway for a read-only source probe.

    Implementations must not import, persist, or mutate source records.
    """

    def probe(self, source_url: str) -> SourceProbeResult: ...
