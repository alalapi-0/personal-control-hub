"""Read-only TC14 color, structure, export and scope checks."""
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
def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
def semantics(state):
    state = json.loads(json.dumps(state)); state.pop('last_updated', None)
    for key in ('status', 'next_action'):
        state['current_work'].pop(key, None)
    return state
def luminance(color):
    values = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(v * w for v, w in zip(values, (.2126, .7152, .0722)))
def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)
def verify():
    palettes = read('palettes.json'); mapping = read('figma.json'); rb = read('readback.json')
    assert palettes['layout_choice'] == 'C' and palettes['palette_choice'] == 'PENDING'
    assert len(palettes['palettes']) == 5
    colors = {p['id']: p['roles'] for p in palettes['palettes']}
    assert len(mapping['frames']) == len(rb['summaries']) == 20
    embedded_frames = (UNIT / 'readback.js').read_text().split('const M=', 1)[1].split(',P=', 1)[0]
    assert json.loads(embedded_frames) == mapping['frames'], 'Readback script embeds stale frame identities'
    assert set(rb['pageChildren']) == {f['id'] for f in mapping['frames']}
    assert not rb['errors'] and not rb['paletteVariableErrors'] and rb['paletteVariableCount'] == 100
    assert rb['protectedContextSha256'] == mapping['protectedContextSha256']
    assert read('source-before.json') == read('source-after.json')
    for page in read('source-before.json'):
        name = page['name'].split(' ')[0]
        file = 'foundations' if name == '00' else name
        old = json.loads((UNIT.parent / f'unit-13/readback/{file}.json').read_text())
        prior = {s['id']: s['sha256'] for s in old['summaries']}
        assert all(prior[r['id']] == r['sha256'] for r in page['roots'])
    observed = {s['id']: s for s in rb['summaries']}
    node_hashes = {}
    for f in mapping['frames']:
        s = observed[f['id']]
        assert s['structure_sha256'] == f['source_structure_sha256'] == f['structure_sha256']
        assert s['content_sha256'] == f['content_sha256'], f"Stale frame identity: {f['key']}"
        assert (s['width'], s['height']) == (f['width'], f['height'])
        assert (f['width'], f['height']) in ((1440, 900), (390, 844))
        assert s['texts'] > 0 and s['instances'] > 0
        node_hashes[s['id']] = s['content_sha256']
        if s['grid']:
            node_hashes[s['grid']['id']] = s['grid']['content_sha256']
    exports = read('exports.json')['exports']
    assert len(exports) == len({e['key'] for e in exports}) == 25
    for e in exports:
        raw = (REPO / e['path']).read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', raw[16:24]) == (round(e['w']), round(e['h']))
        assert hashlib.sha256(raw).hexdigest() == e['png']['sha256']
        assert e['content_sha256'] == node_hashes[e['id']]
        assert e['palette_roles'] == colors[e['key'].split('/')[0]]
        if e['kind'] == 'full-content':
            assert e['createdNodeIds'] == e['removedNodeIds']
    text_ratios, ui_ratios = [], []
    for r in colors.values():
        for fg in ('text', 'muted', 'accent', 'warning', 'success'):
            for bg in ('canvas', 'surface', 'tint'):
                value = contrast(r[fg], r[bg]); assert value >= 4.5
                text_ratios.append(value)
        value = contrast(r['onaccent'], r['accent']); assert value >= 4.5
        text_ratios.append(value)
        for fg in ('border', 'muted', 'accent'):
            for bg in ('canvas', 'surface'):
                value = contrast(r[fg], r[bg]); assert value >= 3
                ui_ratios.append(value)
    baseline = json.loads((UNIT.parent / 'unit-12/baseline.json').read_text())
    protected = dict(baseline['protected_files'])
    previous = json.loads((UNIT.parent / 'unit-13/candidate-v1.json').read_text())
    protected.update(previous['identity']['files'])
    for path, expected in protected.items():
        assert digest(REPO / path) == expected, f'Protected file changed: {path}'
    for path in UNIT.rglob('*.json'):
        json.loads(path.read_text())
    import yaml
    state = yaml.safe_load((REPO / 'STATE.yaml').read_text())
    assert state['current_work']['unit'] == 'HUB-14-C-PALETTES'
    assert sum((REPO / p).stat().st_size for p in ('STATE.yaml', 'AGENTS.md')) <= 8192
    candidate = UNIT / ('candidate-v2.json' if (UNIT / 'candidate-v2.json').exists() else 'candidate-v1.json')
    if candidate.exists():
        c = json.loads(candidate.read_text())
        assert hashlib.sha256(canonical(c['identity'])).hexdigest() == c['candidate_sha256']
        assert c['identity']['state_semantics'] == semantics(state)
        for path, expected in c['identity']['files'].items():
            assert digest(REPO / path) == expected, f'Candidate changed: {path}'
    return {'palettes': 5, 'frames': 20, 'exports': 25, 'color_bindings': sum(s['paints'] for s in rb['summaries']),
            'text_contrast_pairs': len(text_ratios), 'minimum_text_contrast': round(min(text_ratios), 4),
            'ui_contrast_pairs': len(ui_ratios), 'minimum_ui_contrast': round(min(ui_ratios), 4),
            'protected_files': len(protected), 'source_roots_preserved': 42,
            'candidate_manifest_checked': candidate.exists()}

if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
