"""Only complete final answers may replace rule-based review opinions."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm_client, result_refiner


@pytest.fixture
def answer():
    return {
        "judgement": "需人工确认",
        "user_reason": "IBM 属于现有系统描述，应核对本次建设是否涉及新增采购。",
        "user_suggestion": "请补充新增采购范围与现有系统的关系。",
        "user_basis": "依据报告中现有网络和设备资源情况章节。",
        "risk_level_suggestion": "中",
    }


def response(content=None, finish_reason="stop", **message):
    return {"choices": [{"finish_reason": finish_reason,
                         "message": {"content": content, **message}}]}


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content", "refusal"])
def test_reasoning_and_refusal_are_never_final_answers(answer, field):
    # Even valid JSON in a reasoning field is not a final answer.
    with pytest.raises(ValueError):
        llm_client.extract_message_content(response(**{field: json.dumps(answer)}))


def test_final_answer_takes_priority_over_reasoning(answer):
    content = json.dumps(answer, ensure_ascii=False)
    assert llm_client.extract_message_content(response(content, reasoning_content="We need to analyze.")) == content


@pytest.mark.parametrize("content", ["<think>We need answer JSON only.</think>",
                                      "<think>We need answer JSON only."])
def test_thinking_only_content_is_rejected(content):
    with pytest.raises(ValueError):
        llm_client.extract_message_content(response(content))
    with pytest.raises(ValueError):
        llm_client._parse_model_content(content)


@pytest.mark.parametrize("object_response", [False, True])
def test_length_finish_reason_rejects_even_parseable_content(answer, object_response):
    data = response(json.dumps(answer), finish_reason="length")
    if object_response:
        data = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="length", message=SimpleNamespace(**data["choices"][0]["message"]))])
    with pytest.raises(ValueError, match="truncated"):
        llm_client.extract_message_content(data)


@pytest.mark.parametrize("wrap", [lambda s: s, lambda s: "```json\n" + s + "\n```",
                                  lambda s: "<think>We need to analyze.</think>\n" + s,
                                  lambda s: "审查结果如下：\n" + s])
def test_complete_final_json_remains_supported(answer, wrap):
    payload = llm_client._parse_model_content(wrap(json.dumps(answer, ensure_ascii=False)))
    assert llm_client._normalize_review(payload, "model", {}, "")["user_reason"] == answer["user_reason"]


@pytest.mark.parametrize("content", [
    "We need answer JSON only. Need analyze. 原始原因需要复核。",
    "审查意见：需要补充建设依据。",
    '{"user_reason": "意见未完成",',
    '{"user_reason": "未完成", "example": {"user_reason": "不能采纳内层示例"}',
    '[{"user_reason": "不接受数组"}]',
])
def test_unstructured_and_incomplete_content_is_rejected(content):
    with pytest.raises(ValueError):
        llm_client._parse_model_content(content)


@pytest.mark.parametrize("field", ["user_reason", "user_suggestion", "user_basis"])
@pytest.mark.parametrize("invalid", [None, "", [], "We need answer JSON only. Need analyze.",
                                    "We need answer JSON only. 原始原因需要复核。"])
def test_review_requires_complete_chinese_business_fields(answer, field, invalid):
    answer[field] = invalid
    with pytest.raises(ValueError):
        llm_client._normalize_review(answer, "model", {}, "")
    with pytest.raises(ValueError):
        llm_client._normalize_security_review({**answer, "passed": False}, "model", {})


def test_plain_text_cannot_bypass_validation():
    with pytest.raises(ValueError):
        llm_client._normalize_review({"_raw_text": "We need answer JSON only."}, "model", {}, "")


@pytest.mark.parametrize("module", ["basis", "resource", "data_reasonableness", "function_correspondence", "sensitive_word"])
def test_invalid_response_retains_rule_opinion_and_marks_review_incomplete(monkeypatch, module):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "_call_deepseek", lambda **_: {"_raw_text": "We need answer JSON only."})
    original = [{"rule_id": "r1", "risk_level": "高", "review_opinion": "原规则意见：需要核对依据。"}]
    expected, _ = result_refiner.refine_findings(module, original, [], False)
    actual, meta = result_refiner.refine_findings(module, original, [], True)
    assert actual[0]["review_opinion"] == expected[0]["review_opinion"]
    assert actual[0]["risk_level"] == "高"
    assert not actual[0].get("llm_refined")
    assert "未完成" in actual[0]["llm_review_notice"]
    assert meta["llm_status"] == "partial"
    assert meta["llm_reviewed_count"] == 0
    assert meta["llm_error_count"] == 1


def test_one_invalid_answer_does_not_block_later_valid_review(monkeypatch, answer):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    replies = iter([{"user_reason": "We need analyze."}, answer])
    monkeypatch.setattr(llm_client, "_call_deepseek", lambda **_: next(replies))
    findings, meta = result_refiner.refine_findings("basis", [
        {"rule_id": "r1", "risk_level": "高"}, {"rule_id": "r2", "risk_level": "高"}], [], True)
    assert not findings[0].get("llm_refined")
    assert findings[1]["review_opinion"] == answer["user_reason"]
    assert meta["llm_reviewed_count"] == 1
    assert meta["llm_status"] == "partial"


@pytest.mark.parametrize("security", [False, True])
def test_invalid_response_is_returned_as_unavailable(monkeypatch, security):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "_call_deepseek", lambda **_: {"user_reason": "We need to analyze."})
    result = (llm_client.review_security_document_with_llm({}, {}) if security
              else llm_client.review_finding_with_llm("basis", {}, {}))
    assert result["llm_available"] is False
    assert result["llm_error_type"] == "invalid_review_response"
    assert "We need" not in result["llm_error"]


def test_request_reserves_room_for_final_answer(monkeypatch, answer):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps(response(json.dumps(answer))).encode()

    def open_response(request, **kwargs):
        captured.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(llm_client, "urlopen_direct", open_response)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "DeepSeek-V4-Flash")
    result = llm_client.review_finding_with_llm("basis", {}, {})
    assert result["llm_available"] is True
    assert captured["max_tokens"] >= 2000
    assert captured["reasoning_effort"] == "low"
    assert "中文" in captured["messages"][0]["content"]
