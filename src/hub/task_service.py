"""Server-only grant routing. Client IDs and feedback are never authority."""
from __future__ import annotations

import copy
import hashlib
import os
import stat
import time
from pathlib import Path

from .service_contract import ServiceError
from .task_store import MAX_TASKS, stable_id
from .workbench_store import digest, validate_command
from .preview_service import PreviewService

REQUEST_FIELDS={'request_id','project_id','annotation_id','annotation_revision','grant_id','grant_version','mode','parent_task_id'}
GRANT_FIELDS={'grant_id','version','source','task_id','project_id','root','root_identity','fingerprint','mode','parent_task_id',
              'annotation_hash','checks','expires_at','isolated_root','writable_scope','prohibited',
              'auth','max_turn_seconds','image','image_sha256'}
FIXED_CHECKS={'fixture_sample':('/usr/bin/python3','-c',"from pathlib import Path; assert Path('index.html').read_text() == '<!doctype html><title>Fixture</title><h1>after</h1>\\n'; print('fixture sample verified')")}


def root_identity(root):
    raw=Path(root)
    if not raw.is_absolute() or '..'in raw.parts or raw.resolve()!=raw:
        raise ServiceError('GRANT_ROOT_REJECTED')
    current=Path(raw.anchor)
    for part in raw.parts[1:]:
        current/=part
        info=current.lstat()
        if not stat.S_ISDIR(info.st_mode):raise ServiceError('GRANT_ROOT_REJECTED')
    info=raw.stat()
    return digest([str(raw),info.st_dev,info.st_ino])


def fingerprint(root):
    """Only an explicitly authorized disposable root; never traverse registry roots."""
    root=Path(root);root_identity(root);rows=[]
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            path=Path(base)/name;info=path.lstat();relative=str(path.relative_to(root))
            if len(rows)>=32:raise ServiceError('GRANT_ROOT_CAPACITY')
            if stat.S_ISLNK(info.st_mode):
                rows.append([relative,'symlink',os.readlink(path)])
                if name in dirs:dirs.remove(name)
            elif stat.S_ISDIR(info.st_mode):rows.append([relative,'directory'])
            elif stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=1048576:
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as stream:raw=stream.read(1048577)
                if len(raw)>1048576:raise ServiceError('GRANT_ROOT_CAPACITY')
                rows.append([relative,hashlib.sha256(raw).hexdigest()])
            else:raise ServiceError('GRANT_ROOT_REJECTED')
    return sorted(rows)


def validate_grant(grant, *, live=False):
    if type(grant)is dict and grant.get('kind')=='ordinary_project':
        from .ordinary_tasks import validate_grant as validate_ordinary_grant
        return validate_ordinary_grant(grant,live=live)
    if type(grant)is dict and grant.get('kind')=='story_exchange':
        from .story_exchange import validate
        return validate(grant,live=live)
    css=type(grant)is dict and grant.get('checks')==['csp_css_trial']
    extra={'approved_commands'}if type(grant)is dict and 'approved_commands'in grant and not css else set()
    if type(grant)is not dict or set(grant)!=(GRANT_FIELDS|{'css_trial'}if css else GRANT_FIELDS|extra):raise ServiceError('INVALID_GRANT')
    if extra:
        commands=grant['approved_commands']
        if type(commands)is not list or not 1<=len(commands)<=8:raise ServiceError('INVALID_GRANT')
        seen=set()
        for command in commands:
            if type(command)is not dict or set(command)!={'command','cwd'} or \
                type(command['command'])is not str or not command['command'].strip() or \
                len(command['command'])>4096 or '\x00'in command['command'] or \
                type(command['cwd'])is not str or command['cwd']!=grant['root'] or command['command']in seen:
                raise ServiceError('INVALID_GRANT')
            seen.add(command['command'])
    for key in ('grant_id','project_id','task_id'):stable_id(grant[key])
    if grant['mode']not in {'new','idle_continue','busy_feedback'} or (grant['mode']=='new')!=(grant['parent_task_id']is None):raise ServiceError('INVALID_GRANT')
    if grant['parent_task_id']is not None:stable_id(grant['parent_task_id'])
    if type(grant['version'])is not int or grant['version']<1 or grant['source']!='verified_owner_decision':raise ServiceError('INVALID_GRANT')
    if grant['auth']!='ChatGPT' or grant['isolated_root']is not True or grant['writable_scope']!=['.'] or grant['prohibited']!=[]:
        raise ServiceError('EXACT_PROJECT_ISOLATION_UNVERIFIED',status=403)
    if type(grant['checks'])is not list or (not css and grant['checks']!=['fixture_sample']):raise ServiceError('CHECK_PLAN_REJECTED')
    if type(grant['expires_at'])not in (int,float) or not time.time()<grant['expires_at']<time.time()+86400:raise ServiceError('GRANT_EXPIRED',status=403)
    if type(grant['max_turn_seconds'])is not int or not 10<=grant['max_turn_seconds']<=600:raise ServiceError('INVALID_GRANT')
    for field in ('root_identity','annotation_hash','image_sha256'):
        value=grant[field]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef'for c in value):raise ServiceError('INVALID_GRANT')
    image=grant['image']
    if css:
        from .css_trial import validate
        validate(grant,live=live)
        return copy.deepcopy(grant)
    if not isinstance(image,str) or Path(image).is_absolute() or '..'in Path(image).parts or image!='fixture.png':raise ServiceError('IMAGE_SCOPE_REJECTED')
    if live:
        if root_identity(grant['root'])!=grant['root_identity'] or fingerprint(grant['root'])!=grant['fingerprint']:raise ServiceError('GRANT_VERSION_STALE',status=409)
        path=Path(grant['root'])/image
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb')as stream:
            info=os.fstat(stream.fileno());raw=stream.read(1048577)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or len(raw)>1048576 or not raw.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(raw).hexdigest()!=grant['image_sha256']:
            raise ServiceError('IMAGE_VERSION_STALE',status=409)
    return copy.deepcopy(grant)


