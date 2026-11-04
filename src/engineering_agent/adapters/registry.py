from __future__ import annotations

from typing import Any

from ..models import ToolName
from .base import ToolAdapter
from .vendor import AdamsAdapter, AnsysAdapter, CatiaAdapter, ViGradeAdapter


def build_adapters(config: dict[str, Any]) -> dict[ToolName, ToolAdapter]:
    tools = config.get("tools", {})
    vi_grade_config = tools.get("vi-grade") or tools.get("vigrade") or tools.get("vi_grade") or {}
    return {
        ToolName.ANSYS: AnsysAdapter(tools.get("ansys", {})),
        ToolName.CATIA: CatiaAdapter(tools.get("catia", {})),
        ToolName.ADAMS: AdamsAdapter(tools.get("adams", {})),
        ToolName.VI_GRADE: ViGradeAdapter(vi_grade_config),
    }
