"""Disposable two-origin browser fixture. No Codex adapter or executable task grant."""
from __future__ import annotations

import copy
import io
import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_workbench as fixtures
from hub.design_store import DesignStore, content_hash_bytes
from hub.design_records import with_content_hash
from hub.design_service import DesignService
from hub.preview_bridge import FixtureBridge
from hub.owner_auth import OwnerAuth
from hub.local_service import HubHTTPServer
from hub.task_store import TaskStore
from hub.task_service import TaskService
from PIL import Image, ImageDraw

PROOF = 'Synthetic-fixture-owner-' + 'a' * 64
f = fixtures.WorkbenchTests(); f.setUp()
f.projects.list_projects = lambda **_: {'projects': [{'project_id': 'fixture-project', 'name': '隔离测试项目'}]}
image = Image.new('RGB', (600, 800), '#f3f5f8'); draw = ImageDraw.Draw(image)
draw.rectangle((40, 40, 560, 120), fill='#172c45'); draw.text((60, 65), 'Controlled preview', fill='white')
draw.rectangle((40, 180, 560, 300), fill='#0e5968'); draw.text((60, 220), 'Sample card', fill='white')
output = io.BytesIO(); image.save(output, format='PNG'); f.fixture.png_path.write_bytes(output.getvalue())
facts = copy.deepcopy(f.fixture.store.read()['facts'])
for fact in facts:
    if fact['kind'] == 'artifact_ref' and fact['id'] == f.fixture.png_artifact['id']:
        fact['sha256'] = content_hash_bytes(output.getvalue())
    elif fact['kind'] == 'candidate':
        fact['artifact_bindings'][0]['sha256'] = content_hash_bytes(output.getvalue())
        fact.update(with_content_hash(fact))
store = DesignStore(f.root, f.fixture.work / 'browser-store.json', fixture=True, fixture_project_ids={'fixture-project'})
revision = store.initialize()['revision']
for index, fact in enumerate(facts):
    state, _ = store.append_fact(fact, expected_revision=revision, request_id=f'browser-fact-{index}')
    revision = state['revision']
f.service.designs = DesignService(store)
tasks = TaskStore(f.root)
tasks.register_storage()  # Explicit disposable ledger setup; no recovery initialization.
task_service = TaskService(f.projects, f.service, tasks)
hub = HubHTTPServer(f.projects, f.service.designs, owner_auth=OwnerAuth(provider=lambda: PROOF),
                    previews=f.service, tasks=task_service)
script = (Path(__file__).resolve().parents[1] / 'src/hub/web/fixture_bridge.js').read_bytes()
control = f.root / 'control.json'; control.write_text('{"mode":"valid"}')
initial_control = control.read_text()


def mode():
    value = json.loads(control.read_text()).get('mode')
    assert value in {'valid', 'missing', 'csp', 'load_error', 'timeout', 'navigation'}
    return value


class Child(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Host') != 'localhost:' + str(self.server.server_port):
            self.send_error(403); return
        state = mode()
        if self.path == '/fixture' and state != 'load_error':
            source = '' if state == 'timeout' else '<script src="/bridge.js"></script>'
            data = (f'<!doctype html><html><head><meta charset="utf-8"><title>Disposable preview</title>'
                    '<style>html,body{margin:0;width:100%;height:100%;background:#f3f5f8;color:#172c45;font:16px sans-serif}'
                    'h2{position:absolute;left:6.6667%;top:5%;width:86.6666%;height:10%;margin:0}'
                    'button{position:absolute;left:6.6667%;top:22.5%;width:86.6666%;height:15%;font:inherit}'
                    'p{position:absolute;left:6.6667%;top:50%;width:86.6666%}button:focus-visible{outline:3px solid #c04d00}'
                    '</style></head>'
                    f'<body data-parent-origin="{hub.origin}" data-mode="{state}">'
                    '<h2 data-hint-id="sample-heading" data-hint-role="heading" tabindex="0">隔离方案预览</h2>'
                    '<button id="sample-card" data-hint-id="sample-card" data-hint-role="button">示例卡片</button>'
                    '<p>底层按钮执行次数：<span id="activation-count">0</span><br>仅测试数据 · 没有业务执行权限</p>'
                    f'{source}</body></html>').encode()
            content_type = 'text/html; charset=utf-8'
        elif self.path == '/bridge.js':
            data = script; content_type = 'text/javascript; charset=utf-8'
        else:
            self.send_error(404); return
        csp = f"default-src 'none'; script-src {'\'none\'' if state == 'csp' else '\'self\''}; style-src 'unsafe-inline'; frame-ancestors {hub.origin}; base-uri 'none'; form-action 'none'"
        self.send_response(200)
        self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Security-Policy', csp); self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers(); self.wfile.write(data)

    def do_POST(self): self.send_error(405)
    def log_message(self, *_): pass


child = ThreadingHTTPServer(('127.0.0.1', 0), Child)
child.daemon_threads = False
bridge = FixtureBridge(f.service, 'http://localhost:' + str(child.server_port))
f.service.fixture_bridge = bridge
original_catalog = f.service.catalog


def catalog():
    f.service.fixture_bridge = None if mode() == 'missing' else bridge
    return original_catalog()


f.service.catalog = catalog
threads = [threading.Thread(target=s.serve_forever) for s in (hub, child)]
for thread in threads: thread.start()
preview_id = original_catalog()['previews'][0]['binding']['preview_id']
print(json.dumps({'origin': hub.origin, 'child_origin': bridge.origin, 'preview_id': preview_id,
                  'root': str(f.root), 'control': str(control), 'classification': 'synthetic_fixture'}), flush=True)
stop = threading.Event()
signal.signal(signal.SIGINT, lambda *_: stop.set())
signal.signal(signal.SIGTERM, lambda *_: stop.set())
stop.wait()
for server in (hub, child): server.shutdown(); server.server_close()
for thread in threads: thread.join()
f.doCleanups()
print('owned fixture servers closed and disposable data removed', flush=True)
