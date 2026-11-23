"""Model review conclusions must survive normalization and result storage intact."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm_client, result_refiner


@pytest.fixture
def long_review():
    return {
        'user_reason': '第一段说明。\n' + '审查意见需逐项核对建设内容和依据。' * 40 + '\n结尾：意见完整。',
        'user_suggestion': '修改建议。\n' + '补充对应依据并核对相关章节。' * 40 + '\n结尾：建议完整。',
        'user_basis': '文档依据。\n' + '对应章节原文和预算清单共同支撑此判断。' * 40 + '\n结尾：依据完整。',
        'judgement': '需人工确认',
        'risk_level_suggestion': '中',
    }


def test_normalize_model_review_keeps_complete_fields(long_review):
    review = llm_client._normalize_review(long_review, 'model', {}, '')
    for field in ('user_reason', 'user_suggestion', 'user_basis'):
        assert review[field] == long_review[field]
    assert review['reason'] == long_review['user_reason']
    assert review['revision_advice'] == long_review['user_suggestion']
    assert review['evidence_summary'] == long_review['user_basis']


def test_normalize_model_review_keeps_explicit_business_fields(long_review):
    payload = {**long_review, 'revision_advice': long_review['user_suggestion'],
               'evidence_summary': long_review['user_basis']}
    review = llm_client._normalize_review(payload, 'model', {}, '')
    assert review['revision_advice'] == payload['revision_advice']
    assert review['evidence_summary'] == payload['evidence_summary']


def test_plain_text_review_requires_structured_final_answer(long_review):
    content = long_review['user_reason'] + '\n建议：' + long_review['user_suggestion'].replace('\n', ' ')
    with pytest.raises(llm_client.InvalidReviewResponseError):
        llm_client._normalize_review({'_raw_text': content}, 'model', {}, '')


def test_security_review_keeps_complete_text_and_judgement(long_review):
    payload = {**long_review, 'passed': False, 'evidence_summary': long_review['user_basis']}
    review = llm_client._normalize_security_review(payload, 'model', {})
    for field in ('user_reason', 'user_suggestion', 'user_basis'):
        assert review[field] == payload[field]
    assert review['evidence_summary'] == payload['evidence_summary']
    assert review['passed'] is False
    assert review['risk_level_suggestion'] == '中'


@pytest.mark.parametrize('module', ['basis', 'resource', 'data_reasonableness', 'function_correspondence', 'sensitive_word'])
def test_full_review_survives_business_result_and_json(monkeypatch, long_review, module):
    import json

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(llm_client, '_call_deepseek', lambda **_: dict(long_review))
    findings, meta = result_refiner.refine_findings(module, [{'rule_id': 'r1', 'risk_level': '高'}], [], True)
    finding = json.loads(json.dumps(findings, ensure_ascii=False))[0]
    assert finding['review_opinion'] == long_review['user_reason']
    assert finding['revision_advice'] == long_review['user_suggestion']
    assert finding['evidence_summary'] == long_review['user_basis']
    assert finding['risk_level'] == '高'
    assert finding['llm_risk_level_suggestion'] == '中'
    assert meta['llm_reviewed_count'] == 1
    assert meta['llm_status'] == 'completed'
