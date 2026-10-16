"""Explicitly started executor for exact, server-owned synthetic read grants.

The existing TaskStore remains the only queue and claim authority. Observing this
object never opens a provider, registers storage, or adds execution authority.
"""
from __future__ import annotations

import copy
import threading

from .codex_adapter import AppServerAdapter
from .ordinary_tasks import KIND, check_registration, validate_grant
from .service_contract import ServiceError
from .task_store import parent_binding_matches
from .task_worker import TaskWorker


class OrdinaryRuntime:
    def __init__(self, store, *, grants, provider_status, adapter_factory=AppServerAdapter,
                 poll_interval=.1, request_timeout=30):
        if (type(grants) is not list or not grants or any(type(g) is not dict for g in grants)
                or type(provider_status) is not dict
                or type(provider_status.get('available')) is not bool
                or provider_status.get('classification') not in {'synthetic_fixture', 'real', 'unknown'}
                or type(poll_interval) not in (int, float) or not 0 < poll_interval <= 10
                or type(request_timeout) is not int or not 1 <= request_timeout <= 30):
            raise ServiceError('ORDINARY_RUNTIME_CONFIG_REJECTED')
        self.store = store
        self._grants = copy.deepcopy(grants)
        if (len({g.get('grant_id') for g in grants}) != len(grants)
                or any(g.get('kind') != KIND or g.get('effect') != 'read_only'
                       or g.get('classification') != 'synthetic_fixture' for g in grants)):
            raise ServiceError('ORDINARY_RUNTIME_SCOPE_REJECTED', status=403)
        self._provider = copy.deepcopy(provider_status)
        self._adapter_factory = adapter_factory
        self._poll_interval = poll_interval
        self._request_timeout = request_timeout
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._worker = None
        self._task_id = None
        self._state = 'stopped'
        self._error = None

    def _validate(self, grant):
        # Compare against the explicit list before touching a supplied root.
        if not any(grant == owned for owned in self._grants):
            raise ServiceError('ORDINARY_RUNTIME_SCOPE_REJECTED', status=403)
        if self.store.grant(grant['grant_id']) != grant:
            raise ServiceError('GRANT_VERSION_STALE', status=409)
        check_registration(grant, self.store.root)
        validate_grant(grant, live=True)

    def writer_check(self, grant):
        with self._lock:
            if self._state != 'running' or self._stop.is_set():
                return False
        try:
            self.store.check_storage()
            self._validate(grant)
            return True
        except (ServiceError, OSError, ValueError, TypeError, KeyError):
            return False

    @staticmethod
    def _capability(available, classification, reason=None, status=None):
        return {'available': available, 'status': status or ('available' if available else 'unavailable'),
                'reason_code': reason, 'classification': classification}

    def status(self):
        with self._lock:
            ready = self._state == 'running' and self._thread is not None and self._thread.is_alive()
            reason = None if ready else self._error or 'TASK_WORKER_UNCONFIGURED'
            return {'worker_runtime': self._capability(ready, 'synthetic_fixture', reason, self._state),
                    'provider': self._capability(self._provider['available'], self._provider['classification'],
                                                None if self._provider['available'] else 'TASK_PROVIDER_UNAVAILABLE')}

    def _failed(self, error):
        with self._lock:
            self._state = 'failed'
            self._error = error.code if isinstance(error, ServiceError) else 'ORDINARY_RUNTIME_FAILED'
            self._stop.set()

    def start(self):
        with self._lock:
            if self._state == 'running':
                return self.status()
            # Explicit stop/failure is terminal for this exact runtime object.
            if self._thread is not None or self._state == 'failed':
                raise ServiceError('ORDINARY_RUNTIME_RESTART_REJECTED', status=409)
            try:
                if self._provider['available'] is not True:
                    raise ServiceError('TASK_PROVIDER_UNAVAILABLE', status=409)
                self.store.check_storage()
                for grant in self._grants:
                    self._validate(grant)
            except Exception as error:
                self._failed(error)
                raise
            self._state = 'running'
            self._thread = threading.Thread(target=self._run, name='hub-ordinary-runtime', daemon=False)
            self._thread.start()
            return self.status()

    def _run(self):
        try:
            while not self._stop.is_set():
                self.store.check_storage()
                for task in sorted(self.store.tasks(), key=lambda row: row['sequence']):
                    if self._stop.is_set():
                        break
                    grant = task['intent'].get('grant')
                    if (task['status'] != 'queued' or task['payload'].get('kind') != KIND
                            or not any(grant == owned for owned in self._grants)):
                        continue
                    if not self.writer_check(grant):
                        continue
                    parent = self.store.task(task['parent']) if task['parent'] else None
                    if task['parent'] or task['payload']['mode'] == 'busy_feedback':
                        if (not parent_binding_matches(task['intent'], parent)
                                or parent['status'] != 'checks_complete' or parent['provider_status'] != 'completed'
                                or parent['cancel']):
                            continue
                    with self._lock:
                        if self._stop.is_set():
                            break
                        self._worker = TaskWorker(self.store, adapter_factory=self._adapter_factory,
                            writer_check=self.writer_check, request_timeout=self._request_timeout, only_task_id=task['id'])
                        self._task_id = task['id']
                        worker = self._worker
                    worker.run_one()
                    with self._lock:
                        self._worker = None
                        self._task_id = None
                self._stop.wait(self._poll_interval)
        except Exception as error:
            self._failed(error)
        finally:
            with self._lock:
                if self._state != 'failed':
                    self._state = 'stopped'

    def stop(self, timeout=5):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise ServiceError('ORDINARY_RUNTIME_CONFIG_REJECTED')
        with self._lock:
            self._stop.set()
            if self._state != 'failed':
                self._state = 'stopping' if self._thread else 'stopped'
            worker, ident, thread = self._worker, self._task_id, self._thread
        if worker is not None and ident is not None:
            try:
                task = self.store.task(ident)
                if task and task['owner'] == worker.owner and task['status'] in {
                        'running', 'validating', 'waiting_input', 'waiting_approval'}:
                    self.store.cancel(ident, worker_owner=worker.owner)
            except Exception as error:
                self._failed(error)
                raise
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                error = ServiceError('ORDINARY_RUNTIME_STOP_TIMEOUT', status=409)
                self._failed(error)
                raise error
        return self.status()
