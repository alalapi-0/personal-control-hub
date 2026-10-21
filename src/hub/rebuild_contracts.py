"""Versioned contracts on the existing TaskStore; never an executor or auth store."""
from __future__ import annotations
import copy
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from jsonschema import Draft202012Validator
from .service_contract import ServiceError
from .workbench_store import canonical,digest


def reject(code='REBUILD_CONTRACT_REJECTED'):
    raise ServiceError(code,status=409)


def exact_paths(values):
    from .ordinary_tasks import _input_path
    if type(values)is not list or len(values)>32 or len(set(values))!=len(values):reject()
    for value in values:
        _input_path(value)
        if value=='.' or any(c in value for c in '*?[]{}') or not Path(value).parts:reject()
    return values


def validate(value,kind):
    name='receipt' if kind=='receipt' else 'task-package'
    schema=json.loads((Path(__file__).parents[2]/'schemas/rebuild'/(name+'.schema.json')).read_text())
    try:
        if len(canonical(value))>8192:reject('REBUILD_CONTRACT_CAPACITY')
        legacy=value.get('schema_version')=='1.0'
        if legacy:schema=schema['$defs']['LegacyAttempt' if kind=='attempt' else 'Legacy']
        elif kind=='attempt':schema=schema['$defs']['Attempt']
        Draft202012Validator(schema).validate(value)
        if legacy:return copy.deepcopy(value) # Historical shape only, never v2 admission authority.
        if value['fact_time'] is None and not value['unknown_reason']:reject()
        if value['fact_time'] is not None and value['unknown_reason'] is not None:reject()
        if kind=='package':
            exact_paths(value['allowed_modules']);exact_paths(list(value['source_versions']))
            for item in value['binding']['input_files']:exact_paths([item['path']])
            effects=value['effects'];paths=exact_paths(effects['writable_scope'])
            if (effects['mode']=='read_only') != (paths==[]):reject()
            if any(not any(p==m or p.startswith(m+'/') for m in value['allowed_modules']) for p in paths):reject()
            if value['sandbox']['write_paths']!=paths:reject()
            if value['sandbox']['profile']!=('ordinary_readonly_v1' if effects['mode']=='read_only' else 'exact_scoped_write_v1'):reject()
            baselines=value.get('write_baselines',{})
            if set(baselines)!=set(paths):reject('REBUILD_WRITE_BASELINES_REQUIRED')
            exact_paths(list(baselines))
            for path,state in baselines.items():
                if value['source_versions'].get(path)!=digest(state):reject('REBUILD_SOURCE_VERSION_REJECTED')
            for item in value['binding']['input_files']:
                if item['path'] not in baselines and value['source_versions'].get(item['path'])!=item['sha256']:reject('REBUILD_SOURCE_VERSION_REJECTED')
            if value['lineage']['original_task_id']!=value['task_id']:reject()
            deps=[d['task_id'] for d in value['dependencies']]
            if value['task_id'] in deps or len(deps)!=len(set(deps)):reject()
            if len({c['check_id'] for c in value['acceptance']})!=len(value['acceptance']):reject()
        if kind=='attempt':
            ex=value['executor']
            if ex['kind']=='model' and (ex['thread_id'] is None or ex['turn_id'] is None):reject()
            if ex['kind']=='deterministic_host' and (ex['thread_id'] is not None or ex['turn_id'] is not None):reject()
        if kind=='receipt':
            if value['ended_at']<value['started_at']:reject()
            for name,key in [('artifacts','path'),('checks','check_id')]:
                vals=[r[key] for r in value[name]]
                if len(vals)!=len(set(vals)):reject()
            for item in value['artifacts']:exact_paths([item['path']])
    except ServiceError:raise
    except Exception:reject()
    return copy.deepcopy(value)


