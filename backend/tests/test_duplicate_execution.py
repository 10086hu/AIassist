import threading
import time
from types import SimpleNamespace

import pytest

from app.modules.duplicate import execution, llm_extractor, llm_judge
from app.modules.duplicate.document_parser import DocumentContent, DocumentSection
from app.modules.duplicate.excel_parser import ParsedFunctionPoint


def test_parallel_calls_are_bounded_preserve_order_and_report_progress(monkeypatch):
    monkeypatch.setattr(execution, "settings", SimpleNamespace(
        duplicate_llm_concurrency=2, duplicate_task_timeout_seconds=5,
    ))
    barrier = threading.Barrier(2)
    active = 0
    peak = 0
    lock = threading.Lock()
    updates = []

    def worker(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return value * 10

    with execution.duplicate_execution(lambda fraction, text: updates.append((fraction, text))):
        result = execution.parallel_model_work(range(6), worker, message="复核", start=0.4, end=0.95)
    assert result == [0, 10, 20, 30, 40, 50]
    assert peak == 2
    assert updates[0][0] == 0.4
    assert updates[-1][0] == 0.95
    assert "6/6" in updates[-1][1]


def test_hung_model_finishes_task_at_deadline_and_cancels_queue(monkeypatch):
    monkeypatch.setattr(execution, "settings", SimpleNamespace(
        duplicate_llm_concurrency=1, duplicate_task_timeout_seconds=0.05,
    ))
    release = threading.Event()
    called = []

    def worker(value):
        called.append(value)
        release.wait(timeout=2)

    started = time.monotonic()
    try:
        with execution.duplicate_execution():
            with pytest.raises(ValueError, match="超过总时限"):
                execution.parallel_model_work(range(10), worker, message="抽取", start=0, end=1)
        assert time.monotonic() - started < 1
        assert called == [0]
    finally:
        release.set()


def test_deadline_is_shared_with_workers_and_context_is_reset(monkeypatch):
    monkeypatch.setattr(execution, "settings", SimpleNamespace(
        duplicate_llm_concurrency=2, duplicate_task_timeout_seconds=5,
    ))
    with execution.duplicate_execution():
        result = execution.parallel_model_work([1, 2], lambda _: execution.remaining_timeout(180), message="复核", start=0, end=1)
        assert all(0 < value <= 5 for value in result)
    assert execution.remaining_timeout(180) == 180


def test_parallel_extraction_keeps_evidence_in_document_order(monkeypatch):
    monkeypatch.setattr(llm_extractor, "settings", SimpleNamespace(deepseek_api_key="configured"))
    monkeypatch.setattr(llm_extractor, "_build_extraction_chunks", lambda _: [("一", "甲"), ("二", "乙")])
    monkeypatch.setattr(llm_extractor, "_extract_from_text", lambda text, **kwargs: [
        ParsedFunctionPoint(1, text, "描述", source_location={"quote": text}),
    ])
    document = DocumentContent("docx", "甲" * 6000, [DocumentSection("功能", "内容")], "报告.docx")
    result = llm_extractor.extract_function_points(document)
    assert [point.row_index for point in result] == [1, 2]
    assert [point.source_location["quote"] for point in result] == ["甲", "乙"]


def test_failed_extraction_block_cannot_be_silently_omitted(monkeypatch):
    monkeypatch.setattr(llm_extractor, "settings", SimpleNamespace(deepseek_api_key="configured"))
    monkeypatch.setattr(llm_extractor, "_build_extraction_chunks", lambda _: [("一", "正常"), ("二", "故障")])
    monkeypatch.setattr(llm_extractor, "_split_chunk", lambda text: [text])

    def extract(text, **kwargs):
        if text == "故障":
            raise ValueError("模型失败")
        return [ParsedFunctionPoint(1, text, "描述")]

    monkeypatch.setattr(llm_extractor, "_extract_from_text", extract)
    document = DocumentContent("docx", "甲" * 6000, [], "报告.docx")
    with pytest.raises(ValueError, match="提取不完整"):
        llm_extractor.extract_function_points(document)


@pytest.mark.parametrize("module", [llm_extractor, llm_judge])
def test_truncated_content_cannot_be_accepted_as_a_complete_review(module):
    response = {"choices": [{"finish_reason": "length", "message": {"content": '{"name": "截断的子对象"}'}}]}
    with pytest.raises(ValueError, match="truncated"):
        module._extract_message_text(response)


def test_invalid_extraction_or_judgement_cannot_become_a_passing_result():
    with pytest.raises(ValueError):
        llm_extractor._parse_extraction_response('{"name": "子对象"}')
    with pytest.raises(ValueError):
        llm_judge._parse_llm_response('{"reason": "缺少判定"}')


def test_dense_block_is_split_again_until_complete_without_losing_text(monkeypatch):
    monkeypatch.setattr(llm_extractor, "settings", SimpleNamespace(deepseek_api_key="configured"))
    monkeypatch.setattr(llm_extractor, "_build_extraction_chunks", lambda _: [("密集功能", "甲" * 1200)])

    def extract(text, **kwargs):
        if len(text) > 300:
            raise ValueError("LLM response truncated, finish_reason=length")
        return [ParsedFunctionPoint(1, "功能", text, source_location={"quote": text})]

    monkeypatch.setattr(llm_extractor, "_extract_from_text", extract)
    document = DocumentContent("docx", "甲" * 6000, [], "报告.docx")
    result = llm_extractor.extract_function_points(document)
    assert len(result) == 4
    assert "".join(point.description for point in result) == "甲" * 1200


def test_compact_output_uses_original_quote_as_function_description():
    result = llm_extractor._parse_extraction_response(
        '{"function_points": [{"name": "统一登录", "source_quote": "提供统一登录功能"}]}'
    )
    assert result[0].description == "提供统一登录功能"
    assert result[0].source_location["quote"] == "提供统一登录功能"


def test_short_document_truncation_also_splits_without_losing_evidence(monkeypatch):
    monkeypatch.setattr(llm_extractor, "settings", SimpleNamespace(deepseek_api_key="configured"))

    def extract(text, **kwargs):
        if len(text) > 300:
            raise ValueError("LLM response truncated, finish_reason=length")
        return [ParsedFunctionPoint(1, "独立模块", text, source_location={"quote": text})]

    monkeypatch.setattr(llm_extractor, "_extract_from_text", extract)
    document = DocumentContent("docx", "甲" * 600, [], "短报告.docx")
    result = llm_extractor.extract_function_points(document)
    assert len(result) == 2
    assert "".join(point.source_location["quote"] for point in result) == "甲" * 600
