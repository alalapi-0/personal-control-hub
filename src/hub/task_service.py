"""Server-only grant routing. Client IDs and feedback are never authority."""
from __future__ import annotations

import copy
import hashlib
import os
import stat
import time
from pathlib import Path

from .service_contract import ServiceError
from .task_store import stable_id
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
    css=type(grant)is dict and grant.get('checks')==['csp_css_trial']
    if type(grant)is not dict or set(grant)!=(GRANT_FIELDS|{'css_trial'}if css else GRANT_FIELDS):raise ServiceError('INVALID_GRANT')
    for key in ('grant_id','project_id','task_id'):stable_id(grant[key])
    if grant['mode']not in {'new','idle_continue','busy_feedback'} or (grant['mode']=='new')!=(grant['parent_task_id']is None):raise ServiceError('INVALID_GRANT')
    if grant['parent_task_id']is not None:stable_id(grant['parent_task_id'])
    if type(grant['version'])is not int or grant['version']<1 or grant['source']!='verified_owner_decision':raise ServiceError('INVALID_GRANT')
    if grant['auth']!='ChatGPT' or grant['isolated_root']is not True or grant['writable_scope']!=['.'] or grant['prohibited']!=[]:
        raise ServiceError('EXACT_PROJECT_ISOLATION_UNVERIFIED',status=403)
    if type(grant['checks'])is not list or (not css and grant['checks']!=['fixture_sample']):raise ServiceError('CHECK_PLAN_REJECTED')
    if type(grant['expires_at'])not in (int,float) or not time.time()<grant['expires_at']<time.time()+86400:raise ServiceError('GRANT_EXPIRED',status=403)
    if type(grant['max_turn_seconds'])is not int or not 10<=grant['max_turn_seconds']<=120:raise ServiceError('INVALID_GRANT')
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
    def __init__(self,projects,annotations,store, *, test_gate=False, writer_check=None):
        self.projects,self.annotations,self.store=projects,annotations,store
        self.test_gate=test_gate
        # An internal lease cannot certify an unrelated writer's absence.
        self.writer_check=writer_check or (lambda _:False)

    def submit(self,payload, *, owner_context='trusted_local_owner', authorize=None):
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

    @staticmethod
    def public(task):
        # Paths, grants, process/control tokens and request configuration stay server-side.
        result={key:task[key]for key in ('id','sequence','project','parent','status','thread_id','turn_id','provider_status','cancel','result','human_acceptance')}
        result.update(mode=task['payload']['mode'],annotation_id=task['payload']['annotation_id'],annotation_revision=task['payload']['annotation_revision'])
        result['worker_state']='lease_expired'if task['lease_until']and task['lease_until']<time.time()and task['status']not in {'checks_complete','failed','cancelled','lost','requires_reconcile'}else'current_or_terminal'
        if task['payload']['mode']=='busy_feedback':
            result['feedback']={'mechanism':'persistent_queue','applied_to_active_turn':False,'parent_binding':task['intent'].get('parent_binding'),
                                'sequence':task['sequence'],'expires_at':task['intent']['grant']['expires_at'],'writer_status':task['intent'].get('writer_status','unknown')}
        if result['result']is not None:result['result']={k:v for k,v in result['result'].items()if k!='fingerprint'}
        return result

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
        return {'choices':choices,'test_gate':self.test_gate,'execution_enabled':False,'reference':reference}

    def list(self):
        storage=self.store.storage_status()
        if storage['state']=='blocked' and storage['reason_code'] not in {'LOW_SPACE','SPACE_UNAVAILABLE'}:
            return {'tasks':[],'tasks_available':False,'execution_enabled':False,'test_gate':self.test_gate,'storage':storage}
        try:tasks=[self.public(t)for t in self.store.tasks()]
        except ServiceError as error:
            if error.code not in {'TASK_STORE_CORRUPT','TASK_SCHEMA_UNSUPPORTED','TASK_LOCATION_REJECTED'}:raise
            return {'tasks':[],'tasks_available':False,'execution_enabled':False,'test_gate':self.test_gate,
                    'storage':{'state':'blocked','profile_version':None,'reason_code':error.code}}
        return {'tasks':tasks,'tasks_available':True,'execution_enabled':False,'test_gate':self.test_gate,'storage':storage}

    def get(self,ident):
        storage=self.store.storage_status()
        if storage['state']=='blocked' and storage['reason_code'] not in {'LOW_SPACE','SPACE_UNAVAILABLE'}:
            raise ServiceError('TASK_STORAGE_BLOCKED',status=503,details={'reason_code':storage['reason_code']})
        task=self.store.task(ident)
        if task is None:raise ServiceError('TASK_NOT_FOUND',status=404)
        fields={'method','digest','thread_id','turn_id','source_emitted_ms','phase','action','original_status'}
        events=[]
        for row in self.store.events(ident):
            events.append({'sequence':row['sequence'],'received':row['received'],
                'kind':row['key'].split('-worker-',1)[0].split('-',1)[0],
                'data':{k:v for k,v in row['data'].items()if k in fields}})
        controls=[]
        for control in self.store.controls(ident):
            public={key:control[key] for key in ('id','version','kind','method','thread_id','turn_id','expires_at','status','permissions','allowed_decisions')}
            if public['status']=='pending' and public['expires_at']<=time.time():public['status']='expired'
            if task['status'] in {'checks_complete','failed','cancelled','lost','requires_reconcile'}:public['allowed_decisions']=[]
            controls.append(public)
        return {'task':self.public(task),'events':events,'controls':controls,'storage':self.store.storage_status()}

    def respond_control(self,ident,command, *, owner_context='trusted_local_owner',authorize=None):
        if not self.test_gate:raise ServiceError('EXECUTION_DISABLED',status=403)
        if type(command)is not dict or set(command)!={'control_id','version','decision'}:raise ServiceError('INVALID_TASK_CONTROL')
        replayed=self.store.respond_control(ident,command['control_id'],command['version'],command['decision'],owner_context=owner_context,authorize=authorize)
        return {**self.get(ident),'replayed':replayed}

    def cancel(self,ident):
        if not self.test_gate:raise ServiceError('EXECUTION_DISABLED',status=403)
        self.store.cancel(ident);return self.get(ident)