def observe_workspace(workspace):
    """Read-only Git identity, never fetch/index refresh or worktree mutation."""
    from .task_service import root_identity
    root_identity(workspace['worktree'])
    result=subprocess.run(['git','-C',workspace['worktree'],'rev-parse','--path-format=absolute','--show-toplevel','--git-common-dir','--abbrev-ref','HEAD','HEAD'],capture_output=True,text=True,timeout=5)
    if result.returncode:reject('REBUILD_WORKSPACE_UNKNOWN')
    rows=result.stdout.splitlines()
    if len(rows)!=4:reject('REBUILD_WORKSPACE_UNKNOWN')
    return {'repository':str(Path(rows[1]).parent),'worktree':rows[0],'worktree_identity':root_identity(rows[0]),'branch':rows[2],'baseline_commit':rows[3]}


@dataclass(frozen=True,slots=True)
class RootAuthority:
    """Internal server wiring. Request APIs never construct or replace this capability.

    Serialized data is defensively immutable; callback objects come only from
    existing server authentication/workspace/sandbox/writer integrations.
    """
    _data: bytes
    _authorize: object
    _workspace: object
    _sandbox: object
    _writer: object
    _plan: object = None
    _budget: object = None

    @classmethod
    def configured(cls,binding,*,authorize,workspace_check=observe_workspace,sandbox_check,writer_check,plan_authority=None,budget_authority=None):
        if not all(callable(x) for x in (authorize,workspace_check,sandbox_check,writer_check)):reject('REBUILD_AUTHORITY_REQUIRED')
        if plan_authority is not None:
            from .plan_admission import authority as require_plan_authority
            require_plan_authority(plan_authority)
        if budget_authority is not None:
            from .dispatch_budget import authority as require_budget
            require_budget(budget_authority)
        return cls(canonical(binding),authorize,workspace_check,sandbox_check,writer_check,plan_authority,budget_authority)

    def snapshot(self):return json.loads(self._data)

    def check(self,store,package,grant,receipt=None):
        self._authorize()
        b=self.snapshot()
        if package['schema_version']!='2.0' or digest(package)!=b['package_sha256']:reject('REBUILD_AUTHORITY_MISMATCH')
        if package['route']!=b['route'] or package['project_id']!=b['project_id'] or package['binding']['root']!=b['root']:reject('REBUILD_ROUTE_REJECTED')
        from .ordinary_tasks import check_registration,validate_grant,validate_scoped_write_grant
        check_registration(grant,store.root) # Registry eligibility BEFORE external workspace/input reads.
        if package['effects']['mode']=='scoped_write':
            grant=validate_scoped_write_grant(grant,live=receipt is None)
            if (grant['task_id']!=package['task_id'] or grant['lineage']!=package['lineage'] or grant['workspace']!=package['workspace']
                    or grant['allowed_modules']!=package['allowed_modules'] or grant['write_paths']!=package['effects']['writable_scope']
                    or grant['write_baselines']!=package['write_baselines']):reject()
        else:grant=validate_grant(grant,live=True)
        if grant['project_id']!=package['project_id']:reject()
        if receipt is not None:
            states=None
            if grant['effect']=='scoped_write':
                from .ordinary_tasks import scoped_observation
                states=scoped_observation(grant)
            if receipt['end_versions']!=current_versions(grant,states=states):reject('REBUILD_INPUT_STALE')
            if grant['effect']=='scoped_write':
                observed={p:states[p] for p in grant['write_paths']}
                if receipt.get('write_states')!=observed:reject('REBUILD_WRITE_STATES_REJECTED')
                expected_artifacts=[]
                for path,state in observed.items():
                    if state!=grant['write_baselines'][path]:
                        expected_artifacts.append({'path':path,**({'sha256':state['sha256']} if state['state']=='file' else {'state':'absent'})})
                if sorted(receipt['artifacts'],key=lambda x:x['path'])!=sorted(expected_artifacts,key=lambda x:x['path']):reject('REBUILD_ARTIFACTS_REQUIRED')
            elif receipt.get('write_states'):
                reject('REBUILD_ARTIFACT_REJECTED')
        expected={k:grant[k] for k in ('root','root_identity','registry_hash','input_version','input_files')}
        if package['binding']!=expected:reject('REBUILD_INPUT_STALE')
        if package['grant']!={k:grant[k] for k in ('grant_id','version','source','auth','expires_at')}|{'sha256':digest(grant)}:reject('REBUILD_GRANT_REJECTED')
        if package['sandbox']!={'version':'1.0','profile':'ordinary_readonly_v1' if grant['effect']=='read_only' else 'exact_scoped_write_v1','read_paths':[i['path'] for i in grant['input_files']],'write_paths':package['effects']['writable_scope']}:reject('REBUILD_SANDBOX_REJECTED')
        if self._workspace(copy.deepcopy(package['workspace']))!=package['workspace']:reject('REBUILD_WORKSPACE_STALE')
        if self._sandbox(copy.deepcopy(package),copy.deepcopy(grant)) is not True:reject('REBUILD_SANDBOX_REJECTED')
        if self._writer(copy.deepcopy(grant)) is not True:reject('EXTERNAL_WRITER_UNKNOWN')
        return b


