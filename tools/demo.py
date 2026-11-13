#!/usr/bin/env python3
"""Run actual loopback API checks and optional Playwright UI checks, then merge reports."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'full-test'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--with-ui', action='store_true')
    args = parser.parse_args()
    run = (args.out_dir or ROOT / 'test-results' / datetime.now(timezone.utc).strftime('demo-%Y%m%dT%H%M%S%fZ')).resolve()
    if run.exists() and any(run.iterdir()):
        parser.error('Use a new empty run directory')
    cli = ROOT / 'node_modules' / '@playwright' / 'test' / 'cli.js'
    if args.with_ui and not cli.is_file():
        parser.error('UI demo needs npm ci and npx playwright install chromium first')
    run.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('full_test_demo', SKILL / 'assets' / 'demo_app.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    target = f'http://127.0.0.1:{server.server_port}'
    inputs, codes = [], []
    try:
        api = run / 'api-results.json'
        codes.append(subprocess.run([sys.executable, str(SKILL / 'scripts' / 'run_api.py'),
                     '--plan', str(SKILL / 'assets' / 'api-plan.example.json'),
                     '--base-url', target, '--output', str(api)], cwd=ROOT).returncode)
        if api.is_file():
            inputs += ['--input', str(api)]
        if args.with_ui:
            env = dict(os.environ, BASE_URL=target, FULL_TEST_RUN_DIR=str(run))
            codes.append(subprocess.run(['node', str(cli), 'test', '--config',
                         str(SKILL / 'assets' / 'playwright.config.cjs')], cwd=ROOT, env=env).returncode)
            raw, ui = run / 'playwright.json', run / 'ui-results.json'
            if raw.is_file():
                codes.append(subprocess.run([sys.executable, str(SKILL / 'scripts' / 'import_playwright.py'),
                             '--input', str(raw), '--output', str(ui), '--target', target], cwd=ROOT).returncode)
                if ui.is_file():
                    inputs += ['--input', str(ui)]
        if not inputs:
            print('No result file produced; report generation blocked', file=sys.stderr)
            return 2
        codes.append(subprocess.run([sys.executable, str(SKILL / 'scripts' / 'report.py'),
                     *inputs, '--out-dir', str(run / 'report')], cwd=ROOT).returncode)
        print('Demo artifacts:', run)
        return 2 if 2 in codes else 1 if any(codes) else 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == '__main__':
    raise SystemExit(main())
