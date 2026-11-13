#!/usr/bin/env python3
"""Check package structure, local reference links and Python syntax without dependencies."""
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'full-test'


def validate():
    errors = []
    required = ['SKILL.md', 'agents/openai.yaml', 'scripts/run_api.py',
                'scripts/import_playwright.py', 'scripts/report.py',
                'references/test-design.md', 'references/ui-testing.md',
                'references/api-testing.md', 'references/reporting.md',
                'references/result-contract.md', 'assets/api-plan.example.json']
    for name in required:
        if not (SKILL / name).is_file():
            errors.append('Missing skill file: ' + name)
    source = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    front = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', source, re.S)
    if not front or not re.search(r'^name: full-test$', front[1], re.M):
        errors.append('Skill frontmatter/name is invalid')
    if not front or not re.search(r'^description: .+', front[1], re.M):
        errors.append('Skill description is missing')
    if '[TODO' in source or '[REPLACE' in source:
        errors.append('Unfinished skill scaffold')
    if len(list(SKILL.rglob('SKILL.md'))) != 1:
        errors.append('Skill bundle must contain exactly one SKILL.md')
    for path in ROOT.rglob('*.md'):
        if any(part in {'.git', 'node_modules', 'test-results', 'dist'} for part in path.parts):
            continue
        for match in re.finditer(r'\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            link = match[1].split('#', 1)[0]
            if not link or re.match(r'^[a-zA-Z][\w+.-]*:', link):
                continue
            if not (path.parent / link).is_file():
                errors.append(f'Broken local link: {path.relative_to(ROOT)} -> {link}')
    for path in list(SKILL.rglob('*.py')) + list((ROOT / 'tools').glob('*.py')):
        try:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except SyntaxError:
            errors.append('Python syntax error: ' + str(path.relative_to(ROOT)))
    for name in ['plugin.json', '.codex-plugin/plugin.json', 'package.json']:
        try:
            data = json.loads((ROOT / name).read_text(encoding='utf-8'))
            if data.get('version') != '0.1.0':
                errors.append('Package versions disagree: ' + name)
        except (OSError, ValueError):
            errors.append('Invalid metadata: ' + name)
    ui = (SKILL / 'agents/openai.yaml').read_text(encoding='utf-8')
    if '$full-test' not in ui or 'display_name:' not in ui:
        errors.append('UI metadata missing invocation/name')
    return errors


if __name__ == '__main__':
    try:
        issues = validate()
    except (OSError, UnicodeError) as exc:
        print('Validation blocked:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
    for issue in issues:
        print(issue, file=sys.stderr)
    if not issues:
        print('Package structure, reference links, metadata and Python syntax: OK')
    sys.exit(bool(issues))