def require_authority(value):
    if type(value)is not RootAuthority:reject('REBUILD_AUTHORITY_REQUIRED')
    return value


def fence(claim):
    return digest({k:claim[k] for k in ('task_id','owner','generation')})


def bind(package,attempt,task,grant,authority,claim,*,now):
    package=validate(package,'package');attempt=validate(attempt,'attempt')
    b=authority.snapshot()
    if (package['schema_version']!='2.0' or attempt['schema_version']!='2.0' or package['task_id']!=task['id']
            or attempt['task_id']!=task['id'] or task['project']!=package['project_id'] or task['intent']['grant']!=grant
            or task['intent']['grant_hash']!=digest(grant) or attempt['package_sha256']!=digest(package)
            or attempt['effect_intent']!=package['effects'] or attempt['executor']!=b['executor']):reject()
    if package['effects']['mode']=='scoped_write' and attempt.get('write_baseline_sha256')!=digest(package['write_baselines']):reject('REBUILD_WRITE_BASELINES_REQUIRED')
    if package['effects']['mode']=='read_only' and 'write_baseline_sha256' in attempt:reject('REBUILD_WRITE_BASELINES_REQUIRED')
    if (attempt['lease']!={k:claim[k] for k in ('owner','generation','fence','expires_at')}
            or attempt['coverage']!={'claim_started_at':claim['started_at'],'observed_until':claim['expires_at']}
            or task['status']!='running' or task['cancel'] or task['owner']!=claim['owner'] or task['lease_until']<=now):reject('REBUILD_LEASE_REJECTED')
    budget=package['budget'];seconds=attempt['budget_reservation']['seconds']
    if not (claim['started_at']<=attempt['started_at']<=now<attempt['started_at']+seconds
            and 0<seconds<=min(900,budget['remaining_seconds'],budget['timeout_seconds'],grant['max_turn_seconds'])
            and attempt['started_at']+seconds<=min(budget['deadline'],grant['expires_at'])):reject('REBUILD_BUDGET_REJECTED')
    return package,attempt


