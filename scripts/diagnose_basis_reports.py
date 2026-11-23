"""Read-only reproduction of construction basis checks for supplied reports."""
from pathlib import Path
import json
import os
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')

def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)

database = Path(os.environ.get('TEMP', '')) / 'AIassist' / 'app.db'
if database.exists():
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = [row[1] for row in db.execute('PRAGMA table_info(check_results)')]
        emit({'database': str(database), 'columns': columns})
        if 'module' in columns:
            rows = db.execute("SELECT * FROM check_results WHERE module='basis' ORDER BY created_at DESC LIMIT 20").fetchall()
            for row in rows:
                value = dict(row)
                result = value.get('reference_data')
                try:
                    result = json.loads(result) if isinstance(result, str) else result
                except Exception:
                    result = None
                payload = result if isinstance(result, dict) else {}
                nested = payload.get('result') or {}
                emit({'saved': {key: value.get(key) for key in ['id','created_at']}, 'report_name': payload.get('report_name'), 'status': nested.get('status'), 'summary': nested.get('summary'), 'notices': nested.get('notices')})

if '--history-only' in sys.argv:
    sys.exit(0)

from app.modules.shanghai_review.base import BaseValidator
from app.modules.shanghai_review import construction_basis_validator as validators
from app.modules.shanghai_review.service import run_basis_check_from_document

paths = [
    Path('D:/桌面/可研案例材料/克拉玛依/克拉玛依市公安局警务保障平台项目可行性报告完整版250806(1).docx'),
    Path('D:/桌面/可研案例材料/克拉玛依/克拉玛依市数据资源和政务服务中心“高效办成一件事”项目可行性研究报告20250718(1).docx'),
    Path('D:/桌面/可研案例材料/b类升级改造/2上海市国资国企在线监管服务平台大系统建设项目（2025年升级改造）可行性研究报告.docx'),
    Path('D:/桌面/20260128上海海关智关航贸建设项目可行性研究.docx'),
]
for path in paths:
    start = time.monotonic()
    try:
        content = path.read_bytes()
        document = BaseValidator.parse_document(content, path.name)
        emit({'file': path.name, 'bytes': len(content), 'characters': len(document['_full_text']), 'sections': {k: {'chars': len(str(document[k])), 'preview': str(document[k])[:100], 'full_text_fallback': document[k] == document['_full_text']} for k in ['2.6','2.7','4.2','4.7','6.1','6.6','8.2']}})
        for name in ['_validate_investment','_validate_indicator','_validate_security','_validate_module_budget']:
            try:
                errors = getattr(validators, name)(document)
                emit({'file': path.name, 'validator': name, 'issues': [{'code': e.code, 'message': e.message} for e in errors]})
            except Exception:
                emit({'file': path.name, 'validator': name, 'error': traceback.format_exc()})
        try:
            original_parse = BaseValidator.parse_document
            try:
                BaseValidator.parse_document = staticmethod(lambda *_: document)
                result = run_basis_check_from_document(content, path.name)
            finally:
                BaseValidator.parse_document = staticmethod(original_parse)
            emit({'file': path.name, 'status': result['status'], 'summary': result['summary'], 'rules': [{'rule': r['rule_id'], 'status': r['status'], 'count': len(r['issues'])} for r in result['results']]})
        except Exception:
            emit({'file': path.name, 'service_error': traceback.format_exc()})
    except Exception:
        emit({'file': path.name, 'parse_error': traceback.format_exc()})
    emit({'file': path.name, 'seconds': round(time.monotonic()-start, 2)})
