import json
import httpx
import pytest

from orchestrator.llm_judge import JudgeConfigurationError, LLMJudge, JudgeResponseError


def test_judge_requires_model_configuration():
    with pytest.raises(JudgeConfigurationError, match="JUDGE_NOT_CONFIGURED"):
        LLMJudge(base_url="", api_key="", model="").evaluate({"task_id": "t1"})


def test_judge_sends_deterministic_request_and_returns_provenance(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "scores": {"topic_fit": 4, "entity_fit": 5, "factuality": 4, "content_completeness": 3},
                "overall_score": 80,
                "reason": "主题基本符合",
                "failure_modes": ["内容不完整"],
                "evidence": ["query: 主题发现"],
                "confidence": 0.9,
            }, ensure_ascii=False)}}]}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(httpx, "post", post)
    result = LLMJudge("https://judge.example/v1", "secret", "judge-model").evaluate({"task_id": "t1", "query": "主题发现"})

    assert result.overall_score == 80
    assert result.model == "judge-model"
    assert result.prompt_version == "matrixgen-judge-v1"
    assert len(result.input_sha256) == 64
    assert calls[0][1]["json"]["temperature"] == 0
    assert calls[0][1]["json"]["top_p"] == 1


def test_judge_rejects_invalid_structured_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(JudgeResponseError, match="JUDGE_INVALID_SCORES"):
        LLMJudge("https://judge.example/v1", "secret", "judge-model").evaluate({"task_id": "t1"})
