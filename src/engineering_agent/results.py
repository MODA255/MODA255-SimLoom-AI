from __future__ import annotations

import csv
import json
import operator
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class MetricRule:
    name: str
    path: str
    operator: str
    target: float
    unit: str = ""


@dataclass
class MetricVerdict:
    name: str
    value: float | None
    operator: str
    target: float
    passed: bool
    unit: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lookup(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def parse_result_file(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"value": value}
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return {"rows": rows, "row_count": len(rows)}
    text = path.read_text(encoding="utf-8", errors="replace")
    values: dict[str, float] = {}
    for key, value in re.findall(r"([A-Za-z][\w ./-]{1,60})\s*[:=]\s*(-?\d+(?:\.\d+)?)", text):
        values[key.strip()] = float(value)
    return {"text": text[-20000:], "metrics": values}


def evaluate_metrics(data: dict[str, Any], rules: list[MetricRule]) -> list[MetricVerdict]:
    comparisons = {"<": operator.lt, "<=": operator.le, "=": operator.eq, "==": operator.eq,
                   ">": operator.gt, ">=": operator.ge}
    verdicts = []
    for rule in rules:
        raw = _lookup(data, rule.path)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        compare = comparisons.get(rule.operator)
        passed = bool(value is not None and compare and compare(value, rule.target))
        reason = "metric missing" if value is None else ("rule satisfied" if passed else "threshold violated")
        verdicts.append(MetricVerdict(rule.name, value, rule.operator, rule.target, passed, rule.unit, reason))
    return verdicts
