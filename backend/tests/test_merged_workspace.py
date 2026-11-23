"""Real auth, original review APIs and complete exports share one application."""
import importlib
import io
import json
from dataclasses import replace

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, CheckResult, SourceArtifact
from app.db.session import get_db


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    main = importlib.import_module('app.main')
    auth = importlib.import_module('app.core.auth')
    evaluate = importlib.import_module('app.api.evaluate')
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def database():
        with factory() as db:
            yield db
    monkeypatch.setattr(auth, 'settings', replace(auth.settings, backend_mode='local'))
    monkeypatch.setattr(main, 'init_db', lambda: None)
    monkeypatch.setattr(evaluate, 'SessionLocal', factory)
    main.app.dependency_overrides[get_db] = database
    try:
        with TestClient(main.app) as client:
            credentials = {'username': 'merged_workspace', 'password': 'Merged-test-password-2026!'}
            assert client.post('/api/auth/register', json=credentials).status_code == 201
            token = client.post('/api/auth/login', json=credentials).json()['access_token']
            client.headers['Authorization'] = 'Bearer ' + token
            yield client, factory
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def report():
    doc = Document()
    doc.add_heading('建设依据', 1)
    doc.add_paragraph('依据《数字政府建设指导意见》建设统一业务服务。')
    doc.add_heading('建设内容', 1)
    doc.add_paragraph('新增统一申报和数据交换功能，系统采用 Oracle 数据库。')
    doc.add_heading('资源申请', 1)
    doc.add_paragraph('申请服务器2台、操作系统2套、数据库1套。')
    doc.add_heading('投资估算', 1)
    doc.add_paragraph('总投资100万元，直接建设费90万元。')
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def test_authenticated_checks_keep_memory_preview_export_and_original_download(workspace):
    client, factory = workspace
    project = client.post('/api/projects', json={'name': '融合检查项目'}).json()['id']
    content = report()
    previous = None
    for index in range(2):
        run = client.post(f'/api/projects/{project}/check-runs', json={
            'report_name': '同一报告.docx', 'source_filename': '同一报告.docx',
            'memory_baseline_id': previous,
        })
        assert run.status_code == 200, run.text
        run_id = run.json()['id']
        response = client.post('/api/evaluate/tasks', files={'file': ('同一报告.docx', content)}, data={
            'project_id': project, 'check_run_id': run_id, 'modules': 'basis,sensitive_word',
            'rule_source': 'local', 'use_llm': 'false',
        })
        assert response.status_code == 200, response.text
        task = client.get('/api/evaluate/tasks/' + response.json()['task_id']).json()
        assert task['status'] == 'completed', task
        assert len(task['result_ids']) == 2
        for result_id in task['result_ids']:
            detail = client.get('/api/evaluate/results/' + result_id)
            assert detail.status_code == 200
            result = detail.json()['result']
            assert result['issue_memory'].get('status') != 'unavailable'
            if index:
                assert result['issue_memory']['has_baseline']
                assert result['issue_memory']['new_count'] == 0
            export = client.get(f'/api/evaluate/results/{result_id}/export')
            assert export.status_code == 200
            assert '修订进展与问题跟踪' in export.text
            with factory() as db:
                source = db.query(SourceArtifact).filter_by(check_result_id=result_id).one()
                source_id = source.id
                assert db.get(CheckResult, result_id).memory_revision is not None
            download = client.get(f'/api/evaluate/results/{result_id}/sources/{source_id}')
            assert download.content == content
            assert download.headers['content-disposition'].startswith('attachment;')
            preview = client.get(f'/api/evaluate/results/{result_id}/sources/{source_id}/preview')
            assert preview.status_code == 200 and 'Oracle' in preview.text
        baselines = client.get(f'/api/projects/{project}/review-baselines')
        assert baselines.status_code == 200
        assert any(row['id'] == run_id for row in baselines.json()['items'])
        previous = run_id


@pytest.mark.parametrize('module', ['basis', 'function_correspondence', 'data_reasonableness',
                                    'resource', 'security', 'price', 'price_reference', 'sensitive_word'])
def test_all_rule_catalogs_remain_available_with_real_auth(workspace, module):
    client, _ = workspace
    response = client.get('/api/evaluate/rules', params={'module': module, 'rule_source': 'local'})
    assert response.status_code == 200, response.text
    assert response.json()['rules']


def test_download_does_not_trust_imported_html_media_type(workspace):
    client, factory = workspace
    project = client.post('/api/projects', json={'name': '附件检查'}).json()['id']
    with factory.begin() as db:
        row = CheckResult(project_id=project, module='resource', check_subtype='aggregate',
                          severity='中', result_label='完成', reason='结果', reference_data=json.dumps({'result': {}}))
        db.add(row)
        db.flush()
        source = SourceArtifact(check_result_id=row.id, filename='report.txt', media_type='text/html',
                                content=b'<script>alert(1)</script>')
        db.add(source)
        db.flush()
        path = f'/api/evaluate/results/{row.id}/sources/{source.id}'
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/plain')
    assert response.headers['x-content-type-options'] == 'nosniff'
