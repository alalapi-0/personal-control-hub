"""Fail-closed RR gateway assertions; never an Owner/session authenticator."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid


class GatewayDenied(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate')
        value[key] = item
    return value


class GatewayAuth:
    """The signer must use its verified mTLS peer, never forwarded identity.

    A distinct gateway OS account is mandatory outside explicitly constructed
    fixtures. Its only PCH capability is the protected Unix socket; database and
    Agent CLI access belong to agent_uid. No fixture switch is accepted by CLI.
    """
    def __init__(self, key, bindings, project_id, origin, gateway_uid, agent_uid, *, fixture=False, clock=time.time):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('gateway credential unavailable')
        if type(gateway_uid) is not int or type(agent_uid) is not int or min(gateway_uid, agent_uid) < 0:
            raise ValueError('gateway identity unavailable')
        if not fixture and (gateway_uid == agent_uid or os.geteuid() != agent_uid):
            raise ValueError('gateway and Agent identities must be separate')
        if not bindings or any(not re.fullmatch('[0-9a-f]{64}', k) or v != project_id for k, v in bindings.items()):
            raise ValueError('device project bindings unavailable')
        if not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?', origin):
            raise ValueError('fixed HTTPS origin required')
        self._key = key
        self.bindings = dict(bindings)
        self.project_id, self.origin = project_id, origin
        self.gateway_uid, self.agent_uid, self.fixture = gateway_uid, agent_uid, fixture
        self.challenge = secrets.token_hex(32)
        self.clock = clock
        self._seen, self._lock = {}, threading.Lock()

    def check_binding(self, peer_uid, device_id):
        if peer_uid != self.gateway_uid or self.bindings.get(device_id) != self.project_id:
            raise GatewayDenied()

    def verify(self, peer_uid, method, path, body, encoded_assertion):
        try:
            if len(encoded_assertion) > 4096 or not re.fullmatch('[A-Za-z0-9_-]+', encoded_assertion):
                raise ValueError()
            raw = base64.urlsafe_b64decode(encoded_assertion + '=' * (-len(encoded_assertion) % 4))
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_float=lambda _: (_ for _ in ()).throw(ValueError()))
            keys = {'v','challenge','device_sha256','project_id','origin','method','path','body_sha256','nonce','issued_at','expires','mac'}
            if set(value) != keys or canonical(value) != raw:
                raise ValueError()
            mac = value.pop('mac')
            if not isinstance(mac, str) or not hmac.compare_digest(hmac.new(self._key, canonical(value), hashlib.sha256).hexdigest(), mac):
                raise ValueError()
            now = self.clock()
            if (type(value['v']) is not int or value['v'] != 1 or type(value['expires']) is not int or type(value['issued_at']) is not int
                    or not now - 5 <= value['issued_at'] <= now + 5
                    or not now <= value['expires'] <= value['issued_at'] + 60):
                raise ValueError()
            if (value['challenge'], value['project_id'], value['origin'], value['method'], value['path'], value['body_sha256']) != (self.challenge, self.project_id, self.origin, method, path, hashlib.sha256(body).hexdigest()):
                raise ValueError()
            if str(uuid.UUID(value['nonce'])) != value['nonce']:
                raise ValueError()
            self.check_binding(peer_uid, value['device_sha256'])
            with self._lock:
                self._seen = {k:v for k,v in self._seen.items() if v >= now}
                if value['nonce'] in self._seen or len(self._seen) >= 4096:
                    raise ValueError()
                self._seen[value['nonce']] = value['expires']
            return value['device_sha256']
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, GatewayDenied):
            raise GatewayDenied() from None
