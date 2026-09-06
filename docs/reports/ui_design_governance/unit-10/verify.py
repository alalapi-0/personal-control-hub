"""Read-only TC10 verification. Credential values stay in controlled memory."""
from pathlib import Path
import copy
import hashlib
import json
import os
import subprocess
import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).parent


def main():
    baseline = json.loads((UNIT / 'baseline.json').read_text())
    registration = json.loads((UNIT / 'claude-registration.json').read_text())
    preserved = {**baseline['protected_hub_hashes'], **baseline['preserved_global_file_hashes']}
    preserved.pop('/Users/alalapi/.claude.json')
    drift = baseline['external_input_drift']
    assert preserved.pop(drift['path']) == drift['original_sha256']
    assert drift['actor'] == 'UNKNOWN' and not drift['reliable_content_preimage']
    assert not drift['task_wrote_file'] and drift['exact_semantic_preservation'] == 'UNPROVEN'
    drift_path = Path(drift['path'])
    assert hashlib.sha256(drift_path.read_bytes()).hexdigest() == drift['observed_sha256']
    assert drift_path.stat().st_mtime == drift['observed_mtime']
    drift_config = tomllib.loads(drift_path.read_text())['mcp_servers']
    assert list(drift_config) == drift['mcp_names'] and len(drift_config) == 7
    assert {k: [x for x in v if any(y in x for y in ('token', 'auth', 'header'))]
            for k, v in drift_config.items()} == drift['auth_field_names']
    for name, expected in preserved.items():
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    source = Path(baseline['claude_config_path'])
    current = json.loads(source.read_text())
    assert current == registration['legacy_settings_postimage']
    restored = copy.deepcopy(current)
    restored['mcpServers']['figma'] = baseline['claude_redacted_preimage']['mcpServers']['figma']
    assert restored == baseline['claude_redacted_preimage']
    assert source.stat().st_mode & 0o777 == 0o644
    user_path = Path.home() / '.claude.json'
    user = json.loads(user_path.read_text())
    assert user['mcpServers'] == registration['mcp_postimage']
    assert user_path.stat().st_mode & 0o777 == 0o600
    assert len(user['projects']) == 8
    assert not source.with_name('settings.json.tc10.tmp').exists()
    assert not user_path.with_name('.claude.json.tc10.tmp').exists()
    old_mcp = json.loads(registration['mcp_redacted_raw_preimage'])
    restored = copy.deepcopy(user['mcpServers']); restored.pop('figma')
    for name, cid, variable in [('github', 'claude-github-token', 'GITHUB_PERSONAL_ACCESS_TOKEN'),
                                ('stitch', 'claude-stitch-api', 'STITCH_API_KEY')]:
        old = old_mcp[name]; now = restored[name]
        assert now['command'] == '/usr/bin/python3'
        assert now['args'] == [str(ROOT / 'scripts/tool_credentials.py'), 'run', cid, '--', old['command'], *old['args']]
        assert variable not in now.get('env', {})
        now['command'], now['args'] = old['command'], old['args']
        now['env'][variable] = '<REDACTED:' + name.upper() + '>'
    assert restored == old_mcp
    assert user['mcpServers']['figma'] == {'command': '/usr/bin/python3', 'args': [
        str(ROOT / 'scripts/tool_credentials.py'), 'run', 'figma-api', '--', 'npx', '-y',
        'figma-developer-mcp', '--stdio'], 'env': {}}
    index = json.loads((ROOT / 'data/credentials/global_tools.json').read_text())
    assert len({e['id'] for e in index['credentials']}) == len(index['credentials']) == 5
    configured = {}
    for name in index['coverage']['configs']:
        path = Path(name).expanduser()
        cfg = tomllib.loads(path.read_text()) if path.suffix == '.toml' else json.loads(path.read_text())
        for server, entry in cfg.get('mcp_servers', cfg.get('mcpServers', {})).items():
            configured[(name, server)] = entry
    consumers = index['coverage']['consumer_configs']
    assert len(consumers) == len(configured) == 19
    assert set(configured) == {(e['config_ref'], e['server']) for e in consumers}
    for item in consumers:
        cfg = configured[(item['config_ref'], item['server'])]
        assert item['env_names'] == list(cfg.get('env', {}))
        assert item['header_names'] == list(cfg.get('headers', cfg.get('http_headers', {})))
        assert item['env_header_names'] == list(cfg.get('env_http_headers', {}))
    by_id = {e['id']: e for e in index['credentials']}
    assert by_id['claude-github-token']['auth_status'] == 'invalid'
    assert by_id['claude-stitch-api']['auth_status'] == 'unverified'
    assert by_id['figma-api']['auth_status'] == 'identity_verified'
    # Restore only the old MCP value in memory, proving every other original byte.
    # Read all three migrated values with the exact configured native Python host.
    scan = '''import importlib.util,json,hashlib,subprocess
from pathlib import Path
root=Path.cwd();unit=root/"docs/reports/ui_design_governance/unit-10"
s=importlib.util.spec_from_file_location("credentials",root/"scripts/tool_credentials.py");m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
backend=m.Keychain();values={}
for cid in ("figma-api","claude-github-token","claude-stitch-api"):
 e=m.load_entry(cid);values[cid]=backend.get(e["service"],e["account"])
r=json.loads((unit/"claude-registration.json").read_text());text=(Path.home()/".claude.json").read_text();d=json.JSONDecoder();i=text.index("{")+1
while True:
 while text[i].isspace() or text[i]==",":i+=1
 key,i=d.raw_decode(text,i)
 while text[i].isspace() or text[i]==":":i+=1
 start=i;value,end=d.raw_decode(text,i)
 if key=="mcpServers":break
 i=end
old=r["mcp_redacted_raw_preimage"]
for name,cid in (("GITHUB","claude-github-token"),("STITCH","claude-stitch-api")):
 old=old.replace(json.dumps("<REDACTED:"+name+">"),json.dumps(values[cid].decode()))
restored=text[:start]+old+text[end:]
assert hashlib.sha256(restored.encode()).hexdigest()==r["global_config_preimage_sha256"]
paths=[root/"STATE.yaml",root/"scripts/tool_credentials.py",root/"tests/test_tool_credentials.py",root/"data/credentials/global_tools.json",root/"docs/global_tool_credentials.md",Path.home()/".claude/settings.json",Path.home()/".claude.json",Path.home()/".codex/config.toml",Path.home()/".cursor/mcp.json",*list(unit.glob("*"))]
assert all(value not in p.read_bytes() for p in paths if p.is_file() for value in values.values())
staged=subprocess.check_output(["git","diff","--cached","--binary"])
assert all(value not in staged for value in values.values())
print(json.dumps({"native_retrieval":3,"original_global_bytes_reconstructed":True,"owned_and_staged_content_secret_absent":True,"value_returned":False}))
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for variable in ('FIGMA_API_KEY', 'GITHUB_PERSONAL_ACCESS_TOKEN', 'STITCH_API_KEY'):
        env.pop(variable, None)
    result = subprocess.run(['/usr/bin/python3', '-c', scan], cwd=ROOT, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, 'controlled native preservation/secret scan failed'
    native = json.loads(result.stdout)
    assert native['original_global_bytes_reconstructed'] and native['owned_and_staged_content_secret_absent']
    for check in registration['credential_checks']:
        assert check['source_equal'] and check['native_readback_equal'] and check['keychain_created']
        assert check['http_status'] == check['baseline_http_status']
    assert registration['stitch_unauthenticated_control_http_status'] == 200
    assert registration['stitch_authentication_proven'] is False
    assert registration['plaintext_removed_after_shadow']
    assert registration['legacy_declaration_removed_after_discovery_and_handshake']
    discovery = json.loads((UNIT / 'claude-discovery.json').read_text())
    assert discovery['exit_code'] == 0 and discovery['user_scope_discovered']
    assert discovery['connection_status'] == 'Connected' and discovery['configuration_unchanged']
    from mcp_probe import package_fingerprint
    mcp = json.loads((UNIT / 'mcp-validation.json').read_text())
    assert mcp['status'] == 'PASS' and mcp['initialize'] and mcp['tools_called'] == 0
    assert mcp['telemetry_disabled_only_in_probe'] and mcp['process_closed']
    assert mcp['package_metadata_hashes'] == package_fingerprint()
    routes = json.loads((UNIT / 'existing-routes.json').read_text())['checks']
    for route in routes:
        variable = 'STITCH_API_KEY' if 'stitch' in route['launcher'] else 'GITHUB_PERSONAL_ACCESS_TOKEN'
        code = 'import os;raise SystemExit(0 if os.environ.get(%r) else 2)' % variable
        result = subprocess.run([route['launcher'], '/usr/bin/python3', '-c', code], env=env, capture_output=True, timeout=20)
        assert result.returncode == 0, 'existing secure route failed'
    state = yaml.safe_load((ROOT / 'STATE.yaml').read_text())
    old_state = yaml.safe_load(baseline['state_preimage'])
    for key in state:
        if key not in {'last_updated', 'current_work'}:
            assert state[key] == old_state[key], key
    assert state['current_work']['unit'] == 'HUB-10-GLOBAL-TOOL-CREDENTIALS'
    assert 'OAuth' in state['current_work']['blocker']
    assert sum((ROOT / n).stat().st_size for n in ('AGENTS.md', 'STATE.yaml')) <= 8192
    print(json.dumps({'status': 'PASS', 'global_tool_consumers': 19, 'migrated_plaintext_keys': 3,
                      'credential_routes': 5, 'existing_secure_launchers_verified': 3,
                      'source_plaintext_absent': True, 'original_global_non_mcp_bytes_preserved': True,
                      'strict_preservation_of_undrifted_files': 'PASS', 'secret_output': False,
                      'codex_config': 'EXTERNAL_DRIFT_PRESERVED',
                      'codex_exact_semantic_preservation': 'UNPROVEN', 'codex_bounded_inventory': 'PASS',
                      'claude_figma_user_scope': 'Connected', 'claude_github_auth': 'invalid_401',
                      'claude_stitch_auth': 'UNVERIFIED_initialize200_without_auth',
                      'existing_session_adoption': 'UNVERIFIED', 'figma_plugin_oauth': 'reauthentication_required',
                      'overall_goal_complete': False}))


if __name__ == '__main__':
    main()
