"""Explicitly started executor for exact, server-owned read-only grants.

The existing TaskStore remains the only queue and claim authority. Observing this
object never opens a provider, registers storage, or adds execution authority.
"""
from __future__ import annotations

import copy
import threading
import time

from .codex_adapter import AppServerAdapter
from .ordinary_tasks import KIND, check_registration, validate_grant
from .service_contract import ServiceError
from .task_store import parent_binding_matches
from .task_worker import TaskWorker


class OrdinaryRuntime:
    def __init__(self, store, *, grants, provider_status, adapter_factory=AppServerAdapter,
                 poll_interval=.1, request_timeout=30, grant_classification='synthetic_fixture', normal_budget_bindings=None):
        if (type(grants) is not list or not grants or any(type(g) is not dict for g in grants)
                or type(provider_status) is not dict
                or type(provider_status.get('available')) is not bool
                or provider_status.get('classification') not in {'synthetic_fixture', 'real', 'unknown'}
                or type(grant_classification) is not str
                or grant_classification not in {'synthetic_fixture', 'real'}
                or (grant_classification == 'real' and provider_status.get('classification') != 'real')
                or type(poll_interval) not in (int, float) or not 0 < poll_interval <= 10
                or type(request_timeout) is not int or not 1 <= request_timeout <= 30):
            raise ServiceError('ORDINARY_RUNTIME_CONFIG_REJECTED')
        from .dispatch_budget import normal_bindings
        self._normal_budgets = normal_bindings(normal_budget_bindings)
        self.store = store
        self._grants = copy.deepcopy(grants)
        if (len({g.get('grant_id') for g in grants}) != len(grants)
                or any(g.get('kind') != KIND or g.get('effect') != 'read_only'
                       or g.get('classification') != grant_classification for g in grants)):
            raise ServiceError('ORDINARY_RUNTIME_SCOPE_REJECTED', status=403)
        self._grant_ids = tuple(g['grant_id'] for g in self._grants)
        self._classification = grant_classification
        # The store independently revalidates every participant and enforces
        # the real two-task bound inside its atomic claim transaction.
        self._capacity = 2
        self._provider = copy.deepcopy(provider_status)
        self._adapter_factory = adapter_factory
        self._poll_interval = poll_interval
        self._request_timeout = request_timeout
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._handles = {}
        self._state = 'stopped'
        self._error = None

    @property
    def normal_budget_bindings(self):
        """Detached read-only map; retain exact internal typed capabilities."""
        from types import MappingProxyType
        with self._lock:
            return MappingProxyType(dict(self._normal_budgets))

    def bind_dispatch_budgets(self,bindings):
        from .dispatch_budget import normal_bindings
        value=normal_bindings(bindings)
        with self._lock:
            if (self._thread is not None or self._handles or self._normal_budgets and
                    (self._normal_budgets.keys()!=value.keys()or any(self._normal_budgets[k]is not value[k]for k in value))):
                raise ServiceError('NORMAL_DISPATCH_BINDING_REJECTED',status=409)
            self._normal_budgets=value

    @property
    def grant_ids(self):
        """Immutable server-selected scope; observing it never starts work."""
        return self._grant_ids

    @property
    def grant_projects(self):
        return tuple((grant['grant_id'],grant['project_id'])for grant in self._grants)

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
            return {'worker_runtime': self._capability(ready, self._classification, reason, self._state),
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

    def _cancel_owned(self):
        # A failed cancellation must not prevent cleanup of the other handle.
        with self._lock:
            handles = list(self._handles.items())
        failure = None
        for ident, handle in handles:
            try:
                task = self.store.task(ident)
                if task and task['owner'] == handle['worker'].owner and task['status'] in {
                        'running', 'validating', 'waiting_input', 'waiting_approval'}:
                    self.store.cancel(ident, worker_owner=handle['worker'].owner)
            except Exception as error:
                failure = failure or error
        return failure

    def _execute(self, handle):
        try:
            handle['claimed'] = handle['worker'].run_one()
        except Exception as error:
            self._failed(error)

    def _observe_handles(self):
        rejected = set()
        with self._lock:
            handles = list(self._handles.items())
        for ident, handle in handles:
            if not handle['thread'].is_alive():
                handle['thread'].join()
                if handle.get('claimed') is False:
                    rejected.add(ident)
                with self._lock:
                    self._handles.pop(ident, None)
        return rejected

    def _run(self):
        rejected = set()
        try:
            while not self._stop.is_set():
                self.store.check_storage()
                rejected.update(self._observe_handles())
                launched = False
                for task in sorted(self.store.tasks(selected_grant_ids=self._grant_ids), key=lambda row: row['sequence']):
                    with self._lock:
                        if self._stop.is_set() or len(self._handles) >= self._capacity:
                            break
                        if task['id'] in self._handles or task['id'] in rejected:
                            continue
                    grant = task['intent'].get('grant')
                    if (task['status'] != 'queued' or task['payload'].get('kind') != KIND
                            or not any(grant == owned for owned in self._grants)):
                        continue
                    if not self.writer_check(grant):
                        continue
                    if task['intent'].get('normal_dispatch_budget')or task['id']in self._normal_budgets:
                        try:
                            observed=self.store.normal_dispatch_control(task['id'],binding=self._normal_budgets.get(task['id']))
                            if not observed['progress_allowed']:continue
                        except ServiceError:
                            continue # Only this selected task is blocked; no whole-runtime stop.

                    parent = self.store.task(task['parent'], selected_grant_ids=self._grant_ids) if task['parent'] else None
                    if task['parent'] or task['payload']['mode'] == 'busy_feedback':
                        if (not parent_binding_matches(task['intent'], parent)
                                or parent['status'] != 'checks_complete' or parent['provider_status'] != 'completed'
                                or parent['cancel']):
                            continue
                    with self._lock:
                        if self._stop.is_set():
                            break
                        worker = TaskWorker(self.store, adapter_factory=self._adapter_factory,
                            writer_check=self.writer_check, request_timeout=self._request_timeout, only_task_id=task['id'],
                            selected_grant_ids=self._grant_ids, normal_budget_bindings=self._normal_budgets)
                        handle = {'worker': worker}
                        handle['thread'] = threading.Thread(target=self._execute, args=(handle,),
                            name='hub-ordinary-task-' + worker.owner, daemon=False)
                        self._handles[task['id']] = handle
                        try:
                            handle['thread'].start()
                            launched = True
                        except Exception:
                            self._handles.pop(task['id'])
                            raise
                if not launched:
                    rejected.clear()
                # Blocked claims are retried only at the configured observation
                # interval, yielding a rejected slot to other queued tasks on
                # the next pass. TaskStore alone decides whether claims succeed.
                self._stop.wait(self._poll_interval)
        except Exception as error:
            self._failed(error)
        finally:
            if self._stop.is_set():
                error = self._cancel_owned()
                if error is not None:
                    self._failed(error)
                deadline = time.monotonic() + 5
                with self._lock:
                    handles = list(self._handles.items())
                for ident, handle in handles:
                    handle['thread'].join(max(0, deadline - time.monotonic()))
                    if not handle['thread'].is_alive():
                        with self._lock:
                            self._handles.pop(ident, None)
                with self._lock:
                    if any(h['thread'].is_alive() for h in self._handles.values()):
                        self._failed(ServiceError('ORDINARY_RUNTIME_STOP_TIMEOUT', status=409))
            with self._lock:
                if self._state != 'failed':
                    self._state = 'stopping' if any(h['thread'].is_alive() for h in self._handles.values()) else 'stopped'

    def stop(self, timeout=5):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise ServiceError('ORDINARY_RUNTIME_CONFIG_REJECTED')
        deadline = time.monotonic() + timeout
        with self._lock:
            self._stop.set()
            if self._state != 'failed':
                self._state = 'stopping' if self._thread else 'stopped'
            coordinator = self._thread
        failure = self._cancel_owned()
        # Join the coordinator first: it can no longer add a handle after stop.
        if coordinator is not None:
            coordinator.join(max(0, deadline - time.monotonic()))
        with self._lock:
            handles = list(self._handles.items())
        for ident, handle in handles:
            handle['thread'].join(max(0, deadline - time.monotonic()))
            if not handle['thread'].is_alive():
                with self._lock:
                    self._handles.pop(ident, None)
        with self._lock:
            alive = (coordinator is not None and coordinator.is_alive()) or any(
                h['thread'].is_alive() for h in self._handles.values())
            if alive:
                failure = failure or ServiceError('ORDINARY_RUNTIME_STOP_TIMEOUT', status=409)
            if failure is None and self._state != 'failed':
                self._state = 'stopped'
        if failure is not None:
            self._failed(failure)
            raise failure
        return self.status()
