"""Source candidates in plan_inputs; never execution or whole-task settlement.

Filesystem capability and evidence are rechecked by internal server callbacks.
Offline validation checks retained content, without renewing that authority.
"""
from __future__ import annotations
import copy
import re
from pathlib import PurePosixPath
from .service_contract import ServiceError
from .workbench_store import canonical, digest

FORMAT = 'plan-source-evidence/1'
SOURCE_IDS = frozenset('''P3-R1-PCH-GRAPH P3-R1-PCH-RELATIONS P3-R2-PCH-OVERVIEW P3-R2-PCH-FOCUS P3-R3-PCH-ACTIONS P3-R3-PCH-IMPACT P3-R3-PCH-PORTS P6-R1-PCH-PRECISE-SAVE P6-R1-PCH-SEMANTIC-UI P6-R2-PCH-ARTIFACTS P6-R2-PCH-VERSION'''.split())


def is_psh_source_task(definition):
    """Structural classification only, never adopted membership or permission.

    The Store binds the exact existing plan definition; plan CAS cannot add IDs.
    Each classified task still needs its own live workspace registration and
    development/evidence callbacks, including when sharing another task's root.
    """
    return (type(definition) is dict
            and definition.get('phase_id') == 'P5'
            and definition.get('admission_class') == 'verified_project_execution'
            and definition.get('project_id') == 'personal-style-hub'
            and type(definition.get('id')) is str
            and re.fullmatch(r'P5-personal-style-hub-(?:0[1-9]|[1-9][0-9])', definition['id']) is not None)


def need(ok, code='PLAN_SOURCE_REJECTED'):
    if not ok:
        raise ServiceError(code, status=409)


def sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def path(value, *, absolute=False):
    need(type(value) is str and 0 < len(value) <= 4096 and not any(ord(c) < 32 for c in value))
    p = PurePosixPath(value)
    need(p.is_absolute() == absolute and str(p) == value and '..' not in p.parts and value not in {'.', '/'})
    return value


def workspace(value):
    need(type(value) is dict and set(value) == {'canonical_root', 'delivery_root', 'native_thread_id'})
    path(value['canonical_root'], absolute=True)
    path(value['delivery_root'], absolute=True)
    need(type(value['native_thread_id']) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value['native_thread_id']) is not None)


def inputs(value):
    need(type(value) is list and 1 <= len(value) <= 128)
    names = set()
    for item in value:
        need(type(item) is dict and set(item) == {'path', 'sha256'})
        path(item['path'])
        need(item['path'] not in names and sha(item['sha256']))
        names.add(item['path'])


def policy(value):
    need(type(value) is dict and set(value) == {'schema_version', 'dependencies', 'inputs', 'owned_paths', 'workspace'})
    need(value['schema_version'] == 'source-admission/1')
    workspace(value['workspace'])
    inputs(value['inputs'])
    owned = value['owned_paths']
    need(type(owned) is list and 1 <= len(owned) <= 128 and len(set(owned)) == len(owned))
    for item in owned:
        path(item)
    deps = value['dependencies']
    need(type(deps) is list and len(deps) <= 128)
    seen = set()
    from .task_store import stable_id
    for dep in deps:
        need(type(dep) is dict and dep.get('kind') in {'accepted', 'source_candidate'})
        need(set(dep) == ({'task_id', 'kind', 'candidate_sha256'} if dep['kind'] == 'source_candidate' else {'task_id', 'kind'}))
        stable_id(dep['task_id'])
        need(dep['task_id'] not in seen)
        seen.add(dep['task_id'])
        if dep['kind'] == 'source_candidate':
            need(sha(dep['candidate_sha256']))
    return copy.deepcopy(value)