def receipt_binding(receipt,package,attempt):
    receipt=validate(receipt,'receipt')
    if (receipt['schema_version']!='2.0' or receipt['task_id']!=package['task_id'] or receipt['attempt_id']!=attempt['attempt_id']
            or receipt['package_sha256']!=digest(package) or receipt['attempt_sha256']!=digest(attempt)
            or receipt['claim']!={'generation':attempt['lease']['generation'],'fence':attempt['lease']['fence'],'claimed_at':attempt['coverage']['claim_started_at']}
            or receipt['started_at']!=attempt['started_at'] or receipt['actual_baseline']!=package['workspace']['baseline_commit']
            or receipt['ended_at']>attempt['started_at']+attempt['budget_reservation']['seconds']
            or receipt['budget_coverage']!={'reserved_seconds':attempt['budget_reservation']['seconds'],'actual_seconds':receipt['ended_at']-receipt['started_at']}
            or receipt['start_versions']!={k:package['binding'][k] for k in ('root_identity','input_version')}
            or receipt['producer']['kind']!=attempt['executor']['kind']):reject('REBUILD_RECEIPT_BINDING_REJECTED')
    if receipt['effect_state'] not in {package['effects']['mode'],'unknown'}:reject('REBUILD_EFFECT_MISMATCH')
    if package['effects']['mode']=='read_only' and receipt.get('write_states'):reject('REBUILD_EFFECT_MISMATCH')
    # Unknown outcomes do not grant deletion authority; bind to the package.
    if package['effects']['mode']!='scoped_write' and any(a.get('state')=='absent' for a in receipt['artifacts']):reject('REBUILD_EFFECT_MISMATCH')
    if package['effects']['mode']=='scoped_write':
        states=receipt.get('write_states',{})
        if set(states)!=set(package['write_baselines']):reject('REBUILD_WRITE_STATES_REJECTED')
        expected=[{'path':p,**({'sha256':v['sha256']} if v['state']=='file' else {'state':'absent'})} for p,v in states.items() if v!=package['write_baselines'][p]]
        if sorted(receipt['artifacts'],key=lambda x:x['path'])!=sorted(expected,key=lambda x:x['path']):reject('REBUILD_ARTIFACTS_REQUIRED')
        from .ordinary_tasks import scoped_input_version
        context={**package['binding'],'project_id':package['project_id'],'workspace':package['workspace'],'effect':'scoped_write','allowed_modules':package['allowed_modules'],'write_paths':package['effects']['writable_scope'],'write_baselines':package['write_baselines']}
        observed={i['path']:{'state':'file','sha256':i['sha256']} for i in context['input_files']};observed.update(states)
        if package['binding']['input_version']!=scoped_input_version(context) or receipt['end_versions']!={'root_identity':context['root_identity'],'input_version':scoped_input_version(context,states=observed)}:reject('REBUILD_INPUT_STALE')
        if attempt.get('write_baseline_sha256')!=digest(package['write_baselines']):reject('REBUILD_WRITE_BASELINES_REQUIRED')
    return receipt


def settled_evidence(package,receipt,provenance,attempt):
    """Dependency evidence only, never changes a task's authoritative status."""
    receipt_binding(receipt,package,attempt)
    expected={c['check_id'] for c in package['acceptance'] if c['required']}
    checks={c['check_id']:c for c in receipt['checks']}
    if not expected or not expected<=set(checks) or receipt['producer']['self_report'] or receipt['effect_state']=='unknown' or receipt['unknowns']:reject('REBUILD_DEPENDENCY_FAILED')
    for name in expected:
        c=checks[name]
        if c['exit']!=0 or c['candidate_hash']!=package['source_version'] or not c['evidence_ref'] or not c['scope']:reject('REBUILD_DEPENDENCY_FAILED')
    for key in ('delivery','integration','sync'):
        policy=package[key];evidence=receipt[key]
        if policy['required']:
            if not (evidence['status']=='passed' or policy['allow_not_applicable'] and evidence['status']=='not_applicable'):reject('REBUILD_DEPENDENCY_FAILED')
            if not evidence['evidence_ref'] or evidence['source_revision']!=package['source_version']:reject('REBUILD_DEPENDENCY_FAILED')
    expected_proof={'task_id':package['task_id'],'package_sha256':digest(package),'attempt_sha256':digest(attempt),'receipt_sha256':digest(receipt)}
    if not provenance or any(provenance.get(k)!=v for k,v in expected_proof.items()) or provenance.get('source')!='root_verified' or not provenance.get('evidence_ref') or not provenance.get('evidence_sha256') or provenance.get('expires_at',0)<__import__('time').time():reject('REBUILD_DEPENDENCY_UNVERIFIED')


def current_versions(grant,*,states=None):
    from .ordinary_tasks import snapshot_inputs,scoped_observation,scoped_input_version
    if grant['kind']=='rebuild_scoped_write':
        if states is None:states=scoped_observation(grant)
        version=scoped_input_version(grant,states=states)
    else:
        files=snapshot_inputs(grant)
        if files!=grant['input_files']:reject('REBUILD_INPUT_STALE')
        version=grant['input_version']
    return {'root_identity':grant['root_identity'],'input_version':version}
