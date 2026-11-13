#!/usr/bin/env python3
"""Loopback-only synthetic fixture for the Full Test quick start, not a product."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full Test Demo</title><style>
body{font:18px system-ui;max-width:640px;margin:64px auto;padding:24px;background:#f4f7fb;color:#15263e}
main{background:white;padding:36px;border-radius:18px;box-shadow:0 12px 48px #18304d12}
button{font:inherit;background:#1659da;color:white;border:0;border-radius:8px;padding:12px 22px;cursor:pointer}
output{display:block;padding:20px 0}a{color:#1659da}
</style><main><h1>Full Test Demo</h1><p>Local synthetic QA fixture / 本地合成测试页面</p>
<button type="button" id="counter">Add one</button>
<output aria-live="polite" data-testid="count">Count: 0</output>
<a href="/health">Health endpoint</a></main><script>
let count=0;document.getElementById('counter').addEventListener('click',()=>{
document.querySelector('output').textContent='Count: '+(++count)});
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            payload, mime, code = PAGE.encode('utf-8'), 'text/html; charset=utf-8', 200
        elif self.path == '/health':
            payload, mime, code = json.dumps({'ok': True}).encode(), 'application/json', 200
        elif self.path == '/api/items':
            payload = json.dumps({'items': [{'id': 'synthetic-1', 'name': 'demo-item'}]}).encode()
            mime, code = 'application/json', 200
        else:
            payload, mime, code = b'{"error":"not_found"}', 'application/json', 404
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'Demo: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
