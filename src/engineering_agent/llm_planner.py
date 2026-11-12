from __future__ import annotations

import json
import os
from typing import Any

from .models import AgentRequest, ExecutionPlan, PlanStep, ToolName
from .planner import EngineeringPlanner


class AutonomousPlanner:
    """LLM planner with deterministic fallback and strict plan validation."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.fallback = EngineeringPlanner()

    def plan(self, request: AgentRequest) -> ExecutionPlan:
        llm = self.config.get("llm", {})
        if not llm.get("enabled"):
            return self.fallback.plan(request)
        try:
            raw = self._ask(request, llm)
            return self._validate(request, raw)
        except Exception:
            # A bad provider response must never stop a safe local plan.
            return self.fallback.plan(request)

    def _ask(self, request: AgentRequest, config: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("INSTALL_HTTPX_FOR_LLM_PLANNER") from exc
        endpoint = config.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        api_key = os.getenv(config.get("api_key_env", "OPENAI_API_KEY"), "")
        prompt = {
            "task": request.prompt,
            "project_dir": request.project_dir,
            "parameters": request.parameters,
            "allowed_tools": [tool.value for tool in ToolName],
            "required_schema": {"steps": [{"id": "string", "tool": "ansys|catia|adams|vi-grade", "action": "string", "objective": "string", "depends_on": ["string"], "inputs": ["string"], "outputs": ["string"], "parameters": {}}]},
        }
        response = httpx.post(endpoint, headers={"Authorization": f"Bearer {api_key}"} if api_key else {}, json={
            "model": config.get("model", "gpt-4o-mini"),
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "Return only a safe engineering execution plan JSON."}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        }, timeout=float(config.get("timeout_seconds", 60)))
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content) if isinstance(content, str) else content

    def _validate(self, request: AgentRequest, raw: dict[str, Any]) -> ExecutionPlan:
        steps = []
        allowed = {tool.value: tool for tool in ToolName}
        for item in raw.get("steps", []):
            tool = allowed.get(str(item.get("tool", "")).lower())
            if not tool or not item.get("id") or not item.get("action"):
                raise ValueError("invalid LLM plan step")
            steps.append(PlanStep(id=str(item["id"]), tool=tool, action=str(item["action"]), objective=str(item.get("objective", "")), depends_on=list(item.get("depends_on", [])), inputs=list(item.get("inputs", [])), outputs=list(item.get("outputs", [])), parameters={**dict(item.get("parameters", {})), **request.parameters}))
        if not steps:
            raise ValueError("empty LLM plan")
        ids = {step.id for step in steps}
        if any(dep not in ids for step in steps for dep in step.depends_on):
            raise ValueError("plan dependency missing")
        return ExecutionPlan(request=request, steps=steps, assumptions=["由 LLM 生成，并经过工具白名单和依赖校验。"])
