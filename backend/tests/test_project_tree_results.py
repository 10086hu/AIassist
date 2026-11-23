"""Report history must show aggregates, never individual storage rows."""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import evaluate, projects
from app.db.models import Base, CheckResult, CheckRun, Project, ReviewRunLink
from app.db.session import get_db


@pytest.fixture
def database():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def row(project, subtype='aggregate', run=None, count=0):
    return CheckResult(project=project, check_run=run, module='resource', check_subtype=subtype,
                       result_label='发现问题' if count else '通过', severity='低', reason='测试记录',
                       reference_data=json.dumps({'report_name': '报告.docx', 'result': {
                           'summary': {'total_findings': count}}}) if subtype == 'aggregate' else '{}')


def test_one_real_task_produces_one_visible_report(database, tmp_path):
    report = tmp_path / '资源.csv'
    report.write_text('资源名称,数量,单位\n应用服务器,3,台\n操作系统,1,套\n', encoding='utf-8-sig')
    factory = sessionmaker(bind=database.bind)
    with patch.object(evaluate, 'SessionLocal', factory):
        evaluate._run_evaluate_task('tree-regression', str(report), report.name, None, None,
                                    '树回归项目', None, ['resource'], 'local', False, None)
    task = evaluate.TASKS.pop('tree-regression')
    assert task['status'] == 'completed', task
    assert len(task['result_ids']) == 1
    stored = database.scalars(select(CheckResult)).all()
    assert any(r.check_subtype == 'resource_rule_15_16' for r in stored)
    assert len([r for r in stored if r.check_subtype == 'aggregate']) == 1
    app = FastAPI()
    app.include_router(projects.router, prefix='/projects')
    app.dependency_overrides[get_db] = lambda: database
    with TestClient(app) as client:
        response = client.get('/projects/tree')
    assert response.status_code == 200
    runs = response.json()['items'][0]['check_runs']
    assert len(runs) == 1
    assert not runs[0]['id'].startswith('legacy:')
    assert [r['id'] for r in runs[0]['results']] == task['result_ids']
    aggregate = next(r for r in stored if r.check_subtype == 'aggregate')
    expected = json.loads(aggregate.reference_data)['result']['summary']['total_findings']
    assert expected > 0
    assert runs[0]['results'][0]['findings_count'] == expected


def test_real_resource_revisions_confirm_fixed_quantity_problem(database, tmp_path):
    report = tmp_path / '修订跟踪.csv'
    factory = sessionmaker(bind=database.bind)
    project = Project(name='连续修订项目')
    database.add(project)
    database.commit()
    for version, os_count in enumerate((1, 3), 1):
        report.write_text(f'来源,资源名称,数量,单位\n计算资源清单,云服务器,3,台\n计算资源清单,服务器操作系统,{os_count},套\n', encoding='utf-8-sig')
        with patch.object(evaluate, 'SessionLocal', factory):
            evaluate._run_evaluate_task(f'memory-real-{version}', str(report), report.name, project.id, None,
                                        project.name, None, ['resource'], 'local', False, None)
        task = evaluate.TASKS.pop(f'memory-real-{version}')
        assert task['status'] == 'completed', task
    rows = database.scalars(select(CheckResult).where(CheckResult.check_subtype == 'aggregate')
                            .order_by(CheckResult.created_at)).all()
    first, second = [json.loads(row.reference_data)['result']['issue_memory'] for row in rows]
    assert second['baseline_result_id'] == rows[0].id
    assert second['resolved_count'] >= 1, [json.loads(row.reference_data)['result'] for row in rows]
    assert second['tracked_open_count'] < first['tracked_open_count']
    assert second['new_count'] == 0


def test_memory_failure_keeps_rule_result_and_source_snapshot(database, monkeypatch):
    project = Project(name='记忆降级测试')
    database.add(project)
    database.flush()
    result_id = evaluate._store_check_result(database, project, 'resource',
        {'status': '发现问题', 'findings': [{'rule_id': 'r1', 'title': '数量不符'}]}, '中', None,
        report_name='资源.txt')
    def broken(*args, **kwargs):
        raise RuntimeError('模拟记忆失败')
    monkeypatch.setattr(evaluate, 'record_memory', broken)
    evaluate._store_current_source_artifact(database, result_id, b'report', '资源.txt')
    database.commit()
    row = database.get(CheckResult, result_id)
    result = json.loads(row.reference_data)['result']
    assert result['status'] == '发现问题'
    assert len(result['findings']) == 1
    assert result['issue_memory']['status'] == 'unavailable'
    assert row.source_artifacts[0].content == b'report'


