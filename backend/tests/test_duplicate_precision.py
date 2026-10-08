import json
from dataclasses import replace

import pytest

from app.modules.duplicate import llm_judge, service
from app.schemas import DuplicatePairOut
from app.api.evaluate import _normalize_duplicate_result
from app.schemas import DuplicateInternalResponse, ProjectOut
from datetime import datetime


@pytest.mark.parametrize("relation", [
    "repeated_description", "shared_reuse", "process_steps", "hierarchy",
    "different_scope", "phase_continuation", "insufficient_evidence",
])
def test_non_construction_relations_cannot_become_findings(relation):
    judgement = llm_judge._parse_llm_response(json.dumps({
        "label": "高度相似", "relation": relation, "reason": "两处提到同一能力",
    }))
    assert judgement.label == "无关"
    assert judgement.severity == "pass"


def test_true_duplicate_requires_verifiable_evidence_on_both_sides():
    judgement = llm_judge.LLMJudgement(
        "重复", "risk", "两个独立系统重复投入", relation="construction_duplicate",
        left_unit="系统甲", right_unit="系统乙", overlap_scope="同一单位统一登录",
        left_evidence="系统甲新建统一登录", right_evidence="系统乙另建统一登录",
    )
    left = "系统甲新建统一登录，预算10万元"
    right = "系统乙另建统一登录，预算20万元"
    assert llm_judge._validate_construction_evidence(judgement, left, right).label == "重复"
    for invalid in [replace(judgement, right_evidence="编造的证据"),
                    replace(judgement, left_unit="不存在的系统"),
                    replace(judgement, overlap_scope="")]:
        assert llm_judge._validate_construction_evidence(invalid, left, right).label == "无关"


def pair(left="系统甲", right="系统乙", scope="统一登录", **kwargs):
    return DuplicatePairOut(
        item_id=kwargs.pop("item_id", "1"), related_item_id="2", item_name="登录",
        related_item_name="统一认证", similarity=0.8, result_label="重复",
        severity="risk", reason="独立系统重复投入", left_unit=left, right_unit=right,
        overlap_scope=scope, item_report_name="报告甲", related_report_name="报告甲",
        **kwargs,
    )


def test_merge_keeps_all_locations_and_capabilities_but_separates_other_units_and_reports():
    first = pair(source_highlights=[{"paragraph": 3, "quote": "建设甲"}])
    second = pair("系统乙", "系统甲", item_id="3",
                  source_highlights=[{"paragraph": 10, "quote": "建设乙"}])
    results = service._merge_construction_findings([
        first, second, pair(right="系统丙"), pair(scope="数据目录"),
        pair(comparison_type="cross_report"),
    ])
    assert len(results) == 3
    assert results[0].merged_count == 3
    assert results[0].overlap_scopes == ["统一登录", "数据目录"]
    assert results[0].item_name == "系统甲"
    assert len(results[0].source_highlights) == 2
    assert results[0].revision_target["highlights"] == results[0].source_highlights


def test_equal_paragraph_numbers_in_different_files_are_distinct():
    assert not service._same_source_occurrence(
        {"file_name": "报告甲", "paragraph": 3}, {"file_name": "报告乙", "paragraph": 3},
    )


def test_prompt_separates_construction_from_text_similarity():
    prompt = llm_judge._build_judge_prompt("提取", "流程含提取和分类", "分类", "同一流程", 0.95)
    assert "不能把整条流程" in prompt
    assert "两个独立建设单元" in prompt
    assert "共性能力复用" in prompt


def test_result_shows_construction_evidence_and_all_merged_source_locations():
    finding = pair(
        model_name="DeepSeek-V4-Flash", left_evidence="系统甲另建登录",
        right_evidence="系统乙另建登录", merged_count=3,
        source_highlights=[{"paragraph": 3}, {"paragraph": 10}, {"paragraph": 12}],
    )
    response = DuplicateInternalResponse(
        project=ProjectOut(id="p", name="项目", status="evaluated",
                           created_at=datetime.now(), updated_at=datetime.now()),
        imported_count=5, threshold=.5, pairs=[finding],
    )
    normalized = _normalize_duplicate_result(response)
    displayed = normalized["findings"][0]
    assert "系统甲另建登录" in displayed["evidence_summary"]
    assert "系统乙另建登录" in displayed["evidence_summary"]
    assert "相似度" not in displayed["evidence_summary"]
    assert displayed["llm_refined"]
    assert displayed["merged_count"] == 3
    assert len(displayed["source_highlights"]) == 3
    assert normalized["summary"]["internal_duplicate_count"] == 1


def test_module_heading_and_sentence_name_are_the_same_construction_unit():
    result = service._merge_construction_findings([
        pair(left="统一认证", right="用户接入"), pair(left="统一认证模块", right="用户接入模块"),
    ])
    assert len(result) == 1
    assert result[0].merged_count == 2


@pytest.mark.parametrize("duplicate", [True, False])
def test_internal_review_passes_source_context_and_replaces_stale_findings(monkeypatch, duplicate):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.db.models import Base, Project, CheckResult
    from app.modules.duplicate.excel_parser import ParsedFunctionPoint

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sources = ["系统甲独立新建统一登录", "系统乙另建统一登录"]
    points = [ParsedFunctionPoint(
        index, "统一登录", "对档案人员提供账号密码登录功能", source_location={
            "file_name": "报告", "paragraph": index, "quote": source,
            "review_context": source + "，分别投入预算20万元。",
            "report_context": "两项分别开发部署",
        },
    ) for index, source in enumerate(sources, 1)]

    def judge(*args, **kwargs):
        assert sources[0] in kwargs["left_source"]
        assert sources[1] in kwargs["right_source"]
        assert "分别投入预算" in kwargs["left_source"]
        assert "两项分别开发部署" in kwargs["context"]
        return llm_judge.LLMJudgement(
            "重复" if duplicate else "无关", "risk" if duplicate else "pass", "复核结果",
            relation="construction_duplicate" if duplicate else "repeated_description",
            left_unit="系统甲", right_unit="系统乙", overlap_scope="统一登录",
            left_evidence=sources[0], right_evidence=sources[1],
        )

    monkeypatch.setattr(service, "judge_pair_with_llm", judge)
    with Session(engine) as db:
        db.add(Project(id="p", name="项目", status="draft"))
        db.add(CheckResult(project_id="p", module="duplicate", check_subtype="internal_dedup",
                           result_label="重复", reason="过时误报", severity="risk"))
        db.commit()
        response = service._process_function_points(db, points, "p", "项目", None)
        stored = list(db.scalars(select(CheckResult)).all())
        assert len(response.pairs) == len(stored) == int(duplicate)
        if duplicate:
            assert stored[0].reason == "复核结果"
            assert json.loads(stored[0].reference_data)["left_evidence"] == sources[0]
