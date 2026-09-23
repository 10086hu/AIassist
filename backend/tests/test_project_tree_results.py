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
from app.db.models import Base, CheckResult, CheckRun, Project
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
