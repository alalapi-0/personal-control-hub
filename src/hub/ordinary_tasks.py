"""Ordinary text requests use explicit server registrations and exact read inputs."""
from __future__ import annotations

import copy
import hashlib
import os
import stat
import time
from pathlib import Path

from .service_contract import ServiceError
from .task_store import stable_id
from .workbench_store import canonical, digest

KIND = 'ordinary_project'
SPEC_FIELDS = {'kind','request_id','project_id','objective','scope','effect','input_version','mode','parent_task_id'}
GRANT_FIELDS = {'kind','grant_id','version','project_id','root','root_identity','registry_hash','input_files',
                'input_version','effect','expires_at','max_turn_seconds','source','auth','classification'}


def _sha(value):
    return type(value)is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def validate_spec(value, *, draft=False):
    if type(value)is not dict or set(value)!=SPEC_FIELDS or value['kind']!=KIND:
        raise ServiceError('ORDINARY_SPEC_INVALID')
    for key in ('request_id','project_id'):stable_id(value[key])
    for key,limit in (('objective',2000),('scope',1000)):
        if type(value[key])is not str or not value[key].strip() or len(value[key])>limit or '\x00'in value[key]:
            raise ServiceError('ORDINARY_SPEC_INVALID',details={'field':key})
    if value['effect']!='read_only':raise ServiceError('ORDINARY_EFFECT_UNAVAILABLE',status=403)
    if not _sha(value['input_version']) and not (draft and value['input_version']is None):
        raise ServiceError('ORDINARY_INPUT_VERSION_REQUIRED',status=409)
    if type(value['mode'])is not str or value['mode']not in {'new','idle_continue','busy_feedback'}:
        raise ServiceError('ORDINARY_SPEC_INVALID')
    if value['parent_task_id']is not None:stable_id(value['parent_task_id'])
    if (value['mode']=='new')!=(value['parent_task_id']is None):raise ServiceError('ORDINARY_SPEC_INVALID')
    if len(canonical(value))>8192:raise ServiceError('TASK_PAYLOAD_CAPACITY',status=413)
    return copy.deepcopy(value)


def _input_path(value):
    if type(value)is not str or not value or len(value)>256:
        raise ServiceError('ORDINARY_INPUT_REJECTED',status=403)
    path=Path(value)
    if path.is_absolute() or '..'in path.parts or '.'in path.parts or str(path)!=value:
        raise ServiceError('ORDINARY_INPUT_REJECTED',status=403)
    if any(part in {'.git','.codex','.ssh','.aws','.env','auth.json','credentials.json'}
           or part.startswith('.env.') for part in path.parts):
        raise ServiceError('ORDINARY_INPUT_REJECTED',status=403)
    return path


def _read_input(root,path):
    """No directory traversal, symlink following, or unbounded business reads."""
    relative=_input_path(path);parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    fd=None
    try:
        for part in relative.parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent);parent=child
        fd=os.open(relative.name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=parent)
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>1048576:
            raise ServiceError('ORDINARY_INPUT_REJECTED',status=403)
        with os.fdopen(fd,'rb') as stream:
            fd=None;raw=stream.read(1048577);after=os.fstat(stream.fileno())
        named=os.stat(relative.name,dir_fd=parent,follow_symlinks=False)
        def identity(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if len(raw)>1048576 or identity(before)!=identity(after) or identity(after)!=identity(named):
            raise ServiceError('ORDINARY_INPUT_CHANGED',status=409)
        return hashlib.sha256(raw).hexdigest()
    except OSError:
        raise ServiceError('ORDINARY_INPUT_UNAVAILABLE',status=409) from None
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)


def snapshot_inputs(grant):
    from .task_service import root_identity
    if root_identity(grant['root'])!=grant['root_identity']:
        raise ServiceError('GRANT_VERSION_STALE',status=409)
    return [{'path':item['path'],'sha256':_read_input(grant['root'],item['path'])}
            for item in grant['input_files']]


def _version(grant):
    return digest({key:grant[key] for key in ('project_id','root_identity','registry_hash','input_files','effect')})


def validate_grant(value, *, live=False):
    if type(value)is not dict or set(value)!=GRANT_FIELDS or value['kind']!=KIND:
        raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
    for key in ('grant_id','project_id'):stable_id(value[key])
    if (type(value['version'])is not int or value['version']<1 or value['effect']!='read_only'
            or value['source']!='verified_owner_decision' or value['auth']!='ChatGPT'
            or type(value['classification'])is not str or value['classification']not in {'synthetic_fixture','real'}):
        raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
    root=Path(value['root']) if type(value['root'])is str else Path('.')
    if not root.is_absolute() or '..'in root.parts or not _sha(value['root_identity']) or not _sha(value['registry_hash']):
        raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
    if type(value['expires_at'])not in (int,float) or not time.time()<value['expires_at']<time.time()+86400:
        raise ServiceError('GRANT_EXPIRED',status=403)
    if type(value['max_turn_seconds'])is not int or not 10<=value['max_turn_seconds']<=600:
        raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
    files=value['input_files']
    if type(files)is not list or not 1<=len(files)<=16:raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
    seen=set()
    for item in files:
        if type(item)is not dict or set(item)!={'path','sha256'} or not _sha(item['sha256']):
            raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
        _input_path(item['path'])
        if item['path']in seen:raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
        seen.add(item['path'])
    if value['input_version']!=_version(value):raise ServiceError('INVALID_ORDINARY_GRANT',status=403)
    if live and snapshot_inputs(value)!=files:raise ServiceError('ORDINARY_INPUT_CHANGED',status=409)
    return copy.deepcopy(value)


def make_grant(root,project_id,registry_hash,grant_id,*,input_paths=('AGENTS.md','README.md'),
               classification='synthetic_fixture',version=1,max_turn_seconds=120):
    """Trusted local registration helper, deliberately absent from HTTP."""
    from .task_service import root_identity
    grant={'kind':KIND,'grant_id':grant_id,'version':version,'project_id':project_id,'root':str(Path(root).absolute()),
           'root_identity':root_identity(root),'registry_hash':registry_hash,'input_files':[],
           'effect':'read_only','expires_at':time.time()+3600,'max_turn_seconds':max_turn_seconds,
           'source':'verified_owner_decision','auth':'ChatGPT','classification':classification}
    grant['input_files']=[{'path':str(path),'sha256':_read_input(grant['root'],str(path))} for path in input_paths]
    grant['input_version']=_version(grant)
    return validate_grant(grant,live=True)


def check_registration(grant,control_root):
    """Recheck Hub metadata before any excluded source path can be probed."""
    from .project_service import ProjectService
    service=ProjectService(control_root);resolver=service._resolver();resolver._check_registry()
    eligible,_=service._management_context(resolver)
    project=resolver.projects.get(grant['project_id'])
    if (grant['project_id']not in eligible or not project or project.get('root_path')!=grant['root']
            or resolver.authority['registry_hash']!=grant['registry_hash']):
        raise ServiceError('TASK_SCOPE_CHANGED',status=403)


def prompt(spec, *, input_files=()):
    spec=validate_spec(spec)
    return ('Perform this ordinary read-only project task. Read only the explicitly registered input files '
            'available under this workspace. Do not modify files, use the network, request broader permissions, '
            'or operate other projects. Return the answer as text; report missing inputs explicitly.\n'
            'Project: '+spec['project_id']+'\nInput version: '+spec['input_version']+'\n'
            'Registered inputs: '+', '.join(str(_input_path(item['path'])) for item in input_files)+'\n'
            'Objective: '+spec['objective']+'\nScope: '+spec['scope']+'\n')
