import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import evaluate
from app.api.evaluate import _render_result_export_html
from app.db.models import Base, CheckResult, Project
from app.db.session import get_db


def test_result_export_contains_full_findings_and_escapes_user_content():
    findings = [
        {
            "title": f"敏感词 {index}",
            "risk_level": "中",
            "review_opinion": "发现 <script>alert(1)</script>",
            "evidence_summary": "原文：项目建设依据",
            "revision_advice": "请补充说明",
        }
        for index in range(1, 106)
    ]
    report = _render_result_export_html(
        {
            "project_name": "测试报告",
            "module_name": "敏感词检查",
            "status": "检查完成",
            "risk_level": "中",
            "created_at": "2026-10-15T10:00:00",
            "findings": findings,
            "result": {"findings": findings, "rule_results": []},
        }
    )

    assert "审查问题总数：<strong>105</strong>" in report
    assert "敏感词 105" in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "<script>alert(1)</script>" not in report
    assert "完整结果数据" in report


def test_result_export_includes_rule_findings_when_no_top_level_findings():
    report = _render_result_export_html(
        {
            "project_name": "规则报告",
            "module_name": "建设依据",
            "status": "检查完成",
            "risk_level": "需人工确认",
            "findings": [],
            "result": {
                "findings": [],
                "rule_results": [
                    {
                        "rule_name": "建设依据完整性",
                        "findings": [
                            {"title": "依据缺失", "reason": "未引用相关政策文件"}
                        ],
                    }
                ],
            },
        }
    )

    assert "建设依据完整性" in report
    assert "依据缺失" in report
    assert "审查问题总数：<strong>1</strong>" in report


def test_export_endpoint_returns_download_with_untruncated_saved_result():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(name="导出项目")
        findings = [{"title": f"finding-{index}"} for index in range(101)]
        record = CheckResult(
            project=project,
            module="sensitive_word",
            check_subtype="aggregate",
            result_label="发现问题",
            severity="中",
            reason="测试记录",
            reference_data=json.dumps(
                {"project_name": "导出项目", "result": {"findings": findings}},
                ensure_ascii=False,
            ),
        )
        db.add(record)
        db.commit()
        record_id = record.id

        app = FastAPI()
        app.include_router(evaluate.router, prefix="/api/evaluate")
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            response = client.get(f"/api/evaluate/results/{record_id}/export")

    engine.dispose()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "finding-100" in response.text
    assert "<strong>101</strong>" in response.text


def test_export_renders_uncapped_memory_findings_beyond_rule_group_limits():
    findings = [{"rule_id": "r1", "title": f"完整问题{index}",
                 "review_opinion": f"完整意见{index}",
                 "issue_memory": {"label": "仍然存在"}} for index in range(105)]
    report = _render_result_export_html({"findings": findings,
        "result": {"rule_results": [{"rule_id": "r1", "findings": findings[:50]}],
                   "issue_memory": {"resolved_count": 2, "persistent_count": 105}}})
    visible = report.split('<section><h2>完整结果数据</h2>')[0]
    assert "<strong>105</strong>" in visible
    assert "完整意见104" in visible
    assert visible.count("完整意见0</dd>") == 1
    assert "本次确认解决：2 项" in visible


def test_export_keeps_rule_opinion_and_separately_shows_memory_feedback_and_evidence():
    report = _render_result_export_html({
        "findings": [{"rule_id": "r1", "title": "缺少数量依据", "review_opinion": "原规则意见仍存在"}],
        "result": {"issue_memory": {
            "message": "优先跟踪历史问题", "unconfirmed_count": 1,
            "revision_reviews": [{"label": "待确认", "summary": "已找到新增清单，尚需核验数量对应关系",
                "previous_evidence": "原版未配置操作系统", "current_evidence": "新版新增操作系统10套"}],
        }},
        "memory_workspace": {
            "context": {"facts": [{"key": "建设阶段", "value": "<script>背景</script>", "source": "人工填写"}]},
            "feedback": [{"action": "resolved", "reason": "人工认为已补齐", "evidence": "第八章资源表",
                          "actor": "审查人", "created_at": "2026-11-15"}],
            "jobs": [{"status": "partial", "completed": 1, "total": 4}],
        },
    })
    visible = report.split('<section><h2>完整结果数据</h2>')[0]
    assert "原规则意见仍存在" in visible
    assert "历史问题整改对照" in visible
    assert "原版未配置操作系统" in visible and "新版新增操作系统10套" in visible
    assert "人工认为已补齐" in visible and "第八章资源表" in visible
    assert "人工确认不代表规则重新通过" in visible
    assert "&lt;script&gt;背景&lt;/script&gt;" in visible
    assert "<script>" not in report