def validate_policies(by):
    edges = {}
    for ident, row in by.items():
        if 'source_admission' not in row:
            continue
        need((ident in SOURCE_IDS or is_psh_source_task(row)) and row['status'] != 'done')
        p = policy(row['source_admission'])
        edges[ident] = [d['task_id'] for d in p['dependencies']]
        need(all(d in by and d != ident for d in edges[ident]))
    def visit(ident, active, done):
        need(ident not in active, 'PLAN_SOURCE_CYCLE')
        if ident in done:
            return
        for dep in edges.get(ident, []):
            visit(dep, active | {ident}, done)
        done.add(ident)
    done = set()
    for ident in edges:
        visit(ident, set(), done)


def record(value, versions):
    keys = {'schema_version', 'request_id', 'plan_sha256', 'task_id', 'definition_sha256', 'candidate_sha256', 'inputs', 'workspace', 'owned_paths', 'checks', 'status', 'unknowns', 'provenance'}
    need(type(value) is dict and (set(value) == keys or set(value) == keys | {'reconciles'}) and len(canonical(value)) <= 32768)
    need(value['schema_version'] == 'plan-source-record/1')
    from .task_store import stable_id
    stable_id(value['request_id'])
    need(value['plan_sha256'] in versions and sha(value['candidate_sha256']))
    definition = next((t for t in versions[value['plan_sha256']]['tasks'] if t['id'] == value['task_id']), None)
    need(definition is not None and 'source_admission' in definition and digest(definition) == value['definition_sha256'])
    p = policy(definition['source_admission'])
    need(all(value[k] == p[k] for k in ('inputs', 'workspace', 'owned_paths')))
    need(value['status'] in {'implemented', 'unknown', 'cancelled'} and type(value['unknowns']) is list and len(value['unknowns']) <= 16)
    need(all(type(x) is str and 0 < len(x) <= 128 for x in value['unknowns']))
    checks = value['checks']
    need(type(checks) is list and 1 <= len(checks) <= 32)
    names = set()
    for ck in checks:
        need(type(ck) is dict and set(ck) == {'name', 'status', 'evidence_sha256'})
        stable_id(ck['name'])
        need(ck['name'] not in names and ck['status'] in {'passed', 'failed', 'unknown'} and sha(ck['evidence_sha256']))
        names.add(ck['name'])
    provenance = value['provenance']
    need(type(provenance) is dict and set(provenance) == {'source', 'evidence_sha256'} and provenance['source'] == 'root_verified' and sha(provenance['evidence_sha256']))
    if 'reconciles' in value:
        refs = value['reconciles']
        need(value['status'] == 'implemented' and type(refs) is list and 1 <= len(refs) <= 256)
        need(all(type(x) is str for x in refs) and len(set(refs)) == len(refs))
        for ref in refs:
            stable_id(ref)
    if value['status'] == 'implemented':
        need(not value['unknowns'] and all(c['status'] == 'passed' for c in checks))
    return copy.deepcopy(value)


def split(value):
    if type(value) is dict and str(value.get('schema_version', '')).startswith('plan-source-evidence/'):
        need(value['schema_version'] == FORMAT and set(value) == {'schema_version', 'plan_history', 'records'}, 'PLAN_SOURCE_CORRUPT')
        need(type(value['records']) is list and 1 <= len(value['records']) <= 256 and len(canonical(value['records'])) <= 2 * 1024 * 1024, 'PLAN_SOURCE_CAPACITY')
        inner = value['plan_history']
        need(not (type(inner) is dict and str(inner.get('schema_version', '')).startswith(('plan-source-evidence/', 'plan-dispatch-budget/'))), 'PLAN_SOURCE_CORRUPT')
        return inner, value['records']
    return value, []


def wrap(history, records):
    return {'schema_version': FORMAT, 'plan_history': history, 'records': records} if records else history


def validate_records(rows, versions):
    ids = set()
    for item in rows:
        need(type(item) is dict and set(item) == {'expected_source_sha256', 'record'}, 'PLAN_SOURCE_CORRUPT')
        need(item['expected_source_sha256'] == digest(rows[:len(ids)]), 'PLAN_SOURCE_CAS_CORRUPT')
        r = record(item['record'], versions)
        need(r['request_id'] not in ids, 'PLAN_SOURCE_REQUEST_CONFLICT')
        validate_reconciliation(r, rows[:len(ids)])
        ids.add(r['request_id'])