class TaskService:
    def __init__(self,projects,annotations,store, *, test_gate=False, writer_check=None, runtime_status=None, ordinary_enabled=False,
                 ordinary_grant_ids=None, rebuild_binding=None, rebuild_authority=None, plan_authority=None, normal_budget_bindings=None):
        from .plan_admission import authority as plan_capability
        self.__plan_authority=plan_capability(plan_authority) if plan_authority is not None else None
        self.projects,self.annotations,self.store=projects,annotations,store
        self.test_gate=test_gate
        # An internal lease cannot certify an unrelated writer's absence.
        self.writer_check=writer_check or (lambda _:False)
        self.writer_check_configured=writer_check is not None
        from .dispatch_budget import normal_bindings
        self.__normal_budgets=normal_bindings(normal_budget_bindings)
        self.runtime_status=runtime_status
        self.ordinary_enabled=ordinary_enabled is True
        self.ordinary_grant_ids=ordinary_grant_ids
        from .rebuild_contracts import require_authority
        if rebuild_binding is not None:raise ServiceError('REBUILD_AUTHORITY_REQUIRED',status=403)
        self.__rebuild_authority=require_authority(rebuild_authority) if rebuild_authority is not None else None

    @property
    def runtime_status(self):return self._runtime_status

    @runtime_status.setter
    def runtime_status(self,value):
        # Default/deferred legacy startup does not inspect or instantiate a
        # Runtime. An attached budget-backed service still cannot be detached.
        if value is None:
            if self.__normal_budgets and getattr(self,'_runtime_status',None)is not None:
                raise ServiceError('ORDINARY_RUNTIME_BINDING_REJECTED',status=403)
            self._runtime_status=None
            return
        from .ordinary_runtime import OrdinaryRuntime
        runtime=getattr(value,'__self__',None)
        runtime_type=isinstance(OrdinaryRuntime,type)
        protocol=getattr(OrdinaryRuntime,'normal_budget_bindings',None)if runtime_type else None
        budget_protocol=isinstance(protocol,property)and callable(protocol.fget)
        configured=(protocol.fget(runtime)if budget_protocol and isinstance(runtime,OrdinaryRuntime)else {})
        if self.__normal_budgets or configured:
            if not budget_protocol or type(runtime)is not OrdinaryRuntime or runtime.store is not self.store:
                raise ServiceError('ORDINARY_RUNTIME_BINDING_REJECTED',status=403)
            if self.__normal_budgets and configured and (
                    self.__normal_budgets.keys()!=configured.keys()or
                    any(self.__normal_budgets[k]is not configured[k]for k in configured)):
                raise ServiceError('NORMAL_DISPATCH_BINDING_REJECTED',status=409)
            chosen=self.__normal_budgets or dict(configured)
            # Runtime lock rechecks immutable agreement and never-started state
            # before either service field changes. No merge/JSON reconstruction.
            runtime.bind_dispatch_budgets(chosen)
            self.__normal_budgets=dict(chosen)
        self._runtime_status=value

    def reconcile_ordinary_budget(self,ident,observation,*,expected_task_sha256,expected_marker,expected_events_sha256):
        """Internal stopped-runtime recovery; no HTTP-selected authority."""
        binding=self.__normal_budgets.get(ident)
        if binding is None:raise ServiceError('NORMAL_DISPATCH_BINDING_REQUIRED',status=403)
        runtime=getattr(self.runtime_status,'__self__',None)
        if runtime is not None and (runtime._thread is not None or runtime._handles):
            raise ServiceError('ORDINARY_RUNTIME_BINDING_REJECTED',status=409)
        return self.store.reconcile_owned(ident,observation,normal_budget_binding=binding,
            expected_task_sha256=expected_task_sha256,expected_marker=expected_marker,
            expected_events_sha256=expected_events_sha256)

    @property
    def rebuild_binding(self):
        return self.__rebuild_authority.snapshot() if self.__rebuild_authority else None

    def _contract_authority(self,owner_context,authorize):
        if authorize is not None:raise ServiceError('REBUILD_CALLER_AUTHORITY_REJECTED',status=403)
        authority=self.__rebuild_authority
        if authority is None:raise ServiceError('REBUILD_ROUTE_UNCONFIGURED',status=403)
        authority._authorize()
        if owner_context!=authority.snapshot()['owner_context']:raise ServiceError('TASK_OWNER_REJECTED',status=403)
        return authority

    def prepare_rebuild(self,package,*,owner_context,authorize=None):
        return self.store.prepare_rebuild(package,authority=self._contract_authority(owner_context,authorize))

    def admit_rebuild(self,package,attempt,*,owner_context,authorize=None):
        authority=self._contract_authority(owner_context,authorize)
        try:return self.store.record_rebuild_attempt(package,attempt,authority=authority)
        except ServiceError as error:
            if error.code=='REBUILD_LEASE_REJECTED':self.store.reconcile_rebuild(package['task_id'],authority=authority)
            raise

    def record_rebuild_receipt(self,receipt,*,owner_context,authorize=None):
        return self.store.record_rebuild_receipt(receipt,authority=self._contract_authority(owner_context,authorize))

    def _plan_authority(self,owner_context):
        from .plan_admission import authority,reject
        cap=authority(self.__plan_authority);cap._authorize()
        if cap.snapshot().get('owner_context')!=owner_context:reject('TASK_OWNER_REJECTED')
        return cap

    def import_plan(self,plan,*,owner_context):
        return self.store.import_plan(plan,authority=self._plan_authority(owner_context))

    def reconcile_plan(self,plan,*,expected_sha256,request_id,owner_context,rollback=False):
        return self.store.reconcile_plan(plan,expected_sha256=expected_sha256,request_id=request_id,
            authority=self._plan_authority(owner_context),rollback=rollback)

    def plan_revision_status(self,plan_id,*,owner_context):
        return self.store.plan_revision_status(plan_id,authority=self._plan_authority(owner_context))

    def plan_ready(self,plan_id,*,owner_context):
        return self.store.plan_ready(plan_id,authority=self._plan_authority(owner_context))

    def plan_source_ready(self,plan_id,*,owner_context):
        return self.store.plan_source_ready(plan_id,authority=self._plan_authority(owner_context))

    def record_plan_source_evidence(self,plan_id,record,*,expected_source_sha256,owner_context):
        return self.store.record_plan_source_evidence(plan_id,record,expected_source_sha256=expected_source_sha256,
            authority=self._plan_authority(owner_context))

    def admit_plan_window(self,plan_id,*,owner_context):
        return self.store.admit_plan_window(plan_id,authority=self._plan_authority(owner_context))

    def record_plan_evidence(self,plan_id,record,*,owner_context):
        return self.store.record_plan_evidence(plan_id,record,authority=self._plan_authority(owner_context))

    @staticmethod
    def _ordinary_scope_ids(value):
        if value is None:return None  # Existing standalone services have no attached runtime scope.
        if (type(value)is not tuple or not value or len(value)>MAX_TASKS
                or any(type(ident)is not str for ident in value)):
            raise ServiceError('ORDINARY_GRANT_SCOPE_REJECTED',status=403)
        try:ids=tuple(stable_id(ident)for ident in value)
        except ServiceError:
            raise ServiceError('ORDINARY_GRANT_SCOPE_REJECTED',status=403)from None
        if len(set(ids))!=len(ids):raise ServiceError('ORDINARY_GRANT_SCOPE_REJECTED',status=403)
        return ids

    @property
    def ordinary_grant_ids(self):
        return self._ordinary_grant_scope[0]

    @ordinary_grant_ids.setter
    def ordinary_grant_ids(self,value):
        self.bind_ordinary_grants(value)

    def bind_ordinary_grants(self,value, *, projects=None):
        ids=self._ordinary_scope_ids(value)
        # Freeze applicability at binding time, so a deleted/rebound row cannot
        # turn a selected same-project ambiguity into a unique survivor. Keep
        # independent projects independent when one registration disappears.
        if projects is None:
            bindings=[]
            for ident in ids or ():
                grant=self.store.grant(ident)
                project=grant.get('project_id')if type(grant)is dict else None
                bindings.append((ident,project if type(project)is str else None))
        else:
            if (ids is None or type(projects)is not tuple or len(projects)!=len(ids)
                    or any(type(pair)is not tuple or len(pair)!=2 for pair in projects)):
                raise ServiceError('ORDINARY_GRANT_SCOPE_REJECTED',status=403)
            for ident,pair in zip(ids,projects):
                if pair[0]!=ident or type(pair[1])is not str:
                    raise ServiceError('ORDINARY_GRANT_SCOPE_REJECTED',status=403)
                try:stable_id(pair[1])
                except ServiceError:
                    raise ServiceError('ORDINARY_GRANT_SCOPE_REJECTED',status=403)from None
            bindings=projects
        self._ordinary_grant_scope=(ids,tuple(bindings))

    def capabilities(self, *, storage=None):
        """Read server readiness only; never authorize a task or probe a provider.

        The trusted runtime callback reports worker_runtime/provider availability.
        Per-task grants and writer evidence remain admission-time requirements.
        """
        def capability(available, reason=None, status=None, classification='unknown'):
            return {'available':available,'status':status or ('available' if available else 'unavailable'),
                    'reason_code':reason,'classification':classification}
        if storage is None:storage=self.store.storage_status()
        runtime={}
        if self.runtime_status is not None:
            try:
                reported=self.runtime_status()
                if type(reported)is dict:runtime=reported
            except Exception:
                pass  # A failed observer cannot confer readiness.
        result={}
        for key,reason in (('worker_runtime','TASK_WORKER_UNCONFIGURED'),('provider','TASK_PROVIDER_UNAVAILABLE')):
            reported=runtime.get(key)
            ready=type(reported)is dict and reported.get('available') is True
            classification=reported.get('classification') if type(reported)is dict else None
            if type(classification)is not str or classification not in {'synthetic_fixture','real','unknown'}:
                classification='unknown'
            # Runtime details/paths are private; expose only fixed reason codes.
            result[key]=capability(ready,None if ready else reason,classification=classification)
        reason=storage.get('reason_code') if type(storage)is dict else 'TASK_STORAGE_STATUS_UNAVAILABLE'
        storage_ready=type(storage)is dict and storage.get('state')=='ready'
        history_ready=storage_ready or reason in {'LOW_SPACE','SPACE_UNAVAILABLE'}
        result['task_drafts']=capability(self.annotations is not None,
            None if self.annotations is not None else 'WORKBENCH_UNAVAILABLE')
        result['task_history']=capability(history_ready,None if history_ready else reason or 'TASK_STORAGE_BLOCKED')
        execution_reason=None
        for ready,blocked_reason in (
                (bool(self.test_gate),'EXECUTION_DISABLED'),
                (self.writer_check_configured,'TASK_WRITER_CHECK_UNCONFIGURED'),
                (result['worker_runtime']['available'],result['worker_runtime']['reason_code']),
                (result['provider']['available'],result['provider']['reason_code']),
                (storage_ready,reason or 'TASK_STORAGE_BLOCKED')):
            if not ready:
                execution_reason=blocked_reason
                break
        runtime_classes={result[key]['classification'] for key in ('worker_runtime','provider')}
        execution_class=('synthetic_fixture' if 'synthetic_fixture' in runtime_classes else
                         'real' if runtime_classes=={'real'} else 'unknown')
        result['annotation_execution']=capability(execution_reason is None,execution_reason,
            'disabled' if not self.test_gate else None,classification=execution_class)
        ordinary_reason=None
        for ready,reason in ((self.ordinary_enabled,'ORDINARY_EXECUTION_DISABLED'),
                (self.writer_check_configured,'TASK_WRITER_CHECK_UNCONFIGURED'),
                (result['worker_runtime']['available'],result['worker_runtime']['reason_code']),
                (result['provider']['available'],result['provider']['reason_code']),
                (storage_ready,reason or 'TASK_STORAGE_BLOCKED')):
            if not ready:ordinary_reason=reason;break
        result['ordinary_execution']=capability(ordinary_reason is None,ordinary_reason,classification=execution_class)
        return result

    def _ordinary_project(self,project_id):
        stable_id(project_id);resolver=self.projects._resolver();resolver._check_registry()
        eligible,_=self.projects._management_context(resolver)
        if project_id not in eligible:raise ServiceError('TASK_PROJECT_REJECTED',status=403)
        return resolver,resolver.projects[project_id]

    def _ordinary_grant(self,resolver,project, *, live=True):
        from .ordinary_tasks import validate_grant_metadata
        selected,selected_projects=self._ordinary_grant_scope
        candidates=[];expired=False;now=time.time()
        if selected is None:
            records=self.store.grants(project_id=project['id'])
        else:
            records=[]
            for ident,bound_project in selected_projects:
                if bound_project is None:
                    raise ServiceError('ORDINARY_PROJECT_GRANT_REQUIRED',status=403)
                if bound_project!=project['id']:continue
                grant=self.store.grant(ident)
                if grant is None:
                    raise ServiceError('ORDINARY_PROJECT_GRANT_REQUIRED',status=403)
                if grant.get('kind')!='ordinary_project' or grant.get('project_id')!=bound_project:
                    raise ServiceError('GRANT_BINDING_REJECTED',status=409)
                records.append(grant)
        for grant in records:
            if grant.get('kind')!='ordinary_project' or grant.get('project_id')!=project['id']:continue
            if selected is not None and grant.get('grant_id')not in selected:continue
            # Old records remain immutable history. Only definitively expired,
            # structurally valid records can be ignored; other invalid selected
            # records reject rather than falling back to another grant.
            grant=validate_grant_metadata(grant)
            if grant['expires_at']<=now:
                expired=True;continue
            grant=validate_grant(grant,live=False)
            if grant['root']!=project.get('root_path') or grant['registry_hash']!=resolver.authority['registry_hash']:
                raise ServiceError('GRANT_BINDING_REJECTED',status=409)
            candidates.append(validate_grant(grant,live=live))
        if not candidates:
            raise ServiceError('GRANT_EXPIRED'if expired else 'ORDINARY_PROJECT_GRANT_REQUIRED',status=403)
        if len(candidates)!=1:raise ServiceError('ORDINARY_PROJECT_GRANT_AMBIGUOUS',status=409)
        return candidates[0]

    @staticmethod
    def _ordinary_parent(parent,project_id,owner_context,grant=None):
        if (not parent or parent['project']!=project_id or parent['payload'].get('kind')!='ordinary_project'
                or parent['intent'].get('owner_context')!=owner_context or not parent['thread_id'] or not parent['turn_id']):
            raise ServiceError('TASK_PARENT_REJECTED',status=409)
        if grant and (parent['intent']['grant']['root_identity']!=grant['root_identity']
                      or parent['intent']['grant']['effect']!='read_only'):
            raise ServiceError('TASK_PARENT_REJECTED',status=409)
        if parent['status']=='checks_complete' and parent['provider_status']=='completed' and not parent['cancel']:
            return ['idle_continue']
        if parent['status']in {'running','waiting_input','waiting_approval','validating'} and not parent['cancel']:
            return ['busy_feedback']
        return []

    def ordinary_context(self,project_id, *, owner_context,parent_task_id=None):
        resolver,project=self._ordinary_project(project_id)
        capabilities=self.capabilities();ready=capabilities['ordinary_execution']['available']
        reason=capabilities['ordinary_execution']['reason_code'];grant=None
        if ready:
            try:
                grant=self._ordinary_grant(resolver,project)
                if not self.writer_check(grant):
                    raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
            except ServiceError as error:ready=False;reason=error.code
        parents=[]
        for parent in self.store.tasks():
            try:allowed=self._ordinary_parent(parent,project_id,owner_context,grant)
            except ServiceError:continue
            parents.append({**self.public(parent),'allowed_modes':allowed})
        if parent_task_id is not None:
            stable_id(parent_task_id)
            if not any(parent['id']==parent_task_id for parent in parents):
                raise ServiceError('TASK_PARENT_REJECTED',status=409)
        draft=self.store.ordinary_draft(owner_context,project_id,parent_task_id)
        resolver._check_registry()
        return {'project_id':project_id,'name':project['name'],'execution_location':project['name']+' · 已注册文件只读范围',
                'input_version':grant['input_version']if grant else None,'allowed_effects':['read_only'],
                'ready':ready,'reason_code':reason,'capabilities':capabilities,'parents':parents,'draft':draft,
                'draft_scope':digest({'owner':owner_context,'project':project_id,'parent':parent_task_id})}

    def save_ordinary_draft(self,command, *, owner_context,authorize):
        from .ordinary_tasks import validate_spec
        fields={'request_id','expected_revision','project_id','parent_task_id','value'}
        if type(command)is not dict or set(command)!=fields:raise ServiceError('ORDINARY_DRAFT_INVALID')
        resolver,_=self._ordinary_project(command['project_id'])
        if command['value']is not None:
            spec=validate_spec(command['value'],draft=True)
            if spec['project_id']!=command['project_id'] or spec['parent_task_id']!=command['parent_task_id']:
                raise ServiceError('ORDINARY_DRAFT_BINDING_REJECTED',status=409)
        if command['parent_task_id']is not None:
            self._ordinary_parent(self.store.task(command['parent_task_id']),command['project_id'],owner_context)
        def check():
            authorize();resolver._check_registry()
            current,_=self._ordinary_project(command['project_id'])
            if current.authority!=resolver.authority:raise ServiceError('TASK_SCOPE_CHANGED',status=409)
        return self.store.save_ordinary_draft(command,owner_context,check)

    def submit_ordinary(self,payload, *, owner_context,authorize):
        from .ordinary_tasks import validate_spec,prompt
        spec=validate_spec(payload);authorize()
        resolver,project=self._ordinary_project(spec['project_id'])
        old=self.store.task(spec['request_id'])
        if old:
            if old['payload']!=spec:raise ServiceError('TASK_REQUEST_CONFLICT',status=409)
            if old['intent'].get('owner_context')!=owner_context:raise ServiceError('TASK_OWNER_REJECTED',status=403)
            if old['intent'].get('normal_dispatch_budget') or spec['request_id']in self.__normal_budgets:
                binding=self.__normal_budgets.get(spec['request_id'])
                if binding is None or old['intent'].get('normal_dispatch_budget')!=binding.marker(spec['request_id'],old['intent']['grant']):
                    raise ServiceError('NORMAL_DISPATCH_BINDING_REQUIRED',status=403)
            authorize();return {'task':self.public(old),'replayed':True}
        caps=self.capabilities()
        if not caps['ordinary_execution']['available']:
            raise ServiceError(caps['ordinary_execution']['reason_code'],status=503)
        selected_scope=self._ordinary_grant_scope
        grant=self._ordinary_grant(resolver,project)
        if spec['input_version']!=grant['input_version']:raise ServiceError('ORDINARY_INPUT_CHANGED',status=409)
        if not self.writer_check(grant):raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
        parent=self.store.task(spec['parent_task_id'])if spec['parent_task_id'] else None
        if parent and spec['mode']not in self._ordinary_parent(parent,spec['project_id'],owner_context,grant):
            raise ServiceError('TASK_PARENT_NOT_IDLE',status=409)
        if spec['parent_task_id']and parent is None:raise ServiceError('TASK_PARENT_REJECTED',status=409)
        intent={'grant':grant,'grant_hash':digest(grant),'task_spec':copy.deepcopy(spec),
                'registry_hash':resolver.authority['registry_hash'],'previous_thread':parent['thread_id']if parent else None,
                'owner_context':owner_context,'writer_status':'verified','prompt_sha256':hashlib.sha256(prompt(spec,input_files=grant['input_files']).encode()).hexdigest()}
        if spec['request_id']in self.__normal_budgets:
            binding=self.__normal_budgets[spec['request_id']]
            if binding is None:raise ServiceError('NORMAL_DISPATCH_BINDING_REQUIRED',status=403)
            intent['normal_dispatch_budget']=binding.marker(spec['request_id'],grant)
        if parent:intent['parent_binding']={'thread_id':parent['thread_id'],'turn_id':parent['turn_id']}
        def check():
            authorize();resolver._check_registry()
            current,current_project=self._ordinary_project(spec['project_id'])
            if (self._ordinary_grant_scope!=selected_scope or current.authority!=resolver.authority
                    or self._ordinary_grant(current,current_project)!=grant):
                raise ServiceError('GRANT_VERSION_STALE',status=409)
            if not self.writer_check(grant):raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
            if parent:
                latest=self.store.task(parent['id'])
                if (spec['mode']not in self._ordinary_parent(latest,spec['project_id'],owner_context,grant)
                        or {'thread_id':latest['thread_id'],'turn_id':latest['turn_id']}!=intent['parent_binding']):
                    raise ServiceError('TASK_PARENT_NOT_IDLE',status=409)
        task,replayed=self.store.admit(spec,intent,authorize=check)
        return {'task':self.public(task),'replayed':replayed}

    @staticmethod
    def _draft(row):
        command=row['command']
        return {'annotation_id':row['request_id'],'revision':row['revision'],
                'command_hash':row['payload_hash'],'binding':copy.deepcopy(command['binding']),
                'task_spec':copy.deepcopy(command['task_spec']),'status':'draft',
                'material_state':row.get('material_state','current'),
                'execution_allowed':False}

    def create_draft(self,command, *, backend_instance,context=None,authorize=lambda:None,actor='owner_session'):
        # Draft scope is owner text, never a grant or a filesystem capability.
        try:
            command=validate_command(command)
            if 'task_spec'not in command:raise ValueError('task specification required')
        except (ValueError,TypeError,UnicodeError):raise ServiceError('INVALID_TASK_DRAFT')from None
        saved=self.annotations.save(command,backend_instance=backend_instance,context=context,authorize=authorize,actor=actor)
        return {'draft':self._draft(saved['receipt']),'receipt':saved['receipt'],
                'replayed':saved['replayed'],'outcome':saved['outcome']}

    def drafts(self):
        annotations=self.annotations.annotations()
        capabilities=self.capabilities()
        return {'drafts':[self._draft(row)for row in annotations['records']if 'task_spec'in row['command']],
                'revision':annotations['revision'],'classification':annotations['classification'],
                'execution_enabled':capabilities['annotation_execution']['available'],'capabilities':capabilities}

    def draft(self,ident):
        stable_id(ident)
        row=self.annotations.receipt(ident)['receipt']
        if 'task_spec'not in row['command']:raise ServiceError('TASK_DRAFT_NOT_FOUND',status=404)
        # Reuse the preview service's historical-current classification.
        for item in self.drafts()['drafts']:
            if item['annotation_id']==ident:return {'draft':item}
        raise ServiceError('TASK_DRAFT_NOT_FOUND',status=404)

    def submit_registered_business(self,payload,*,registration_sha256,owner_context='trusted_local_owner',authorize=None):
        """Trusted local enrollment only; the HTTP submit route cannot call this.

        A source-based writing task has no fabricated image or annotation. Its
        frozen Root registration and real source inputs replace those bindings.
        """
        fields={'kind','request_id','project_id','grant_id','grant_version','mode','parent_task_id','specification_sha256'}
        if type(payload)is not dict or set(payload)!=fields or payload['kind']!='story_exchange':
            raise ServiceError('INVALID_BUSINESS_TASK_REQUEST')
        for key in ('request_id','project_id','grant_id'):stable_id(payload[key])
        if type(payload['grant_version'])is not int or payload['grant_version']<1 or payload['mode']!='new' or payload['parent_task_id']is not None:
            raise ServiceError('INVALID_BUSINESS_TASK_REQUEST')
        if authorize is not None:authorize()
        existing=self.store.task(payload['request_id'])
        if existing:
            if existing['payload']!=payload:raise ServiceError('TASK_REQUEST_CONFLICT',status=409)
            return {'task':self.public(existing),'replayed':True}
        resolver=self.projects._resolver();resolver._check_registry()
        project=resolver.projects.get(payload['project_id'])
        if not project or not PreviewService._eligible(project):raise ServiceError('TASK_PROJECT_REJECTED',status=403)
        value=self.store.grant(payload['grant_id'])
        grant=validate_grant(value,live=True)
        if grant.get('kind')!='story_exchange':raise ServiceError('GRANT_BINDING_REJECTED',status=403)
        scope=grant['story_exchange']
        if (grant['task_id']!=payload['request_id']or grant['project_id']!=payload['project_id']or grant['version']!=payload['grant_version']
                or scope['canonical_root']!=project['root_path']or scope['registry_hash']!=resolver.authority['registry_hash']
                or scope['specification_sha256']!=payload['specification_sha256']or scope['registration_sha256']!=registration_sha256):
            raise ServiceError('GRANT_BINDING_REJECTED',status=403)
        from .native_business import NativeBusinessAdapter
        from .story_exchange import prompt
        from .story_promotion import source_snapshot
        NativeBusinessAdapter(grant['root'],hub_root=self.store.root,grant=grant)  # No process or model.
        source_snapshot(grant,expected=True)
        writer=self.writer_check(grant)
        if not writer:raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
        text=prompt(grant)
        intent={'grant':grant,'grant_hash':digest(grant),'registry_hash':scope['registry_hash'],
                'task_spec':copy.deepcopy(grant['task_spec']),'source_binding':copy.deepcopy(scope['source_preconditions']),
                'checks':grant['checks'],'thread_source':'hub_owned_app_server','previous_thread':None,
                'owner_context':owner_context,'writer_status':'verified','prompt_sha256':hashlib.sha256(text.encode()).hexdigest()}
        if type(writer)is dict:intent['writer_evidence']=copy.deepcopy(writer)
        resolver._check_registry()
        if authorize is not None:authorize()
        task,replayed=self.store.admit(copy.deepcopy(payload),intent,authorize=authorize)
        return {'task':self.public(task),'replayed':replayed}

    def submit(self,payload, *, owner_context='trusted_local_owner', authorize=None):
        if type(payload)is dict and payload.get('kind')=='ordinary_project':
            return self.submit_ordinary(payload,owner_context=owner_context,authorize=authorize or (lambda:None))
        if type(payload)is not dict or set(payload)!=REQUEST_FIELDS:raise ServiceError('INVALID_TASK_REQUEST')
        for key in ('request_id','project_id','annotation_id','grant_id'):stable_id(payload[key])
        for key in ('annotation_revision','grant_version'):
            if type(payload[key])is not int or payload[key]<1:raise ServiceError('INVALID_TASK_REQUEST')
        if payload['mode']not in {'new','idle_continue','busy_feedback'}:raise ServiceError('INVALID_TASK_REQUEST')
        if payload['parent_task_id']is not None:stable_id(payload['parent_task_id'])
        if (payload['mode']=='new')!=(payload['parent_task_id']is None):raise ServiceError('INVALID_TASK_REQUEST')
        if not self.test_gate:raise ServiceError('EXECUTION_DISABLED',status=403)
        if authorize is not None:authorize()
        existing=self.store.task(payload['request_id'])
        if existing:
            if existing['payload']!=payload:raise ServiceError('TASK_REQUEST_CONFLICT',status=409)
            return {'task':self.public(existing),'replayed':True}
        resolver=self.projects._resolver();resolver._check_registry()
        project=resolver.projects.get(payload['project_id'])
        if not project or not PreviewService._eligible(project):
            raise ServiceError('TASK_PROJECT_REJECTED',status=403)
        grant=self.store.grant(payload['grant_id'])
        if grant is None:raise ServiceError('PROJECT_GRANT_REQUIRED',status=403)
        grant=validate_grant(grant,live=True)
        if grant.get('kind')=='story_exchange':raise ServiceError('BUSINESS_TASK_BACKEND_ONLY',status=403)
        mapped_root=grant.get('css_trial',{}).get('canonical_root',grant['root'])
        if grant['version']!=payload['grant_version'] or grant['task_id']!=payload['request_id'] or grant['project_id']!=payload['project_id'] or grant['mode']!=payload['mode'] or grant['parent_task_id']!=payload['parent_task_id'] or mapped_root!=project.get('root_path'):
            raise ServiceError('GRANT_BINDING_REJECTED',status=403)
        writer_verified=self.writer_check(grant)
        if not writer_verified and payload['mode']!='busy_feedback':raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
        annotation=self.annotations.receipt(payload['annotation_id'])['receipt']
        if not annotation or annotation['revision']!=payload['annotation_revision']:raise ServiceError('ANNOTATION_VERSION_STALE',status=409)
        command=validate_command(annotation['command'])
        if command['binding']['project_id']!=payload['project_id'] or digest(command)!=grant['annotation_hash'] or command['binding']['artifact_sha256']!=grant['image_sha256'] or command['binding']['registry_hash']!=resolver.authority['registry_hash']:raise ServiceError('ANNOTATION_GRANT_MISMATCH',status=409)
        self.annotations.validate_references(command,historical=True)
        parent=self.store.task(payload['parent_task_id'])if payload['parent_task_id']else None
        if payload['parent_task_id']:
            if not parent or parent['project']!=payload['project_id'] or parent['intent']['grant']['root_identity']!=grant['root_identity'] or not parent['thread_id']:
                raise ServiceError('TASK_PARENT_REJECTED',status=409)
            if payload['mode']=='idle_continue' and (parent['status']!='checks_complete' or parent['provider_status']!='completed'):
                raise ServiceError('TASK_PARENT_NOT_IDLE',status=409)
            if payload['mode']=='busy_feedback' and (not parent['turn_id'] or parent['status'] not in {'running','waiting_input','waiting_approval','validating','checks_complete'}):
                raise ServiceError('TASK_PARENT_NOT_BOUND',status=409)
        resolver._check_registry()
        raw=b''
        if 'css_trial'not in grant:
            sample=Path(grant['root'])/'index.html'
            fd=os.open(sample,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb')as stream:
                info=os.fstat(stream.fileno());raw=stream.read(2001)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or len(raw)>2000:raise ServiceError('PREIMAGE_REJECTED')
        if fingerprint(grant['root'])!=grant['fingerprint']:raise ServiceError('GRANT_VERSION_STALE',status=409)
        intent={'grant':grant,'grant_hash':digest(grant),'annotation':command,'registry_hash':resolver.authority['registry_hash'],'before_sample':raw.decode('utf-8'),
                'preimage':grant['fingerprint'],'checks':grant['checks'],'thread_source':'hub_owned_app_server',
                'previous_thread':parent['thread_id']if parent else None,'owner_context':owner_context,'writer_status':'verified' if writer_verified else 'unknown'}
        if parent:intent['parent_binding']={'thread_id':parent['thread_id'],'turn_id':parent['turn_id']}
        if 'css_trial'in grant:
            from .css_trial import prompt
            intent['prompt_sha256']=hashlib.sha256(prompt(command).encode()).hexdigest()
        if authorize is not None:authorize()
        task,replayed=self.store.admit(copy.deepcopy(payload),intent,authorize=authorize)
        if authorize is not None:authorize()
        return {'task':self.public(task),'replayed':replayed}

    def public(self,task):
        # Paths, grants, process/control tokens and request configuration stay server-side.
        result={key:task[key]for key in ('id','sequence','project','parent','status','thread_id','turn_id','provider_status','cancel','result','human_acceptance')}
        result.update(mode=task['payload']['mode'],annotation_id=task['payload'].get('annotation_id'),annotation_revision=task['payload'].get('annotation_revision'))
        if task['payload'].get('kind')=='story_exchange':
            result.update(kind='story_exchange',task_spec=copy.deepcopy(task['intent']['task_spec']),classification='real')
        elif task['payload'].get('kind')=='ordinary_project':
            result.update(kind='ordinary_project',task_spec=copy.deepcopy(task['intent']['task_spec']),
                classification=task['intent']['grant']['classification'],execution_location=task['project']+' · 已注册文件只读范围')
        if result.get('kind')=='ordinary_project':
            from .ordinary_tasks import validate_spec
            spec=validate_spec(task['intent']['task_spec'])
            def native_id(value):
                return value if (type(value)is str and 0<len(value)<=256 and
                    all(c.isascii() and (c.isalnum() or c in '_.:-') for c in value)) else None
            thread=native_id(task['thread_id']);turn=native_id(task['turn_id'])
            result['run_observation']={
                'schema_version':'task-run-observation/1','run_id':None,
                'run_id_reason':'NO_NATIVE_RUN_ID_PUBLISHED',
                'thread_id':thread,'turn_id':turn,'session_id':None,
                'identity_state':'observed_thread_turn' if thread and turn else 'unknown',
                'identity_reason':None if thread and turn else 'NATIVE_THREAD_OR_TURN_NOT_ASSIGNED',
                'session_reason':'SESSION_ID_NOT_PUBLISHED_BY_TASK_RECORD',
                'input_version':spec['input_version'],'parameter_sha256':digest(spec),
                'effect_declared':spec['effect'],'native_execution_proven':False,
                'tool_capability':None,'tool_reason':'NATIVE_CAPABILITY_NOT_PUBLISHED_BY_TASK_RECORD'}
        from .task_lifecycle import lifecycle
        facts=self.store.events(task['id'])
        result['lifecycle']=lifecycle(task,facts)
        result['result_review']=self.store.review_state(task,facts)
        result['result_version']=self.store.result_version(task)
        decision=result['result_review']['decision']
        result['human_acceptance']={'accept':'accepted','request_changes':'changes_requested','defer':'deferred'}.get(decision,'pending')
        result['lifecycle']['human_accepted']=decision=='accept'
        result['lifecycle']['owner_review_required']=task['status']=='checks_complete' and decision!='accept'
        result['worker_state']='lease_expired'if task['lease_until']and task['lease_until']<time.time()and task['status']not in {'checks_complete','failed','cancelled','lost','requires_reconcile'}else'current_or_terminal'
        if task['payload']['mode']=='busy_feedback':
            result['feedback']={'mechanism':'persistent_queue','applied_to_active_turn':False,'parent_binding':task['intent'].get('parent_binding'),
                                'sequence':task['sequence'],'expires_at':task['intent']['grant']['expires_at'],'writer_status':task['intent'].get('writer_status','unknown')}
        if result['result']is not None:result['result']={k:v for k,v in result['result'].items()if k!='fingerprint'}
        if result.get('kind')=='ordinary_project' and result['result']is not None:
            result['result']['response']=result['result'].pop('provider_reply','')
        return result

    def business_preview(self,ident):
        task=self.store.task(stable_id(ident))
        if not task or task['payload'].get('kind')!='story_exchange' or task['status']!='checks_complete' or not task['result'].get('source_promoted'):
            raise ServiceError('BUSINESS_PREVIEW_UNAVAILABLE',status=404)
        resolver=self.projects._resolver();resolver._check_registry();project=resolver.projects.get(task['project'])
        grant=task['intent']['grant'];scope=grant['story_exchange']
        if (not project or not PreviewService._eligible(project)or project['root_path']!=scope['canonical_root']
                or resolver.authority['registry_hash']!=scope['registry_hash']):raise ServiceError('TASK_PROJECT_REJECTED',status=403)
        from .story_exchange import read_result, _read
        from .story_promotion import source_snapshot
        output=read_result(grant)
        raw=_read(grant['root'],scope['output_path'],262144)
        if hashlib.sha256(raw).hexdigest()!=task['result']['output_sha256']:raise ServiceError('BUSINESS_PREVIEW_VERSION_STALE',status=409)
        snapshot=source_snapshot(grant,expected=False)
        binding=task['result']['source_binding']
        cards=[{'id':item['id'],**{key:item['data'][key]for key in ('title','goal','obstacle','action','choice','consequence','difference','strength','weakness')}}for item in output['objects']]
        resolver._check_registry()
        return {'kind':'story_exchange','classification':'real','binding':copy.deepcopy(binding),'cards':cards,
                'current':snapshot['workspace_sha256']==binding['workspace_sha256']and snapshot['workspace_revision']==binding['workspace_revision']}

    def options(self,annotation_id):
        stable_id(annotation_id);choices=[]
        annotation=self.annotations.receipt(annotation_id)['receipt']
        command=validate_command(annotation['command'])
        self.annotations.validate_references(command,historical=True)
        if self.test_gate:
            annotation=self.annotations.receipt(annotation_id)['receipt']
            if annotation is None:raise ServiceError('ANNOTATION_NOT_FOUND',status=404)
            for grant in self.store.grants():
                if grant.get('annotation_hash')!=digest(annotation['command']):continue
                try:validate_grant(grant)
                except ServiceError as error:
                    if error.code=='GRANT_EXPIRED':continue
                    raise
                choices.append({'request_id':grant['task_id'],'project_id':grant['project_id'],'annotation_id':annotation_id,
                    'annotation_revision':annotation['revision'],'grant_id':grant['grant_id'],'grant_version':grant['version'],
                    'mode':grant['mode'],'parent_task_id':grant['parent_task_id']})
        reference={'annotation_id':annotation_id,'revision':annotation['revision'],'command_hash':digest(command),
                   'binding':copy.deepcopy(command['binding'])}
        for key in ('design_reference','element_hint'):
            if key in command:reference[key]=copy.deepcopy(command[key])
        self.annotations.validate_references(command,historical=True)
        capabilities=self.capabilities()
        return {'choices':choices,'test_gate':self.test_gate,
                'execution_enabled':capabilities['annotation_execution']['available'],
                'capabilities':capabilities,'reference':reference}

    def list(self):
        storage=self.store.storage_status()
        capabilities=self.capabilities(storage=storage)
        readiness={'execution_enabled':capabilities['annotation_execution']['available'],'capabilities':capabilities}
        if storage['state']=='blocked' and storage['reason_code'] not in {'LOW_SPACE','SPACE_UNAVAILABLE'}:
            return {'tasks':[],'tasks_available':False,**readiness,'test_gate':self.test_gate,'storage':storage}
        try:tasks=[self.public(t)for t in self.store.tasks()]
        except ServiceError as error:
            if error.code not in {'TASK_STORE_CORRUPT','TASK_SCHEMA_UNSUPPORTED','TASK_LOCATION_REJECTED'}:raise
            storage={'state':'blocked','profile_version':None,'reason_code':error.code}
            capabilities=self.capabilities(storage=storage)
            return {'tasks':[],'tasks_available':False,'execution_enabled':False,'test_gate':self.test_gate,
                    'capabilities':capabilities,'storage':storage}
        return {'tasks':tasks,'tasks_available':True,**readiness,'test_gate':self.test_gate,'storage':storage}

    def get(self,ident, *, expected_result_version=None):
        if expected_result_version is not None and (
                type(expected_result_version) is not str or len(expected_result_version) != 64
                or any(char not in '0123456789abcdef' for char in expected_result_version)):
            raise ServiceError('INVALID_RESULT_VERSION')
        storage=self.store.storage_status()
        if storage['state']=='blocked' and storage['reason_code'] not in {'LOW_SPACE','SPACE_UNAVAILABLE'}:
            raise ServiceError('TASK_STORAGE_BLOCKED',status=503,details={'reason_code':storage['reason_code']})
        task=self.store.task(ident)
        if task is None:raise ServiceError('TASK_NOT_FOUND',status=404)
        if expected_result_version is not None and self.store.result_version(task) != expected_result_version:
            raise ServiceError('RESULT_VERSION_STALE',status=409)
        fields={'method','digest','thread_id','turn_id','source_emitted_ms','phase','action','original_status'}
        events=[]
        for row in self.store.events(ident):
            events.append({'sequence':row['sequence'],'received':row['received'],
                'kind':row['key'].split('-worker-',1)[0].split('-',1)[0],
                'data':{k:v for k,v in row['data'].items()if k in fields}})
        controls=[]
        for control in self.store.controls(ident):
            public={key:control[key] for key in ('id','version','kind','method','thread_id','turn_id','expires_at','status','permissions','allowed_decisions')}
            public['questions']=control.get('questions',[])
            if 'isBlocking'in control:public['isBlocking']=control['isBlocking']
            public['input_available']=control['kind']=='input' and 'answer'in control['allowed_decisions'] and control['status']=='pending'
            if public['status']=='pending' and public['expires_at']<=time.time():public['status']='expired'
            if task['status'] in {'checks_complete','failed','cancelled','lost','requires_reconcile'}:public['allowed_decisions']=[]
            if public['status']!='pending' or task['cancel']:
                public['allowed_decisions']=[];public['input_available']=False
            controls.append(public)
        return {'task':self.public(task),'events':events,'controls':controls,'storage':self.store.storage_status()}

    def review(self,ident,command, *, owner_context='trusted_local_owner',authorize=None):
        if authorize is not None:authorize()
        saved=self.store.review_result(ident,command,owner_context=owner_context,authorize=authorize)
        if authorize is not None:authorize()
        return {**self.get(ident),**saved}

    def _control_enabled(self,ident,owner_context,authorize):
        task=self.store.task(ident)
        ordinary=bool(task and task['intent'].get('grant',{}).get('kind')=='ordinary_project')
        if ordinary:
            if not self.ordinary_enabled:raise ServiceError('ORDINARY_EXECUTION_DISABLED',status=403)
            if authorize is None:raise ServiceError('OWNER_AUTH_REQUIRED',status=403)
            authorize()
            if task['intent'].get('owner_context')!=owner_context:raise ServiceError('TASK_CONTROL_OWNER_REJECTED',status=403)
        elif not self.test_gate:raise ServiceError('EXECUTION_DISABLED',status=403)

    def respond_control(self,ident,command, *, owner_context='trusted_local_owner',authorize=None):
        if type(command)is not dict or set(command) not in ({'control_id','version','decision'},{'control_id','version','decision','answers'}):raise ServiceError('INVALID_TASK_CONTROL')
        if type(command['decision'])is not str or command['decision']not in {'accept','decline','answer'}:raise ServiceError('TASK_CONTROL_DECISION_REJECTED',status=409)
        if ('answers'in command)!=(command['decision']=='answer'):raise ServiceError('INVALID_TASK_CONTROL')
        self._control_enabled(ident,owner_context,authorize)
        extra={'answers':command['answers']}if command['decision']=='answer'else{}
        replayed=self.store.respond_control(ident,command['control_id'],command['version'],command['decision'],owner_context=owner_context,authorize=authorize,**extra)
        return {**self.get(ident),'replayed':replayed}

    def cancel(self,ident, *, owner_context='trusted_local_owner',authorize=None):
        task=self.store.task(ident)
        if task and task['intent'].get('grant',{}).get('kind')=='ordinary_project':
            # Stop existing work even after admission has been disabled/revoked.
            if authorize is None:raise ServiceError('OWNER_AUTH_REQUIRED',status=403)
            authorize()
            if task['intent'].get('owner_context')!=owner_context:raise ServiceError('TASK_CONTROL_OWNER_REJECTED',status=403)
        elif not self.test_gate:raise ServiceError('EXECUTION_DISABLED',status=403)
        self.store.cancel(ident,owner_context=owner_context,authorize=authorize)
        if authorize is not None:authorize()
        return self.get(ident)
