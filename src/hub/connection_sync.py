"""Finite, registry-qualified source refreshes using the existing durable ledger."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import stat
import time
from zoneinfo import ZoneInfo

from hub.connection_records import (identifier, require, string, content_hash, fingerprint,
                                    empty_business, FIELDS, update_key, authority_matches)
from hub.connection_refresh import RequestConflictError, refresh
from hub.connection_sources import _safe_relative_read
from hub.connections import parse_source
from hub.service_contract import ServiceError

from hub.refresh_schedule import schedule_policy, latest_due_slot, retry_readiness


@contextmanager
def sync_mutex(ledger):
    """One nonblocking batch lock; SQLite CAS still fences other writers."""
    path=ledger.path.with_name('.sync.lock')
    descriptor=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid(),
                'unsafe sync mutex')
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ServiceError('SYNC_BATCH_BUSY',status=409,retryable=True) from None
        yield
    finally:os.close(descriptor)


def _version(result):
    # A metadata-only touch is not a changed source or business fact.
    value=deepcopy(result)
    value.pop('observed_at',None);value.pop('update_key',None)
    if 'binding_version' in value.get('authority', {}):
        authority = value['authority']
        value['authority'] = {k: authority[k] for k in ('schema_hash', 'adapter_version', 'binding_version')}
        value['authority']['binding_hash'] = authority['project_bindings'].get(value['project_id'])
    for source in value.get('sources',[]):source.pop('modified_at',None)
    for field in value.get('extensions',{}).get('fact',{}).values():
        if field.get('proof'):field['proof'].pop('observed_at',None)
    return value


def incremental_request(root, resolver, ledger, request_id, *, plan=None):
    """Bounded safe reads; append only changed/expired evidence, never on GET."""
    plan=plan if plan is not None else scope_plan(root,resolver)
    identifier(request_id,'request')
    if not plan['eligible_project_ids']:
        return {'request_id':request_id,'status':'NO_ELIGIBLE_PROJECTS','appended_count':0,
                'checked_count':0,'unchanged_count':0,
                'failure_code_counts':{'NO_ELIGIBLE_PROJECTS':1},'head':ledger.head},2
    history=ledger.history(current_authority=resolver.authority)
    existing=next((row for row in history['requests'] if row['request_id']==request_id),None)
    if existing:
        return sync_request(root,resolver,ledger,request_id,project_ids=existing['project_ids'],plan=plan)
    previous={row['project_id']:row['result'] for row in history['results']}
    scanned={};unchanged=[];errors=Counter()
    for ident in plan['eligible_project_ids']:
        result=resolver.refresh(ident)
        old=previous.get(ident)
        expired=False
        if old is not None and result['success']:
            observed=datetime.fromisoformat(result['observed_at'].replace('Z','+00:00'))
            prior=datetime.fromisoformat(old['observed_at'].replace('Z','+00:00'))
            age=(observed-prior).total_seconds()
            expired=age<0 or age>=old['freshness']['max_read_age_seconds']
        if old is None or _version(old)!=_version(result) or expired:
            scanned[ident]=result
        else:unchanged.append(ident)
        if not result['success']:
            errors.update(error['code'] for error in result['errors'])
    resolver._check_registry(plan['eligible_project_ids'])
    if not scanned:
        return {'request_id':request_id,'status':'UNCHANGED','appended_count':0,
                'checked_count':len(unchanged),'unchanged_count':len(unchanged),
                'failure_code_counts':dict(errors),'head':ledger.head},2 if errors else 0
    class ScannedResolver:
        authority=resolver.authority
        validate_result=staticmethod(resolver.validate_result)
        def refresh(self,ident):
            resolver._check_registry(ident)
            return deepcopy(scanned[ident])
    outcome=refresh(ledger,ScannedResolver(),request_id,list(scanned),expected_head=history['head'])
    return {'request_id':request_id,'status':outcome['request']['status'],
            'appended_count':len(outcome['appended_project_ids']),
            'checked_count':len(scanned)+len(unchanged),'unchanged_count':len(unchanged),
            'failure_code_counts':dict(errors),'head':outcome['projection']['head']},2 if errors else 0



def _source_version(result):
    return content_hash([{k: source[k] for k in ('id', 'path', 'sha256')}
                         for source in result['sources']]) if result['sources'] else None


def _loaded_method_version():
    # Fingerprint loaded code objects, including bounded reader, parser and validators.
    # Never hash mutable on-disk files and call that a hot reload.
    from types import CodeType, FunctionType
    from hub import connection_sources, connection_records, connections
    def stable(value):
        if isinstance(value, FunctionType): return stable(value.__code__)
        if type(value) is dict: return {k: stable(v) for k, v in sorted(value.items())}
        if isinstance(value, CodeType):
            return [value.co_code.hex(), list(value.co_names), [stable(c) for c in value.co_consts]]
        if isinstance(value, (tuple, frozenset)):
            items = [stable(c) for c in value]
            return sorted(items, key=repr) if isinstance(value, frozenset) else items
        if isinstance(value, bytes): return value.hex()
        return value if value is None or type(value) in (str, int, bool, float) else repr(value)
    functions = {}
    for module in (connection_sources, connection_records, connections):
        for name, value in vars(module).items():
            if isinstance(value, FunctionType): functions[module.__name__ + '.' + name] = [stable(value.__code__), stable(value.__defaults__), stable(value.__kwdefaults__)]
    for name, value in vars(connection_sources.SourceResolver).items():
        if isinstance(value, FunctionType): functions['SourceResolver.' + name] = [stable(value.__code__), stable(value.__defaults__), stable(value.__kwdefaults__)]
    functions['loaded_reader_limits'] = [connection_sources.MAX_SOURCE_BYTES, connection_sources.MAX_OWNED_HUB_STATE_BYTES, connection_sources.DECLARATION_PATH]
    return content_hash(functions)


def _method_version():
    return _loaded_method_version()


def _substantive_binding(resolver, ident):
    """Bounded root/declaration observation; no business state body is read.

    Permission authority retains full stat binding. Retry identity excludes stat-only
    declaration touches and includes actual declaration bytes plus stable root inode.
    """
    from hub.connection_sources import DECLARATION_PATH, _root_path_allowed
    require(getattr(resolver, 'observe_sources', True), 'retry observation requires an observing resolver')
    project = resolver.projects[ident]
    resolver._check_registry(ident)
    value = {'registration': {key: project.get(key) for key in ('id', 'root_path', 'enabled', 'connection_read_allowed', 'access_profile', 'local_presence', 'current_state_status', 'project_type', 'root_kind', 'current_state_paths', 'hub_source_ref', 'hub_field_mapping')},
             'root': None, 'declaration': None, 'routes': []}
    if (project.get('enabled') is not True or
            project.get('local_presence', {}).get('status') == 'removed_local' or
            project.get('current_state_status') == 'removed_local' or
            project.get('connection_read_allowed', project.get('enabled')) is not True or
            project.get('access_profile') == 'no_current_goal_access' or
            project.get('project_type') in {'cloud', 'cloud_project'} or project.get('root_kind') == 'cloud'):
        return content_hash(value)
    try:
        root = Path(project['root_path']).expanduser()
        _root_path_allowed(root); root = root.resolve(strict=True); _root_path_allowed(root)
        info = root.stat(); value['root'] = [str(root), info.st_dev, info.st_ino]
        data, _ = _safe_relative_read(root, DECLARATION_PATH,
                                    authority_check=lambda: resolver._check_registry(ident))
        import hashlib
        value['declaration'] = hashlib.sha256(data).hexdigest()
        for raw in project.get('current_state_paths', []):
            path = Path(raw).expanduser()
            if path.is_absolute():
                _root_path_allowed(path); resolved = path.resolve(strict=False); _root_path_allowed(resolved)
                value['routes'].append([raw, str(resolved)])
    except (OSError, ValueError, KeyError):
        # Missing registered input is an unavailable identity, never a fallback
        # path. SourceResolver records the precise per-project invalid result.
        pass
    return content_hash(value)


def _attempt_metadata(resolver, ids, *, role='automatic'):
    return {'version': '1.0', 'method_version': _method_version(), 'role': role,
            'project_inputs': {ident: _substantive_binding(resolver, ident) for ident in ids}}


def _observation(result, substantive, admission_ref=None, admission_kind=None, failure_ref=None):
    return {'version': '1.0', 'source_version': _source_version(result),
            'substantive_binding': substantive, 'admission_ref': admission_ref, 'admission_kind': admission_kind, 'failure_ref': failure_ref}


def record_recovery_observation(ledger, resolver, request_id, ident):
    """Explicit permitted observation, not automatic admission; fixture/runtime boundary unchanged."""
    require(ident in resolver.projects and resolver.projects[ident].get('enabled') is True, 'observation project not enabled')
    metadata = _attempt_metadata(resolver, [ident], role='observation')
    return refresh(ledger, resolver, request_id, [ident], attempt_metadata=metadata,
        observation_factory=lambda pid, result: _observation(result, metadata['project_inputs'][pid]))


def project_recovery(history, ident, instant=None):
    chain = []
    for stored in history['results']:
        if stored['project_id'] != ident: continue
        attempt = stored.get('attempt_metadata')
        result = stored['result']
        if result['success']:
            chain = []
            continue
        observation = stored.get('observation_metadata') or {}
        source = _source_version(result)
        errors = content_hash(result['errors'])
        method = attempt['method_version'] if attempt else None
        substantive = observation.get('substantive_binding')
        chain.append({'project_id': ident, 'failure_ref': stored['result_hash'],
            'failure_event_sequence': stored['event_sequence'],
            'observed_at': result['observed_at'], 'source_version': source,
            'binding_hash': result.get('binding_hash'), 'substantive_binding': substantive,
            'schema_hash': result['authority']['schema_hash'],
            'adapter_version': result['authority']['adapter_version'],
            'error_fingerprint': errors, 'error_codes': [e['code'] for e in result['errors']],
            'method_version': method, 'admission_ref': observation.get('admission_ref'),
            'admission_kind': observation.get('admission_kind'),
            'failure_signature': content_hash([source, substantive, result['authority']['schema_hash'],
                result['authority']['adapter_version'], errors, method])})
    return retry_readiness(chain, instant or datetime.now(timezone.utc))


def _retry_admission(history, resolver, ident, instant, event):
    state = project_recovery(history, ident, instant)
    if state['state'] == 'ready': return state, None
    # Deadline is independent of diagnosis/cap state. Unknown legacy method/input
    # cannot be silently inferred from this process or upgraded by a hash claim.
    if state['clock_regressed'] or instant < datetime.fromisoformat(state['deadline_at']):
        return state, None
    substantive = _substantive_binding(resolver, ident)
    method = _method_version()
    current = resolver.authority['project_bindings'].get(ident)
    evidence = None
    changed = (state['substantive_binding'] is not None and substantive != state['substantive_binding'])
    shared = (resolver.authority['schema_hash'] != state['schema_hash'] or
              resolver.authority['adapter_version'] != state['adapter_version'])
    if (changed or shared) and state['state'] != 'event_wait':
        evidence = {'kind': 'B', 'source': None, 'hash': content_hash([ident, state['failure_ref'], substantive, resolver.authority['schema_hash'], method])}
    if event is not None:
        require(type(event) is dict and set(event) == {'kind', 'failure_ref', 'source_version',
            'binding_hash', 'evidence_hash', 'proof_ref'}, 'invalid recovery event fields')
        require(event['kind'] in {'source_changed', 'transient', 'diagnosed'}, 'invalid recovery kind')
        for key in ('failure_ref', 'binding_hash', 'evidence_hash'): fingerprint(event[key], 'recovery ' + key)
        if event['source_version'] is not None: fingerprint(event['source_version'], 'recovery source')
        require(type(event['proof_ref']) is str, 'recovery proof reference required')
        proof = next((row for row in history.get('observation_results', []) if row['result_ref'] == event['proof_ref'] and row['project_id'] == ident), None)
        used = {row.get('observation_metadata', {}).get('admission_ref') for row in history['results'] if row.get('observation_metadata')}
        if (proof is not None and proof.get('attempt_metadata') and
                proof['attempt_metadata']['role'] == 'observation' and
                proof['attempt_metadata']['method_version'] == method and proof.get('observation_metadata') and
                proof['event_sequence'] > state['failure_event_sequence'] and
                event['failure_ref'] == state['failure_ref'] and event['binding_hash'] == current and
                event['evidence_hash'] == proof['result_hash'] and event['evidence_hash'] not in used and
                _source_version(proof['result']) == event['source_version'] and
                authority_matches(proof['result']['authority'], resolver.authority, [ident]) and
                proof['observation_metadata']['substantive_binding'] == substantive and
                datetime.fromisoformat(state['observed_at'].replace('Z', '+00:00')) <=
                    datetime.fromisoformat(proof['result']['observed_at'].replace('Z', '+00:00')) <= instant):
            source_changed = event['source_version'] is not None and event['source_version'] != state['source_version']
            method_changed = state['method_version'] is not None and method != state['method_version']
            kind = event['kind']
            if ((kind == 'source_changed' and source_changed and state['no_progress_count'] < 2) or
                (kind == 'diagnosed' and (source_changed or method_changed or changed)) or
                (kind == 'transient' and state['transient_retries'] == 0 and state['no_progress_count'] < 2 and
                 set(state['error_codes']) & {'offline', 'permission_denied'} and proof['success'])):
                evidence = {'kind': {'source_changed': 'S', 'diagnosed': 'D', 'transient': 'T'}[kind],
                            'source': event['source_version'], 'hash': event['evidence_hash']}
    if evidence is None: return state, None
    return state, {**evidence, 'method': method, 'substantive': substantive, 'failure_ref': state['failure_ref']}


def _automatic_snapshot(root, resolver, ledger, request_id, plan, instant, events, paused):
    history = ledger.history()
    existing = next((row for row in history['requests'] if row['request_id'] == request_id), None)
    deferred = {}; retries = {}; healthy = []
    for ident in plan['eligible_project_ids']:
        if ident in paused:
            deferred[ident] = {'state': 'paused', 'next_retry_at': None,
                               'recovery_condition': 'Explicit synchronization resume required.'}
            continue
        try:
            state, evidence = _retry_admission(history, resolver, ident, instant, events.get(ident))
        except ValueError:
            state, evidence = project_recovery(history, ident, instant), None
            state['event_rejection'] = 'invalid_recovery_event'
        if state['state'] == 'ready': healthy.append(ident)
        elif evidence is not None: retries[ident] = evidence
        else: deferred[ident] = state
    # Existing request selection/committed bytes are immutable, even after recovery.
    # Finished replay is ledger-only; a qualifying recovery gets a distinct ID.
    if existing and existing['status'] == 'FINISHED':
        failures = Counter(error['code'] for row in history['results']
            if row['request_id'] == request_id and not row['success'] for error in row['result']['errors'])
        payload = {'request_id': request_id, 'status': 'FINISHED', 'appended_count': 0,
                   'failure_code_counts': dict(failures), 'head': history['head']}
    elif existing or healthy:
        selected = existing['project_ids'] if existing else healthy
        allowed = set(selected) - set(paused)
        class AdmittedResolver:
            authority = resolver.authority
            validate_result = staticmethod(resolver.validate_result)
            def refresh(self, ident):
                if ident not in allowed: raise RuntimeError('automatic_project_paused')
                return resolver.refresh(ident)
        # Reuse original stored method metadata on crash recovery, never the
        # current implementation as a claim about already committed history.
        metadata = next((e['payload'].get('attempt_metadata') for e in history['events']
            if e['request_id'] == request_id and e['event_type'] == 'REQUEST_BEGUN'), None) if existing else _attempt_metadata(resolver, selected)
        if existing and metadata and metadata['method_version'] != _method_version():
            raise RequestConflictError('open automatic request method changed; preserve committed units and diagnose missing scope')
        outcome = refresh(ledger, AdmittedResolver(), request_id, selected,
            attempt_metadata=metadata, observation_factory=(lambda pid, result:
                _observation(result, metadata['project_inputs'][pid])) if metadata else None)
        failures = Counter(error['code'] for row in ledger.history(request_id)['results']
            if not row['success'] for error in row['result']['errors'])
        payload = {'request_id': request_id, 'status': outcome['request']['status'],
                   'appended_count': len(outcome['appended_project_ids']),
                   'failure_code_counts': dict(failures), 'head': ledger.head}
    else:
        payload = {'request_id': request_id, 'status': 'DEFERRED', 'appended_count': 0,
                   'failure_code_counts': {}, 'head': ledger.head}
    attempts = []
    for ident, evidence in retries.items():
        # Opaque ID is only deduplication, never a source of admission authority.
        retry_id = 'recovery.' + content_hash([ident, project_recovery(history, ident, instant)['failure_ref'], evidence])
        class RecoveryResolver:
            authority = resolver.authority
            validate_result = staticmethod(resolver.validate_result)
            def refresh(self, project_id):
                result = resolver.refresh(project_id)
                if evidence['kind'] != 'B' and _source_version(result) != evidence['source']:
                    result.pop('extensions', None)
                    message = 'Recovery event source version mismatches the actual bounded read.'
                    result.update(success=False, disposition='invalid', business=empty_business(),
                        field_provenance={}, unknown_fields={key: message for key in FIELDS},
                        errors=[{'code': 'invalid', 'message': message}])
                    result['freshness'].update(read_status='invalid', stale=True, reason=message)
                    result['update_key'] = update_key(result)
                return result
        metadata = {'version': '1.0', 'method_version': evidence['method'], 'role': 'automatic',
                    'project_inputs': {ident: evidence['substantive']}}
        outcome = refresh(ledger, RecoveryResolver(), retry_id, [ident], attempt_metadata=metadata,
            observation_factory=lambda pid, result: _observation(result, evidence['substantive'], evidence['hash'], evidence['kind'], evidence['failure_ref']))
        attempts.append(retry_id)
        payload['appended_count'] += len(outcome['appended_project_ids'])
        payload['head'] = ledger.head
        for stored in ledger.history(retry_id)['results']:
            if not stored['success']:
                for error in stored['result']['errors']:
                    payload['failure_code_counts'][error['code']] = payload['failure_code_counts'].get(error['code'], 0) + 1
    if attempts and payload['status'] == 'DEFERRED': payload['status'] = 'RECOVERY'
    current = ledger.history()
    payload.update(deferred_projects=deferred, recovery_request_ids=attempts,
                   scope_total=plan['scope_total'], scope_project_ids=list(plan['project_ids']),
                   recovery=recovery_summary(current, set(plan['eligible_project_ids']), instant=instant))
    latest = {row['project_id']: row['result'] for row in current['results']}
    failed = any(not latest[ident]['success'] for ident in plan['eligible_project_ids'] if ident in latest)
    return payload, 2 if deferred or failed or payload['failure_code_counts'] else 0

def scheduled_request(root,resolver,ledger,*,instant=None,plan=None,incremental_only=False,
                      recovery_events=None, paused_project_ids=()):
    """One finite latest-due snapshot, or an explicit manual incremental read."""
    instant=instant or datetime.now(ZoneInfo('Asia/Tokyo'))
    require(instant.tzinfo is not None,'scheduled instant needs timezone')
    plan=plan if plan is not None else scope_plan(root,resolver)
    require(recovery_events is None or type(recovery_events) is dict, 'recovery event map required')
    require(isinstance(paused_project_ids, (tuple, list, set, frozenset)), 'paused project ID collection required')
    require(set(paused_project_ids) <= set(plan['project_ids']), 'pause identities outside frozen scope')
    policy=plan['schedule']
    started=time.monotonic()
    with sync_mutex(ledger):
        if incremental_only:
            # Manual runs are distinct recoveries, never a generated timer policy.
            import uuid
            ident=f"{plan['scope_id']}.manual.{uuid.uuid4().hex}"
            payload,code=incremental_request(root,resolver,ledger,ident,plan=plan)
            slot=None
            mode='manual_incremental'
        else:
            slot=latest_due_slot(instant,policy['interval_hours'])
            ident=f"{plan['scope_id']}.snapshot.{policy['interval_hours']}h.{slot.strftime('%Y-%m-%dT%H-%M')}"
            payload,code=_automatic_snapshot(root,resolver,ledger,ident,plan,instant,
                recovery_events or {}, set(paused_project_ids))
            mode='snapshot'
        elapsed=time.monotonic()-started
        payload.update(mode=mode,scheduled_at=instant.isoformat(),elapsed_seconds=elapsed,
                       due_slot=slot.isoformat() if slot else None,
                       interval_hours=policy['interval_hours'],
                       overloaded=elapsed>policy['expected_interval_seconds'])
        return payload,code

SCOPE_PATH = "data/connections/sync_scope.yaml"


def scope_plan(root: Path, resolver) -> dict:
    """Qualify a frozen identity list without probing any registered project root."""
    data, meta = _safe_relative_read(Path(root), SCOPE_PATH, authority_check=lambda: resolver._check_registry(registration_only=True))
    scope = parse_source(data, "yaml")
    require(type(scope) is dict and set(scope) in ({
        "schema_version", "scope_id", "baseline_total", "project_ids", "excluded_project_ids"}, {
        "schema_version", "scope_id", "baseline_total", "project_ids", "excluded_project_ids", "schedule"}),
        "invalid sync scope fields")
    require(scope["schema_version"] == "1.0", "unsupported sync scope")
    try:
        policy = schedule_policy(scope["schedule"] if "schedule" in scope else {"interval_hours":12})
        require("schedule" not in scope or type(scope["schedule"]) is dict, "invalid sync schedule")
    except ValueError:
        require(False, "invalid sync schedule")
    identifier(scope["scope_id"], "scope")
    ids, exclusions = scope["project_ids"], scope["excluded_project_ids"]
    require(type(ids) is list and bool(ids), "sync scope identities required")
    for project_id in ids:
        identifier(project_id, "project")
    require(len(ids) == len(set(ids)), "duplicate sync scope identity")
    require(type(scope["baseline_total"]) is int and scope["baseline_total"] == len(ids),
            "sync denominator must match frozen identities")
    require(type(exclusions) is dict, "invalid sync exclusions")
    for project_id, reason in exclusions.items():
        identifier(project_id, "excluded project")
        string(reason, "exclusion reason")
    eligible, excluded = [], []
    for project_id in ids:
        row = resolver.projects.get(project_id)
        reason = exclusions.get(project_id)
        if row is None:
            reason = "missing_registration"
        elif (row.get("local_presence", {}).get("status") == "removed_local"
              or row.get("current_state_status") == "removed_local"):
            reason = "removed_local"
        elif row.get("enabled") is not True:
            reason = "disabled"
        elif row.get('project_type') in {'cloud','cloud_project'} or row.get('root_kind')=='cloud':
            reason = 'cloud'
        elif row.get("summary_enabled", row.get("enabled")) is not True:
            reason = "summary_disabled"
        elif (row.get("connection_read_allowed", row.get("enabled")) is not True
              or row.get("access_profile") == "no_current_goal_access"):
            reason = "connection_read_disabled"
        if reason is None:
            eligible.append(project_id)
        else:
            excluded.append({"project_id": project_id, "reason": reason})
    for project_id in resolver.projects:
        if project_id not in ids:
            excluded.append({"project_id": project_id,
                             "reason": exclusions.get(project_id, "outside_frozen_scope")})
    return {"scope_id": scope["scope_id"], "scope_total": scope["baseline_total"],
            "schedule": policy,
            "scope_sha256": meta["sha256"],
            "project_ids": list(ids), "eligible_project_ids": eligible, "excluded": excluded,
            "registered_count": len(resolver.projects),
            "outside_scope_count": len(set(resolver.projects) - set(ids))}


def recovery_summary(history: dict, eligible: set[str], *, instant=None) -> dict:
    """Derive finite request recovery from the existing immutable ledger only.

    A successful source read says nothing about business or human acceptance.
    Historical request selections are not historical permission snapshots.
    """
    by_request = {}
    for result in history["results"]:
        by_request.setdefault(result["request_id"], []).append(result)
    summaries, versions = [], []
    for request in history["requests"]:
        ids = set(request["project_ids"])
        versions.append({"request_id": request["request_id"],
                         "project_ids": request["project_ids"],
                         "registry_sha256": request["authority"]["registry_hash"],
                         "started_at": request["started_at"],
                         "authority_drift": request["authority_drift"]})
        if not ids or not ids <= eligible:
            continue
        results = by_request.get(request["request_id"], [])
        codes = Counter(error["code"] for row in results if not row["success"]
                        for error in row["result"]["errors"])
        failed = sum(not row["success"] for row in results)
        summaries.append({"request_id": request["request_id"],
                          "status": request["status"],
                          "started_at": request["started_at"],
                          "finished_at": request["finished_at"],
                          "project_ids": request["project_ids"],
                          "completed": len(request["completed_project_ids"]),
                          "remaining_project_ids": request["remaining_project_ids"],
                          "failed": failed, "failure_codes": dict(sorted(codes.items())),
                          "authority_drift": request["authority_drift"],
                          "can_resume": request["status"] == "OPEN" and not request["authority_drift"]})
    success = [row for row in summaries if row["status"] == "FINISHED" and not row["failed"]]
    failed = [row for row in summaries if row["failed"]]
    return {"projects": {ident: project_recovery(history, ident, instant) for ident in sorted(eligible)},
            "last_success": success[-1] if success else None,
            "last_failure": failed[-1] if failed else None,
            "open_requests": [row for row in summaries if row["status"] == "OPEN"],
            "request_versions": versions,
            "history_basis": "Recorded request selections and authority hashes; not permission snapshots or timer triggers."}


def daily_request_id(scope_id: str, *, instant: datetime | None = None) -> str:
    instant = instant or datetime.now(ZoneInfo("Asia/Tokyo"))
    require(instant.tzinfo is not None, "daily instant needs timezone")
    return f"{scope_id}.daily.{instant.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()}"


def sync_request(root: Path, resolver, ledger, request_id: str, *,
                 project_ids: list[str] | None = None, plan: dict | None = None) -> tuple[dict, int]:
    """Same request resumes committed results; a new request reads sources again."""
    from hub.project_service import ProjectService

    plan = plan if plan is not None else scope_plan(root, resolver)
    identifier(request_id, "request")
    selected = plan["eligible_project_ids"] if project_ids is None else project_ids
    require(type(selected) is list and len(selected) == len(set(selected))
            and set(selected) <= set(plan["eligible_project_ids"]), "sync projects must be eligible scope members")
    if project_ids is not None:
        require(bool(selected), "sync subset must not be empty")
    failures = Counter()
    if selected:
        outcome = refresh(ledger, resolver, request_id, selected)
        for code in outcome["resolver_errors"].values():
            failures[code] += 1
        for stored in ledger.history(request_id)["results"]:
            if not stored["success"]:
                codes = {error["code"] for error in stored["result"]["errors"]}
                for code in codes or {stored["result"]["disposition"]}:
                    failures[code] += 1
        status = outcome["request"]["status"]
        appended = len(outcome["appended_project_ids"])
        head = outcome["projection"]["head"]
    else:
        if any(row["request_id"] == request_id for row in ledger.history()["requests"]):
            raise RequestConflictError("request ID is already bound to a different eligible scope")
        status, appended, head = "NO_ELIGIBLE_PROJECTS", 0, ledger.head
        failures["NO_ELIGIBLE_PROJECTS"] += 1
    payload = {"request_id": request_id, "status": status, "appended_count": appended,
               "failure_code_counts": dict(sorted(failures.items())), "head": head}
    try:
        summary = ProjectService(root, ledger_path=ledger.path).sync_status()
    except ServiceError as exc:
        # Preserve the committed request receipt even if a concurrent authority
        # change makes the current summary unavailable. Never print source data
        # or a traceback to the user journal after a successful commit.
        payload.update(summary=None, summary_error=exc.code)
        return payload, 2
    if summary["head"] != head:
        payload.update(summary=None, summary_error="SYNC_HEAD_CHANGED")
        return payload, 2
    payload["summary"] = summary["counts"]
    return payload, 2 if failures else 0
