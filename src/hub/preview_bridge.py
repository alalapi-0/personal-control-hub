"""Default-off, fixture-only hint capability. This never authorizes execution."""
from __future__ import annotations

import copy
import secrets
import threading
import time
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from .service_contract import ServiceError
from .workbench_store import digest, exact, number
from .design_records import HASH_RE, ID_RE

HINT_FIELDS = {'bridge_id', 'bridge_version', 'manifest_hash', 'binding_hash',
               'load_id', 'element_id', 'role', 'rect', 'source_mapping'}


def validate_hint(hint):
    exact(hint, HINT_FIELDS)
    for key in ('bridge_id', 'load_id', 'element_id'):
        if not isinstance(hint[key], str) or not ID_RE.fullmatch(hint[key]):
            raise ValueError('invalid hint identity')
    if type(hint['bridge_version']) is not int or hint['bridge_version'] != 1:
        raise ValueError('invalid bridge version')
    for key in ('manifest_hash', 'binding_hash'):
        if not isinstance(hint[key], str) or not HASH_RE.fullmatch(hint[key]):
            raise ValueError('invalid hint digest')
    if hint['role'] not in {'button', 'article', 'heading'} or hint['source_mapping'] != 'unknown':
        raise ValueError('unsupported semantic hint')
    exact(hint['rect'], {'x1', 'y1', 'x2', 'y2'})
    for v in hint['rect'].values(): number(v, 0, 1)
    r = hint['rect']
    if r['x1'] >= r['x2'] or r['y1'] >= r['y2']:
        raise ValueError('empty hint geometry')


class FixtureBridge:
    """Trusted construction only; no request can supply these fixed descriptors."""
    def __init__(self, previews, origin, *, clock=time.monotonic, ttl=300):
        if previews.store.classification != 'synthetic_fixture' or previews.designs.store.fixture is not True:
            raise ServiceError('BRIDGE_FIXTURE_REQUIRED')
        parts = urlsplit(origin)
        if (parts.scheme != 'http' or parts.hostname != 'localhost' or not parts.port
                or parts.netloc != 'localhost:' + str(parts.port) or parts.path or parts.query or parts.fragment):
            raise ServiceError('BRIDGE_ORIGIN_REJECTED')
        catalog = previews.catalog()['previews']
        if len(catalog) != 1: raise ServiceError('BRIDGE_FIXTURE_REQUIRED')
        self.previews, self.origin, self.clock, self.ttl = previews, origin, clock, ttl
        self.binding = copy.deepcopy(catalog[0]['binding'])
        self.script = Path(__file__).with_name('web') / 'fixture_bridge.js'
        self.script_hash = hashlib.sha256(self.script.read_bytes()).hexdigest()
        self.elements = [{'id': 'sample-card', 'role': 'button'}, {'id': 'sample-heading', 'role': 'heading'}]
        self.manifest_hash = digest({'schema': 1, 'binding': self.binding, 'elements': self.elements,
                                     'source_mapping': 'unknown', 'resource': '/fixture', 'script_hash': self.script_hash})
        self.lock = threading.Lock()
        self.loads = {}

    def issue(self, context):
        self._check_script()
        self.previews.resolve(self.binding['preview_id'], binding=self.binding, image=True)
        now = self.clock()
        with self.lock:
            self.loads = {k: v for k, v in self.loads.items() if v['expires'] > now}
            # One live load per authenticated Hub session; bounded by session capacity.
            if context not in self.loads and len(self.loads) >= 32:
                raise ServiceError('BRIDGE_CAPACITY', status=503)
            load_id = 'load-' + secrets.token_hex(16)
            self.loads[context] = {'load_id': load_id, 'expires': now + self.ttl}
        return {'available': True, 'classification': 'synthetic_fixture', 'bridge_id': 'owned-fixture',
                'bridge_version': 1, 'manifest_hash': self.manifest_hash,
                'binding_hash': digest(self.binding), 'origin': self.origin,
                'frame_url': self.origin + '/fixture', 'nonce': secrets.token_hex(32),
                'load_id': load_id, 'ttl_seconds': self.ttl, 'elements': copy.deepcopy(self.elements)}

    def validate(self, hint, binding, *, context=None, historical=False):
        self._check_script()
        try: validate_hint(hint)
        except (ValueError, TypeError): raise ServiceError('BRIDGE_HINT_REJECTED') from None
        if (binding != self.binding or hint['binding_hash'] != digest(binding)
                or hint['bridge_id'] != 'owned-fixture' or hint['manifest_hash'] != self.manifest_hash
                or {'id': hint['element_id'], 'role': hint['role']} not in self.elements):
            raise ServiceError('BRIDGE_VERSION_STALE', status=409)
        self.previews.resolve(binding['preview_id'], binding=binding, image=True)
        if not historical:
            with self.lock: load = self.loads.get(context)
            if not load or load['load_id'] != hint['load_id'] or load['expires'] <= self.clock():
                raise ServiceError('BRIDGE_LOAD_STALE', status=409)

    def _check_script(self):
        if hashlib.sha256(self.script.read_bytes()).hexdigest() != self.script_hash:
            raise ServiceError('BRIDGE_VERSION_STALE', status=409)
