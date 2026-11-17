"""Check the explicit publication tree, without printing any matched secret values.
A local guard, not a replacement for an independent secrets scanner before release.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
DIRS={'src','tests','config','docs','deploy','tools','.github'}
FILES={'.gitignore','.dockerignore','README.md','LICENSE','NOTICE.md','VERSION','manage.py','requirements-asr.txt','AGENTS.md'}
PATTERNS=[re.compile(p) for p in [r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'\bAKIA[A-Z0-9]{16}\b',r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}',r'Bearer [A-Za-z0-9_-]{24,}',r'/' + r'Users/[^/\s]+/',r'/' + r'Volumes/[^/\s]+/']]
FORBIDDEN={'.pem','.key','.wav','.opus','.mp3','.shortcut','.sqlite3','.db','.log','.zip'}

def files():
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if any(part in {'.git','__pycache__','.venv'} for part in rel.parts):continue
        if rel.parts[0] in DIRS or str(rel) in FILES:
            if p.is_symlink():raise ValueError('Symlink in publication tree: '+str(rel))
            if p.is_file():yield p

def main():
    errors=[];selected=list(files())
    for p in selected:
        rel=str(p.relative_to(ROOT))
        if p.suffix in FORBIDDEN:errors.append(rel+': private/generated file type')
        if p.name.endswith('-config.json') or p.name in {'client-setup.json','deployment-secrets.json'}:errors.append(rel+': private configuration')
        if p.stat().st_size>1024*1024:errors.append(rel+': unexpected large file');continue
        try:body=p.read_text()
        except UnicodeDecodeError:errors.append(rel+': unexpected binary');continue
        for pattern in PATTERNS:
            if pattern.search(body):errors.append(rel+': possible private value');break
    if (ROOT/'.git').exists():
        tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        allowed={str(p.relative_to(ROOT)) for p in selected}
        for p in filter(None,tracked):
            if p not in allowed:errors.append(p+': outside publication allowlist')
    if errors:
        print('\n'.join(errors));return 1
    print(f'Public tree checks passed: {len(selected)} text files; no bundled data, credentials or models.');return 0

if __name__=='__main__':sys.exit(main())
