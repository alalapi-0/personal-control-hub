"""Verify the exact Figma enable Boolean and preservation; no Figma calls."""
from pathlib import Path
import hashlib
import json
import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).parent

def verify():
    baseline = json.loads((UNIT / 'baseline.json').read_text())
    result = json.loads((UNIT / 'validation.json').read_text())
    path = Path(baseline['source']); current = path.read_bytes()
    assert hashlib.sha256(current).hexdigest() == result['after_sha256']
    config = tomllib.loads(current.decode())
    assert config['apps'][baseline['target_app']] == {'enabled': True}
    offset = baseline['changed_token_byte_offset']
    assert current[offset:offset+4] == b'true'
    restored = current[:offset] + b'false' + current[offset+4:]
    assert hashlib.sha256(restored).hexdigest() == baseline['before_sha256']
    old = tomllib.loads(restored.decode())
    config['apps'][baseline['target_app']]['enabled'] = False
    assert config == old
    assert oct(path.stat().st_mode & 0o777) == baseline['mode'] == '0o600'
    assert not path.with_name('config.toml.tc11.tmp').exists()
    tc10 = json.loads((UNIT / baseline['protected_reference']).read_text())
    candidate = json.loads((UNIT / baseline['accepted_tc10_candidate']).read_text())
    protected = {**tc10['protected_hub_hashes'], **candidate['identity']['files'],
                 **baseline['other_global_hashes']}
    for name, expected in protected.items():
        p = Path(name) if Path(name).is_absolute() else ROOT / name
        assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, name
    state = yaml.safe_load((ROOT / 'STATE.yaml').read_text())
    before = yaml.safe_load(baseline['state_preimage'])
    assert {k:v for k,v in state.items() if k not in ('last_updated','current_work')} == {
        k:v for k,v in before.items() if k not in ('last_updated','current_work')}
    assert state['current_work']['unit'] == 'HUB-11-FIGMA-LOCAL-ENABLE'
    assert '未验证' in state['current_work']['blocker']
    assert sum((ROOT / n).stat().st_size for n in ('AGENTS.md','STATE.yaml')) <= 8192
    assert baseline['prechange_runtime']['figma_tools_available'] == 0
    assert result['remote_figma_calls'] == 0 and not result['clients_restarted']
    print(json.dumps({'status':'PASS','only_boolean_changed':True,'protected_paths':len(protected),
                      'keychain_and_credentials_unchanged':True,'remote_calls':0,
                      'fresh_runtime':'UNVERIFIED','oauth':'UNVERIFIED','overall_goal_complete':False}))

if __name__ == '__main__':
    verify()
