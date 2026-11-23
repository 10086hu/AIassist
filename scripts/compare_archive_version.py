"""Compare supplied ZIP to the current checkout without extracting or executing it."""
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
ignored = {'.git', '.venv', '__pycache__', 'bin', 'obj', 'node_modules', '.pytest_cache', '.runtime', '.codex-run'}
text_ext = {'.py', '.cs', '.xaml', '.md', '.txt', '.json', '.toml', '.yaml', '.yml', '.ps1', '.bat', '.csproj', '.example', '.gitignore'}

def included(name):
    p = Path(name)
    return not any(part in ignored for part in p.parts) and p.name != '.env' and p.suffix not in {'.pyc', '.db'}

def normalized(data, name):
    if Path(name).suffix in text_ext or Path(name).name in {'Dockerfile', '.gitignore'}:
        try:
            return data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
        except UnicodeDecodeError:
            pass
    return data

with zipfile.ZipFile(archive) as z:
    zipped = {i.filename[len(prefix):]: i for i in z.infolist() if not i.is_dir() and i.filename.startswith(prefix) and included(i.filename[len(prefix):])}
    local = {p.relative_to(root).as_posix(): p for p in root.rglob('*') if p.is_file() and included(p.relative_to(root).as_posix())}
    local.pop('scripts/compare_archive_version.py', None)
    changes = []
    for name in sorted(zipped.keys() & local.keys()):
        old = normalized(local[name].read_bytes(), name)
        new = normalized(z.read(zipped[name]), name)
        if old != new:
            info = {'path': name, 'current_bytes': len(old), 'zip_bytes': len(new)}
            if Path(name).suffix in text_ext or Path(name).name in {'Dockerfile', '.gitignore'}:
                try:
                    diff = list(difflib.unified_diff(old.decode('utf-8').splitlines(), new.decode('utf-8').splitlines(), fromfile='current/'+name, tofile='zip/'+name, lineterm=''))
                    info['diff'] = diff
                except UnicodeDecodeError:
                    pass
            changes.append(info)
    result = {'direction': 'current project -> ZIP (8)', 'current_file_count': len(local), 'zip_file_count': len(zipped), 'only_in_zip': sorted(zipped.keys()-local.keys()), 'only_in_current': sorted(local.keys()-zipped.keys()), 'modified': changes}
    output = root / '.codex-run' / 'archive-8-comparison.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**{k:v for k,v in result.items() if k!='modified'}, 'modified':[{k:v for k,v in x.items() if k!='diff'} for x in changes], 'report':str(output)},ensure_ascii=False))
