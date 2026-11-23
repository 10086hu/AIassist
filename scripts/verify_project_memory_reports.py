"""Read-only memory regression over original reports and cached rule results.

This checks revision tracking and evidence excerpts, not a new execution of the
rule modules or live model verification. All writes use an in-memory database.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['DEEPSEEK_API_KEY'] = ''
    os.environ['LLM_API_KEY'] = ''
    sys.path.insert(0, str(root / 'backend'))
    sys.stdout.reconfigure(encoding='utf-8')
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db.models import Base, CheckResult, CheckRun, Project
    from app.services.issue_memory import link_run, record_memory, unique_findings
    cache = json.loads((root / '.codex-run/merge-20261113/real-reports-after.json').read_text(encoding='utf-8'))
    evidence = root / '.codex-run/memory-upgrade-20261115'
    output = {'scope': 'Memory-only replay of cached rule findings against four original documents; no rules or models rerun',
              'cases': [], 'sources': []}
    for source in cache['sources']:
        content = Path(source['path']).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        assert digest == source['sha256'], 'Original report changed since prior verification'
        output['sources'].append({'report': source['label'], 'sha256': digest, 'unchanged': True})
        for module in ('basis', 'sensitive_word', 'resource'):
            cached = next(item['result'] for item in cache['results']
                          if item['report'] == source['label'] and item['module'] == module)
            findings = copy.deepcopy(unique_findings(cached)[:3])
            for finding in findings:
                for key in ('issue_memory', 'previous_issue', 'history_priority', 'history_review_order', 'project_memory_context'):
                    finding.pop(key, None)
            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                project = Project(name=source['label'])
                db.add(project)
                db.flush()
                snapshots = []
                rule_payload = {'status': cached['status'], 'findings': findings,
                                'memory_scope': cached.get('memory_scope') or {'module': module, 'source': 'cached-regression'},
                                'rules_used': cached.get('rules_used') or []}
                for revision in (1, 2):
                    when = datetime(2026, 11, 10) + timedelta(days=revision)
                    run = CheckRun(project_id=project.id, report_name=Path(source['path']).name,
                                   source_filename=Path(source['path']).name, status='completed', created_at=when)
                    db.add(run)
                    db.flush()
                    link_run(db, run)
                    row = CheckResult(project_id=project.id, check_run_id=run.id, module=module,
                                      check_subtype='aggregate', result_label=cached['status'], reason='原规则结论',
                                      severity='中', created_at=when,
                                      reference_data=json.dumps({'report_name': Path(source['path']).name,
                                                                 'result': copy.deepcopy(rule_payload)}, ensure_ascii=False))
                    db.add(row)
                    db.flush()
                    snapshot = record_memory(db, row, content, Path(source['path']).name)
                    db.commit()
                    saved = json.loads(row.reference_data)['result']
                    assert saved['status'] == cached['status'] and row.reason == '原规则结论'
                    snapshots.append(snapshot)
                summary = snapshots[-1]['summary']
                assert summary['new_count'] == 0
                assert summary['persistent_count'] == len(findings)
                assert summary['resolved_count'] == 0
                assert summary['same_file'] is True
                assert [item['issue_id'] for item in snapshots[0]['issues']] == [item['issue_id'] for item in snapshots[1]['issues']]
                for issue in snapshots[-1]['issues']:
                    verification = issue.get('verification') or issue.get('review_delta') or {}
                    for candidate in verification.get('evidence_candidates') or []:
                        assert candidate['reference_only'] is True
                        assert candidate['text']
                case = {'report': source['label'], 'module': module, 'tracked_findings': len(findings),
                        'same_file_preserved': True, 'rule_verdict_preserved': True, 'stable_issue_ids': True,
                        'new_count': summary['new_count'], 'persistent_count': summary['persistent_count'],
                        'status': 'passed'}
                output['cases'].append(case)
                print(json.dumps(case, ensure_ascii=False), flush=True)
            engine.dispose()
    output['status'] = 'passed'
    output['case_count'] = len(output['cases'])
    (evidence / 'real-report-memory-replay.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
