"""Offline comparison of real reports against an isolated checker snapshot.

Use --backend to select before/after sources; no original document is modified.
Duplicate's model-based pipeline is covered by deterministic provider fixtures
in the backend suite, not by paid calls from this verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['DEEPSEEK_API_KEY'] = ''
    os.environ['LLM_API_KEY'] = ''
    sys.path.insert(0, str(args.backend.resolve()))
    sys.stdout.reconfigure(encoding='utf-8')
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.api.evaluate import _run_module_check, _attach_finding_locations
    from app.db.models import Base, Project
    from app.modules.completeness.service import check_completeness

    entries = json.loads((root / '.codex-run/basis-report-audit/summary.json').read_text(encoding='utf-8'))
    output = {'sources': [], 'results': [], 'limitations': [
        'Live model/provider calls intentionally disabled; duplicate pipeline covered by fixture regression.',
        'Baseline includes the authorized price standard to compare calculations with identical data.',
    ]}
    volatile = {'created_at', 'updated_at', 'checked_at', 'elapsed_seconds', 'parse_elapsed_seconds',
                'duration_seconds', 'elapsed_ms', 'duration_ms'}
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in volatile}
        if isinstance(value, list):
            return [stable(v) for v in value]
        if isinstance(value, str):
            return re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '<generated-id>', value)
        return value

    for entry in entries:
        path = Path(entry['path'])
        if not path.is_file():
            candidates = list((root / '.codex-run/comparison-20261113/archive/samples').rglob(path.name))
            if not candidates:
                raise FileNotFoundError(path)
            path = candidates[0]
        content = path.read_bytes()
        output['sources'].append({'label': entry['label'], 'path': str(path),
                                  'sha256': hashlib.sha256(content).hexdigest()})
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with Session(engine) as db, \
                patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Network disabled in offline regression')), \
                patch('requests.sessions.Session.request', side_effect=AssertionError('Network disabled in offline regression')):
            project = Project(name=entry['label'])
            db.add(project)
            db.flush()
            for module in ['basis', 'sensitive_word', 'function_correspondence', 'data_reasonableness',
                           'resource', 'security', 'price', 'price_reference', 'completeness']:
                try:
                    if module == 'completeness':
                        result = check_completeness(content, path.name)
                    else:
                        result = _run_module_check(db, module, str(path), path.name, project, entry['label'],
                                                   None, 'local', False, [], {})
                        result = _attach_finding_locations(result, content, path.name)
                except Exception as exc:
                    result = {'verification_error': type(exc).__name__, 'message': str(exc)}
                normalized = stable(result)
                digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                                    default=str).encode('utf-8')).hexdigest()
                output['results'].append({'report': entry['label'], 'module': module, 'sha256': digest,
                                          'result': normalized})
                print(json.dumps({'report': entry['label'], 'module': module, 'sha256': digest}, ensure_ascii=False), flush=True)
        engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
