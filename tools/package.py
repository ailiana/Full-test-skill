#!/usr/bin/env python3
"""Build portable Skill and plugin archives from an explicit release allowlist."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def archive(target, entries):
    # Fixed timestamps and permissions make the same sources reproducible.
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for source, name in sorted(entries, key=lambda entry: entry[1]):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, source.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    skill = ROOT / 'skills' / 'full-test'
    files = [p for p in skill.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    standalone = [(p, 'full-test/' + p.relative_to(skill).as_posix()) for p in files]
    standalone.append((ROOT / 'LICENSE', 'full-test/LICENSE'))
    plugin = [(p, 'full-test/skills/full-test/' + p.relative_to(skill).as_posix()) for p in files]
    plugin += [(ROOT / name, 'full-test/' + name) for name in
               ['plugin.json', '.codex-plugin/plugin.json', 'README.md', 'LICENSE', 'docs/SOURCES.md']]
    source_files = [ROOT / name for name in ['README.md', 'LICENSE', 'plugin.json',
                    '.codex-plugin/plugin.json', '.gitignore', '.gitattributes',
                    'package.json', 'package-lock.json']]
    for directory, pattern in [('tools', '*.py'), ('tests', '*.py'), ('docs', '*.md'), ('.github/workflows', '*.yml')]:
        source_files.extend((ROOT / directory).glob(pattern))
    source = [(p, 'Full-test-skill/' + p.relative_to(ROOT).as_posix()) for p in files + source_files]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets = [args.output_dir / name for name in
               ['full-test-skill.zip', 'full-test-plugin.zip', 'full-test-source.zip']]
    if any(path.exists() for path in targets):
        parser.error('Release archive already exists; choose a new output directory')
    for target, entries in zip(targets, [standalone, plugin, source]):
        archive(target, entries)
        print(target.resolve())


if __name__ == '__main__':
    main()
