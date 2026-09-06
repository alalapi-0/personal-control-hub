"""Read-only MCP handshake against the actual configured Figma command."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

CACHE = Path.home() / '.npm/_npx/e05028111420be70'


def package_fingerprint():
    # Existing package/dependency metadata, not cache access times or npm logs.
    files = ['package-lock.json', 'node_modules/.package-lock.json',
             'node_modules/figma-developer-mcp/package.json',
             'node_modules/figma-developer-mcp/dist/bin.js']
    return {p: hashlib.sha256((CACHE / p).read_bytes()).hexdigest() for p in files}


def probe():
    cfg = json.loads((Path.home() / '.claude.json').read_text())['mcpServers']['figma']
    before = package_fingerprint()
    env = dict(os.environ, FRAMELINK_TELEMETRY='off', npm_config_offline='true',
               npm_config_ignore_scripts='true', npm_config_update_notifier='false',
               PYTHONDONTWRITEBYTECODE='1')
    env.pop('FIGMA_API_KEY', None)
    process = subprocess.Popen([cfg['command'], *cfg['args']], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, start_new_session=True, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
    selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
    buffer = b''
    received = {}
    def send(payload):
        process.stdin.write(json.dumps(payload).encode() + b'\n')
        process.stdin.flush()
    def response(request_id):
        nonlocal buffer
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            for key, _ in selector.select(min(1, max(0, deadline - time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == 'stderr':
                    continue  # Never surface raw child errors or configuration.
                buffer += data
                if len(buffer) > 1048576:
                    raise RuntimeError('MCP output exceeded limit')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(item, dict) and 'id' in item:
                        received[item['id']] = item
            if request_id in received:
                item = received.pop(request_id)
                if 'error' in item or 'result' not in item:
                    raise RuntimeError('MCP request failed')
                return item['result']
            if process.poll() is not None:
                raise RuntimeError('MCP process exited before response')
        raise RuntimeError('MCP response timed out')
    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'hub-credential-check', 'version': '1.0'}}})
        initialized = response(1)
        assert initialized.get('protocolVersion') and initialized.get('serverInfo')
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        tools = response(2)['tools']
        assert tools and all(isinstance(t.get('name'), str) for t in tools)
        result = {'status': 'PASS', 'actual_configured_command': True,
                  'initialize': True, 'tool_count': len(tools),
                  'tool_names': [t['name'] for t in tools], 'tools_called': 0,
                  'telemetry_disabled_only_in_probe': True, 'npm_offline': True,
                  'package_metadata_hashes': before, 'secret_output': False}
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()
    assert package_fingerprint() == before, 'Existing package metadata changed'
    result['process_closed'] = True
    result['existing_package_unchanged'] = True
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(probe()))
    except Exception:
        raise SystemExit('Controlled MCP validation failed; raw child output withheld')
