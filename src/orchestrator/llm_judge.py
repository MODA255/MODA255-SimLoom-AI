import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any
import httpx
from .judge_evidence import build_judge_evidence

JUDGE_PROMPT_VERSION = "matrixgen-judge-v1"


@dataclass
class JudgeResult:
    scores: dict[str, int]
    overall_score: float
    reason: str
    failure_modes: list[str]
    evidence: list[str]
    confidence: float
    model: str
    prompt_version: str
    input_sha256: str


class JudgeConfigurationError(RuntimeError):
    pass


class JudgeResponseError(RuntimeError):
    pass


def evaluate_judge_evidence(evidence):
    missing = []
    phases = evidence.get("phases") or []
    artifacts = evidence.get("text_artifacts") or []
    if not any(item.get("evidence_status") == "available" for item in artifacts):
        missing.append("transcript_or_script")
    if not phases or any(item.get("evidence_status") == "missing" for item in phases):
        missing.append("phase_evidence")
    if not evidence.get("lineage"):
        missing.append("lineage")
    if missing:
        return {"status": "insufficient_evidence", "overall_score": None, "confidence": 0, "cannot_conclude": True, "missing_evidence": sorted(set(missing)), "failure_modes": ["MISSING_JUDGE_EVIDENCE"]}
    return {"status": "ready", "overall_score": None, "confidence": None, "cannot_conclude": False, "missing_evidence": [], "failure_modes": []}


class LLMJudge:
    def __init__(self, base_url=None, api_key=None, model=None, timeout=60):
        self.base_url = (os.getenv("MODEL_BASE_URL", "") if base_url is None else base_url).rstrip("/")
        self.api_key = os.getenv("MODEL_API_KEY", "") if api_key is None else api_key
        self.model = os.getenv("MODEL_NAME", "") if model is None else model
        self.timeout = timeout

    def _validate_config(self):
        if not self.base_url or not self.api_key or not self.model:
            raise JudgeConfigurationError("JUDGE_NOT_CONFIGURED")

    @staticmethod
    def _input_hash(payload):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def evaluate(self, payload: dict[str, Any]) -> JudgeResult:
        self._validate_config()
        input_hash = self._input_hash(payload)
        system = "你是 MatrixGen 剪辑链路质量评测员。只根据提供的任务证据、brief、主题和字幕/脚本文本评测，不猜测未提供的信息。宁可漏报，不可误报。严格输出 JSON。"
        user = {
            "prompt_version": JUDGE_PROMPT_VERSION,
            "task": payload,
            "rubric": [
                "主题与 brief/query 一致性",
                "品牌、商品、IP 与输入证据一致性",
                "脚本或字幕是否出现无证据承诺",
                "内容是否完整、连贯且可用于目标场景",
            ],
            "output_schema": {
                "scores": {"topic_fit": "integer 1-5", "entity_fit": "integer 1-5", "factuality": "integer 1-5", "content_completeness": "integer 1-5"},
                "overall_score": "number 0-100",
                "reason": "string",
                "failure_modes": "array of strings",
                "evidence": "array of quoted evidence strings",
                "confidence": "number 0-1",
            },
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "temperature": 0, "top_p": 1, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise JudgeResponseError("JUDGE_INVALID_JSON") from exc
        scores = result.get("scores")
        if not isinstance(scores, dict) or not all(isinstance(scores.get(k), int) and 1 <= scores[k] <= 5 for k in ("topic_fit", "entity_fit", "factuality", "content_completeness")):
            raise JudgeResponseError("JUDGE_INVALID_SCORES")
        confidence = result.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise JudgeResponseError("JUDGE_INVALID_CONFIDENCE")
        return JudgeResult(scores=scores, overall_score=float(result.get("overall_score", 0)), reason=str(result.get("reason", "")), failure_modes=list(result.get("failure_modes", [])), evidence=list(result.get("evidence", [])), confidence=float(confidence), model=self.model, prompt_version=JUDGE_PROMPT_VERSION, input_sha256=input_hash)
