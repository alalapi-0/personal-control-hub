"""Single-owner proof through a named environment reference, never a token file."""
from __future__ import annotations

from collections import deque
import hashlib
import hmac
import os
import threading
import time

from .service_contract import ServiceError


class OwnerAuth:
    reference = 'HUB_OWNER_TOKEN'

    def __init__(self, *, provider=None, clock=time.monotonic):
        # An injected provider belongs only to trusted server code/isolated tests.
        self.provider = provider or (lambda: os.environ.get(self.reference))
        self.clock = clock
        self.attempts = deque(maxlen=8)
        self.lock = threading.Lock()

    def _credential(self):
        try:
            value = self.provider()
            if not isinstance(value, str) or not 32 <= len(value) <= 512:
                return None
            return value.encode('utf-8')
        except Exception:
            return None

    @property
    def configured(self):
        return self._credential() is not None

    @property
    def version(self):
        secret = self._credential()
        return hashlib.sha256(secret).digest() if secret is not None else None

    def verify(self, proof):
        secret = self._credential()
        if secret is None:
            raise ServiceError('OWNER_AUTH_UNAVAILABLE', status=503)
        with self.lock:
            now = self.clock()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 8:
                raise ServiceError('OWNER_AUTH_RATE_LIMITED', status=429)
            self.attempts.append(now)
        try:
            encoded = proof.encode('utf-8') if isinstance(proof, str) and 32 <= len(proof) <= 512 else None
        except UnicodeError:
            encoded = None
        if encoded is None or not hmac.compare_digest(hashlib.sha256(secret).digest(), hashlib.sha256(encoded).digest()):
            raise ServiceError('OWNER_AUTH_FAILED', status=401)
        return hashlib.sha256(secret).digest()
