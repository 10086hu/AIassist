"""Import only reviewed duplicate-check changes; preserve unrelated project code."""
from pathlib import Path
import difflib
import hashlib
import json
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8')
root = Path(__file__).resolve().parents[1]
archive = Path('C:/Users/86180/Downloads/AIassist-duplicate-check-completion (8).zip')
prefix = 'AIassist-duplicate-check-completion/'
files = [
    'backend/app/modules/duplicate/candidate_search.py',
    'backend/app/modules/duplicate/compact_extractor.py',
    'backend/app/modules/duplicate/document_cache.py',
    'backend/app/modules/duplicate/document_parser.py',
    'backend/app/modules/duplicate/llm_extractor.py',
    'backend/app/modules/duplicate/llm_judge.py',
    'backend/app/modules/duplicate/service.py',
    'backend/tests/test_duplicate_compare_tasks.py',
    'backend/tests/test_duplicate_long_documents.py',
    'backend/tests/test_duplicate_llm_and_preview.py',
    'docs/duplicate_long_report_review.md',
    'scripts/benchmark_duplicate_long_report.py',
]
shared = ['backend/app/api/evaluate.py', 'backend/app/core/config.py',
          'desktop-wpf/MainWindow.xaml.cs', '.env.example', '.gitignore', 'backend/README.md']
backup = root / '.codex-run' / 'duplicate-archive-8-before'
ignored = {'.git', '.venv', '__pycache__', 'bin', 'obj', 'node_modules', '.pytest_cache', '.runtime', '.codex-run'}

def normalize(data):
    return data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')

def hashes():
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()
            and not any(part in ignored for part in p.relative_to(root).parts)}

if '--verify' in sys.argv:
    record = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    before, after = record['before'], hashes()
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    unexpected = sorted(set(changed) - set(record['allowed']))
    print(json.dumps({'changed': changed, 'unexpected_changes': unexpected}, ensure_ascii=False))
    sys.exit(bool(unexpected))

assert not backup.exists(), 'Backup already exists; do not merge twice.'
baseline = json.loads((root / '.codex-run/archive-8-comparison.json').read_text(encoding='utf-8'))
expected_diffs = {item['path']: item.get('diff') for item in baseline['modified']}
updates = {}
with zipfile.ZipFile(archive) as z:
    for name in files + shared:
        data = z.read(prefix + name)
        local = root / name
        if local.exists():
            old, new = normalize(local.read_bytes()), normalize(data)
            diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                        fromfile='current/'+name, tofile='zip/'+name, lineterm=''))
            assert diff == expected_diffs[name], f'File changed since comparison: {name}'
        else:
            assert name in baseline['only_in_zip'], f'Unexpected new file: {name}'
        if name == 'desktop-wpf/MainWindow.xaml.cs':
            # Retain the construction-basis failure display fix; import only
            # the duplicate comparison upload/polling method.
            old = normalize(local.read_bytes())
            start = '    private async Task<JsonDocument> UploadAndRunDuplicateCompareAsync('
            end = '    private IEnumerable<string> SelectedRuleIdsForModule('
            replacement = normalize(data).split(start, 1)[1].split(end, 1)[0]
            original = old.split(start, 1)[1].split(end, 1)[0]
            merged = old.replace(start + original + end, start + replacement + end, 1)
            assert '本次检查执行失败，尚未形成有效结论；请修复后重新检测。' in merged
            data = merged.encode('utf-8')
        updates[name] = data

before = hashes()
backup.mkdir(parents=True)
for name, data in updates.items():
    destination = root / name
    if destination.exists():
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(destination.read_bytes())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
(backup / 'manifest.json').write_text(json.dumps({'before': before, 'allowed': list(updates)},
    ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'imported': list(updates), 'backup': str(backup)}, ensure_ascii=False))