def unknown_history(rows, verify=None):
    """Unknown identity survives plan/definition changes; only explicit proof clears it.

    Offline readers validate links but never grant live authority. Live readers
    recheck both the original observation and its resolution on every use.
    """
    pending = {}
    observations = {}
    cancelled = set()
    for entry in rows:
        r = entry['record']; ident = r['task_id']
        if r['status'] == 'unknown' or r['unknowns']:
            pending.setdefault(ident, set()).add(r['request_id'])
            observations[r['request_id']] = r
        if r['status'] == 'cancelled':
            cancelled.add(ident)
        refs = r.get('reconciles', [])
        if refs and (verify is None or verify(copy.deepcopy(r)) is True and all(verify(copy.deepcopy(observations[ref])) is True for ref in refs)):
            pending.setdefault(ident, set()).difference_update(refs)
    return pending, cancelled


def validate_reconciliation(value, previous):
    refs = value.get('reconciles')
    if refs is None:
        return
    pending, _ = unknown_history(previous)
    need(set(refs) == pending.get(value['task_id'], set()) and bool(refs), 'PLAN_SOURCE_RECONCILIATION_REJECTED')


def applicable(plan, rows, verify):
    """Exact definition/hash applicability; unknowns are not completion."""
    definitions = {t['id']: t for t in plan['tasks']}
    available = {}
    pending, cancelled = unknown_history(rows, verify)
    blocked = {ident for ident, refs in pending.items() if refs} | cancelled
    for entry in rows:
        r = entry['record']
        t = definitions.get(r['task_id'])
        if t is None or digest(t) != r['definition_sha256']:
            continue
        if r['status'] != 'implemented':
            continue  # Unknown/cancelled rows are handled across all versions above.
        if verify(copy.deepcopy(r)) is not True:
            blocked.add(r['task_id'])
        else:
            available.setdefault(r['task_id'], set()).add(r['candidate_sha256'])
    # Changing an implementation policy cannot bypass an unresolved real edge.
    while True:
        descendants = {ident for ident, t in definitions.items()
                       if set(t['depends_on']) & blocked or
                       {d['task_id'] for d in t.get('source_admission', {}).get('dependencies', [])} & blocked}
        if descendants <= blocked:
            break
        blocked |= descendants
    for ident in blocked:
        available.pop(ident, None)
    return available, blocked


def source_ready(plan, states, accepted, rows, development, verify, *, reconciliation=None):
    """Pure candidates only; neither ROUTES bypass nor dispatch permission."""
    from .plan_admission import validate_plan
    plan = validate_plan(plan)
    if reconciliation is not None:
        # This is a checked result proposal, never a dispatch or state rewrite.
        validate_reconciliation(reconciliation, rows)
        rows = [*rows, {'record': reconciliation}]
    available, blocked = applicable(plan, rows, verify)
    definitions = {t['id']: t for t in plan['tasks']}
    def deps_ok(t):
        for d in t['source_admission']['dependencies']:
            ident = d['task_id']
            if d['kind'] == 'accepted':
                if ident not in accepted:
                    return False
            elif d['candidate_sha256'] not in available.get(ident, set()):
                return False
        return True
    # A retained producer cannot lend its candidate through a now-invalid chain.
    while True:
        invalid = {i for i in available if i in states or not deps_ok(definitions[i])}
        if not invalid:
            break
        for i in invalid:
            available.pop(i)
            blocked.add(i)
    ready = []
    for t in plan['tasks']:
        ident = t['id']
        if 'source_admission' not in t or ident in accepted or ident in states or ident in blocked or t['status'] == 'done':
            continue
        if deps_ok(t) and development(copy.deepcopy(t)) is True:
            ready.append(ident)
    return sorted(ready, key=lambda i: (definitions[i]['dispatch_priority'], i))[:32]
