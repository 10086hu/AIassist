import json
from types import SimpleNamespace

import pytest

from app.modules.duplicate import llm_judge


def configure(monkeypatch, responses):
    monkeypatch.setattr(llm_judge, "settings", SimpleNamespace(
        deepseek_api_url="https://example.invalid/v1", deepseek_api_key="test",
        deepseek_model="DeepSeek-V4-Flash", duplicate_llm_max_tokens=6000,
        duplicate_judge_max_tokens=24000, duplicate_llm_timeout_seconds=180,
    ))
    calls = []
    responses = iter(responses)

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return next(responses)

    def post(url, headers, payload, timeout):
        calls.append(json.loads(json.dumps(payload)))
        return Response()

    monkeypatch.setattr(llm_judge, "_post_with_retry", post)
    return calls


def output(reason, content=""):
    return {"choices": [{"finish_reason": reason, "message": {"content": content}}]}


def test_truncation_retries_with_larger_budget_and_preserves_all_evidence(monkeypatch):
    final = '{"label":"无关","relation":"process_steps","reason":"前后步骤"}'
    calls = configure(monkeypatch, [output("length"), output("length", '{"label":'), output("stop", final)])
    assert llm_judge._call_deepseek_api("完整的双侧证据和上下文") == final
    assert [c["max_tokens"] for c in calls] == [6000, 12000, 24000]
    assert all(c["messages"][0]["content"] == "完整的双侧证据和上下文" for c in calls)


def test_repeated_truncation_stops_at_budget_limit_without_passing_result(monkeypatch):
    calls = configure(monkeypatch, [output("length")] * 3)
    with pytest.raises(ValueError, match="finish_reason=length"):
        llm_judge._call_deepseek_api("证据")
    assert len(calls) == 3


def test_non_truncation_error_does_not_trigger_token_retry(monkeypatch):
    calls = configure(monkeypatch, [{"choices": []}])
    with pytest.raises(ValueError, match="missing choices"):
        llm_judge._call_deepseek_api("证据")
    assert len(calls) == 1
