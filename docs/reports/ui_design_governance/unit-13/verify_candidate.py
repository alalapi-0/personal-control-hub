"""Read-only integrity checks for the TC13 design deliverable."""
import hashlib
import json
import struct
from pathlib import Path

UNIT = Path(__file__).resolve().parent
REPO = UNIT.parents[3]


def read(name):
    return json.loads((UNIT / name).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_semantics(state):
    state = json.loads(json.dumps(state))
    state.pop('last_updated', None)
    for key in ('status', 'next_action'):
        state['current_work'].pop(key, None)
    return state


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def verify():
    scene, figma, exports = read('scene-source.json'), read('figma.json'), read('exports.json')['exports']
    ops = [op for section in scene['sections'] for op in section['ops']]
    assert len({op['key'] for op in ops}) == len(ops)
    assert all(op['key'] in figma['nodes'] for op in ops), 'Missing authored node mapping'
    assert len(scene['frames']) == 36
    summaries = {}
    checks = 0
    for direction in 'ABC':
        data = read(f'readback/{direction}.json')
        assert data['mismatchCount'] == 0
        checks += data['expectedCount']
        expected = {f['key']: f for f in scene['frames'] if f['direction'] == direction}
        assert set(expected) == {s['key'] for s in data['summaries']}
        assert set(data['page']['childIds']) == {s['id'] for s in data['summaries']}
        for s in data['summaries']:
            f = expected[s['key']]
            assert (s['width'], s['height']) == (f['w'], f['h'])
            assert s['id'] == figma['nodes'][s['key']]
            assert s['horizontalErrorCount'] == s['verticalErrorCount'] == s['imageFills'] == 0
            assert s['textMinimum'] >= 12 and s['texts'] > 0 and s['instances'] > 0
            summaries[s['id']] = s
    foundations = read('readback/foundations.json')
    assert foundations['export_context_unchanged']
    assert len(foundations['variables']) == 41 and len(foundations['textStyles']) == 7
    summaries.update({s['id']: s for s in foundations['summaries']})
    assert read('readback/hash-vectors.json')['pass']
    assert read('readback/routes.json') == {'checked': 133, 'errors': []}
    for check in read('readback/export-consistency.json')['after_comparison']:
        assert not check['content_changes'] and not check['extra_top_level_nodes'] and not check['mismatches']
    assert len(exports) == 58 and len({e['key'] for e in exports}) == 58
    for e in exports:
        path = REPO / e['path']
        raw = path.read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', raw[16:24]) == (round(e['w']), round(e['h']))
        assert digest(path) == e['png_sha256']
        if e['kind'] in ('viewport', 'foundation'):
            assert summaries[e['id']]['sha256'] == e['sha256']
        else:
            assert e['id'] in figma['nodes'].values()
            assert e['temporaryCreated'] == e['temporaryRemoved']
    assert not read('readback/png-validation.json')['dimension_and_canvas_errors']
    palette = figma['palette']
    def lum(hex_color):
        values = [v / 255 for v in bytes.fromhex(hex_color.lstrip('#'))]
        values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(v * w for v, w in zip(values, (0.2126, 0.7152, 0.0722)))
    contrasts = {}
    for direction, colors in palette.items():
        for fg in ('text', 'muted', 'accent', 'warning', 'success'):
            for bg in ('canvas', 'surface'):
                a, b = sorted((lum(colors[fg]), lum(colors[bg])))
                ratio = (b + .05) / (a + .05)
                assert ratio >= 4.5
                contrasts[f'{direction}/{fg}/{bg}'] = round(ratio, 4)
        a, b = sorted((lum(colors['onaccent']), lum(colors['accent'])))
        ratio = (b + .05) / (a + .05)
        assert ratio >= 4.5
        contrasts[f'{direction}/onaccent/accent'] = round(ratio, 4)
    baseline = json.loads((UNIT.parent / 'unit-12/baseline.json').read_text())
    for path, expected in baseline['protected_files'].items():
        assert digest(REPO / path) == expected, f'Protected file changed: {path}'
    assert digest(Path.home() / '.codex/config.toml') == baseline['accepted_tc11_config_sha256']
    candidate_path = UNIT / 'candidate-v1.json'
    if candidate_path.exists():
        import yaml
        candidate = read('candidate-v1.json')
        identity = candidate['identity']
        assert hashlib.sha256(canonical(identity)).hexdigest() == candidate['candidate_sha256']
        for path, expected in identity['files'].items():
            assert digest(REPO / path) == expected, f'Candidate file changed: {path}'
        state = yaml.safe_load((REPO / 'STATE.yaml').read_text())
        assert state_semantics(state) == identity['state_semantics']
    return {'frames': 36, 'authored_ops': len(ops), 'source_nodes_checked': checks,
            'exports': len(exports), 'prototype_links': 133, 'contrast_pairs': len(contrasts),
            'minimum_text_contrast': min(contrasts.values()), 'protected_files': 16,
            'candidate_manifest_checked': candidate_path.exists()}


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
