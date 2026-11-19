"""Conservative text requirement extraction for AgentCAD-style drawing workflows."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".rst", ".json"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF_TEXT_EXTRACTION_UNAVAILABLE") from exc
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    raise ValueError("REQUIREMENTS_DOCUMENT_UNSUPPORTED:" + suffix)


def _match_number(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            try:
                return _number(match.group(1))
            except ValueError:
                continue
    return None


def extract_requirements(path: str | Path, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    text = _read_text(source)
    normalized = text.replace("×", "x").replace("Ｘ", "x")
    width = _match_number(normalized, [r"(?:width|宽度|plate\s*width|长度)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:mm|毫米)?",
                                         r"([0-9]+(?:[.,][0-9]+)?)\s*x\s*[0-9]+(?:[.,][0-9]+)?\s*(?:mm|毫米)?"])
    height = _match_number(normalized, [r"(?:height|高度|plate\s*height|宽度)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:mm|毫米)?",
                                          r"[0-9]+(?:[.,][0-9]+)?\s*x\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:mm|毫米)?"])
    diameter = _match_number(normalized, [r"(?:diameter|直径|dia(?:meter)?)[^0-9\n:：=]*[:=]?\s*(?:Ø|⌀)?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:mm|毫米)?",
                                            r"(?:Ø|⌀)\s*([0-9]+(?:[.,][0-9]+)?)"])
    edge_offset = _match_number(normalized, [r"(?:edge\s*offset|offset|边距|边缘距离)[^0-9]*([0-9]+(?:[.,][0-9]+)?)\s*(?:mm|毫米)?"])
    hole_count = None
    count_match = re.search(r"(?:hole\s*count|孔数|holes?)\s*[:=]?\s*(\d+)", normalized, flags=re.IGNORECASE)
    if count_match:
        hole_count = int(count_match.group(1))
    kind = "plate" if re.search(r"plate|板|底板|安装板", normalized, re.IGNORECASE) else None
    facts: dict[str, Any] = {"kind": kind or "unknown", "units": "mm"}
    if width is not None: facts["width"] = width
    if height is not None: facts["height"] = height
    if diameter is not None: facts["hole_diameter"] = diameter
    if edge_offset is not None: facts["edge_offset"] = edge_offset
    if hole_count is not None: facts["hole_count"] = hole_count
    if overrides:
        facts.update(json.loads(json.dumps(overrides)))
    warnings = []
    if kind is None: warnings.append("SPEC_KIND_NOT_RECOGNIZED")
    for key in ("width", "height"):
        if key not in facts: warnings.append("MISSING_" + key.upper())
    confidence = max(0.0, min(1.0, 0.35 + 0.15 * sum(key in facts for key in ("width", "height", "hole_diameter", "edge_offset", "hole_count"))))
    return {"schema_version": "simloom-ai/requirements/v1", "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "facts": facts,
            "warnings": warnings, "confidence": confidence, "text_length": len(text)}