def test_failed_module_still_produces_visible_failure_record(database, tmp_path):
    report = tmp_path / '失败报告.docx'
    report.write_bytes(b'not-a-real-docx')
    factory = sessionmaker(bind=database.bind)
    with (
        patch.object(evaluate, 'SessionLocal', factory),
        patch.object(evaluate, '_run_module_check', side_effect=RuntimeError('模型输出被截断')),
        patch.object(evaluate, 'get_llm_status', return_value={'model': 'DeepSeek-V4-Flash'}),
    ):
        evaluate._run_evaluate_task(
            'failed-module-record', str(report), report.name, None, None,
            '失败记录项目', None, ['duplicate'], 'local', True, None,
        )

    task = evaluate.TASKS.pop('failed-module-record')
    assert task['status'] == 'failed'
    assert len(task['result_ids']) == 1
    aggregate = database.scalar(select(CheckResult).where(CheckResult.check_subtype == 'aggregate'))
    payload = json.loads(aggregate.reference_data)['result']
    assert payload['status'] == 'failed'
    assert payload['summary']['failure_reason'] == '模型输出被截断'
    assert payload['model_name'] == 'DeepSeek-V4-Flash'


def test_legacy_aggregate_survives_and_detail_rows_are_hidden(database):
    project = Project(name='历史项目')
    visible = row(project, count=2)
    database.add_all([visible, row(project, subtype='resource_rule_15_16'), row(project, subtype='price_rule')])
    database.commit()
    runs = projects._project_tree_to_dict(project)['check_runs']
    assert len(runs) == 1
    assert [r['id'] for r in runs[0]['results']] == [visible.id]
    assert runs[0]['results'][0]['findings_count'] == 2


def test_real_zero_finding_aggregate_is_not_hidden(database):
    project = Project(name='无问题项目')
    run = CheckRun(project=project, report_name='报告.docx', status='completed')
    visible = row(project, run=run)
    database.add_all([visible, row(project, subtype='resource_rule_15_16', run=run)])
    database.commit()
    results = projects._check_run_to_dict(run)['results']
    assert len(results) == 1 and results[0]['id'] == visible.id
    assert results[0]['findings_count'] == 0


def test_details_alone_do_not_create_fake_report(database):
    project = Project(name='内部明细项目')
    database.add(row(project, subtype='resource_rule_15_16'))
    database.commit()
    assert projects._project_tree_to_dict(project)['check_runs'] == []
    assert len(project.check_results) == 1  # No destructive data cleanup.


def test_repeated_runs_are_not_collapsed(database):
    project = Project(name='重复检测项目')
    for _ in range(2):
        run = CheckRun(project=project, report_name='相同报告.docx', status='completed')
        database.add(row(project, run=run, count=1))
    database.commit()
    runs = projects._project_tree_to_dict(project)['check_runs']
    assert len(runs) == 2
    assert all(len(run['results']) == 1 for run in runs)


def test_baseline_api_lists_only_visible_runs_and_links_renamed_report(database):
    project = Project(name='基准选择项目')
    old = CheckRun(project=project, report_name='原报告.docx', status='completed')
    hidden = CheckRun(project=project, report_name='仅明细.docx', status='completed')
    database.add_all([row(project, run=old, count=1), row(project, run=hidden, subtype='resource_rule_15_16')])
    database.commit()
    app = FastAPI()
    app.include_router(projects.router, prefix='/projects')
    app.dependency_overrides[get_db] = lambda: database
    with TestClient(app) as client:
        listed = client.get(f'/projects/{project.id}/review-baselines')
        assert [item['id'] for item in listed.json()['items']] == [old.id]
        response = client.post(f'/projects/{project.id}/check-runs', json={
            'report_name': '改名修订版.docx', 'memory_baseline_id': old.id})
    assert response.status_code == 200
    link = database.get(ReviewRunLink, response.json()['id'])
    assert link.baseline_run_id == old.id


def test_baseline_api_rejects_another_project_and_keeps_existing_records(database):
    first = Project(name='项目甲')
    second = Project(name='项目乙')
    old = CheckRun(project=first, report_name='报告.docx', status='completed')
    database.add_all([row(first, run=old), second])
    database.commit()
    app = FastAPI()
    app.include_router(projects.router, prefix='/projects')
    app.dependency_overrides[get_db] = lambda: database
    with TestClient(app) as client:
        response = client.post(f'/projects/{second.id}/check-runs', json={
            'report_name': '报告.docx', 'memory_baseline_id': old.id})
    assert response.status_code == 400
    assert database.get(CheckRun, old.id) is not None
    assert database.query(CheckRun).filter_by(project_id=second.id).count() == 0
