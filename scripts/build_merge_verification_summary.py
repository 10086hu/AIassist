"""Collect local merge verification evidence without loading runtime secrets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    evidence = root / '.codex-run/merge-20261113'
    load = lambda path: json.loads(path.read_text(encoding='utf-8-sig'))
    before = load(evidence / 'before-merge-sha256.json')
    modules = load(root / '.codex-run/comparison-20261113/current-rule-sha256.json')
    comparison = load(root / '.codex-run/comparison-20261113/comparison.json')
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    changed = [path for path, sha in modules.items() if digest(root / path) != sha]
    protected = [
        'backend/app/services/issue_memory.py',
        'backend/app/services/llm_client.py',
        'backend/app/services/result_refiner.py',
        'backend/app/services/check_service.py',
        'backend/app/api/projects.py',
        'backend/app/schemas.py',
    ]
    suites = ET.parse(evidence / 'backend-regression.xml').getroot().findall('testsuite')
    test_count = sum(int(item.get('tests', 0)) for item in suites)
    skipped = sum(int(item.get('skipped', 0)) for item in suites)
    failures = sum(int(item.get('failures', 0)) for item in suites)
    errors = sum(int(item.get('errors', 0)) for item in suites)
    skips = [
        {'test': case.get('name'), 'reason': case.find('skipped').get('message')}
        for suite in suites for case in suite.findall('testcase')
        if case.find('skipped') is not None
    ]
    summary = {
        'verification_date': '2026-11-13',
        'workspace': str(root),
        'source_snapshot': 'before-merge-source.zip',
        'original_module_files': len(modules),
        'unchanged_original_module_files': len(modules) - len(changed),
        'changed_original_module_files': changed,
        'module_change_reasons': {
            'backend/app/modules/price/service.py': 'Authorized missing-standard catalog handling from archive; formulas unchanged.',
            'backend/app/modules/price/standards.py': 'Authorized missing-standard error handling from archive; formulas unchanged.',
            'backend/app/modules/shanghai_review/security_review.py': 'Fix pre-existing group(1) IndexError by restoring capture group; match range unchanged.',
        },
        'protected_shared_files_unchanged': {path: digest(root / path) == before[path] for path in protected},
        'original_files_missing': [path for path in before if not (root / path).is_file()],
        'original_only_files_missing': [path for path in comparison['current_only'] if not (root / path).is_file()],
        'archive_only_files_missing': [path for path in comparison['archive_only'] if not (root / path).is_file()],
        'api_inventory': load(evidence / 'feature-inventory.json'),
        'backend_tests': {
            'total': test_count, 'passed': test_count - skipped - failures - errors,
            'skipped': skipped, 'failures': failures, 'errors': errors, 'skip_details': skips,
            'evidence': 'backend-regression.xml',
        },
        'boundaries': [
            'All verification uses isolated databases; existing user database and original report files are not modified.',
            'No live paid model calls; model-dependent paths use deterministic provider fixtures.',
            'No production deployment or real MySQL migration was performed.',
            'Native desktop windows were not manually visually reviewed; build and automated control tests are recorded separately.',
            'Archive transfer limits retained: 32 MiB attachments, 48 MiB package, 50 MiB request.',
        ],
    }
    optional = {
        'real_reports': 'real-report-comparison.json',
        'desktop': 'desktop-verification.json',
        'live_memory': 'live/memory-roundtrip-verification.json',
    }
    for key, name in optional.items():
        path = evidence / name
        if not path.is_file():
            summary[key] = {'status': 'pending', 'evidence': name}
        elif key == 'real_reports':
            data = load(path)
            summary[key] = {field: data[field] for field in (
                'source_files_identical', 'before_count', 'after_count', 'compared_count',
                'identical_count', 'changed_count', 'after_status_counts',
                'only_expected_security_exception_fix')}
            summary[key]['changed_results'] = [
                {field: item[field] for field in (
                    'report', 'module', 'before_errors', 'after_errors', 'before_status', 'after_status')}
                for item in data['results'] if not item['same']
            ]
            summary[key]['after_error_results'] = [
                {'report': item['report'], 'module': item['module'], 'errors': item['after_errors']}
                for item in data['results'] if item['after_errors']
            ]
            summary[key]['after_execution_summary'] = data.get('after_execution_summary', {})
            summary[key]['evidence'] = name
        else:
            summary[key] = {'evidence': name, **load(path)}
    (evidence / 'verification-summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'backend': summary['backend_tests'],
        'changed_modules': changed,
        'protected_shared_files_unchanged': summary['protected_shared_files_unchanged'],
        'missing_original_files': summary['original_files_missing'],
        'missing_archive_additions': summary['archive_only_files_missing'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
