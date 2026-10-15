"""Trusted local story transaction bridge; model output never chooses commands."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .service_contract import ServiceError
from .workbench_store import digest

WORKSPACE='workbench/creative_assistance/workspace.json'
OBSERVATION='docs/story-system-rebuild/STORY_SYSTEM_STATE.json'


def source_snapshot(grant,*,expected):
    from .story_exchange import _read,_json
    from .task_service import root_identity
    scope=grant['story_exchange'];root=scope['canonical_root']
    if root_identity(root)!=scope['canonical_root_identity']:raise ServiceError('BUSINESS_SOURCE_IDENTITY_STALE',status=409)
    raw=_read(root,WORKSPACE,1048576);workspace=_json(raw)
    state=_read(root,OBSERVATION,1048576)
    result={'workspace_revision':workspace['revision'],'workspace_sha256':hashlib.sha256(raw).hexdigest(),
            'source_state_sha256':hashlib.sha256(state).hexdigest(),'baseline_id':workspace['baseline_id'],'dataset':workspace['dataset']}
    if expected and result!=scope['source_preconditions']:raise ServiceError('BUSINESS_SOURCE_VERSION_STALE',status=409)
    return result


def child_command(root,*args):
    """Fixed existing project entry, never model-provided shell or argv."""
    from .codex_adapter import child_environment
    root=Path(root)
    result=subprocess.run([sys.executable,'-B',str(root/'scripts/storage_runtime.py'),'story-system',*args],
        cwd=root,env=child_environment(),capture_output=True,text=True,timeout=30)
    if len(result.stdout.encode())>1048576 or len(result.stderr.encode())>16384:
        raise ServiceError('BUSINESS_CHECK_OUTPUT_CAPACITY',status=507)
    if result.returncode:
        raise ServiceError('BUSINESS_CHILD_CHECK_FAILED',status=409,details={'operation':args[0],'exit':result.returncode,
            'stdout_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
            'stderr_sha256':hashlib.sha256(result.stderr.encode()).hexdigest()})
    return json.loads(result.stdout),{'id':'story-'+args[0],'exit':0,'stdout_digest':hashlib.sha256(result.stdout.encode()).hexdigest(),
                                    'stderr_digest':hashlib.sha256(result.stderr.encode()).hexdigest()}


def promote(store,ident,*,import_path,verify_sources):
    """Root runs this only after the native worker/adapter has closed.

    A durable intent or uncertain outcome is read/reconcile only; this function
    never redispatches a turn or silently repeats a source mutation.
    """
    from .story_exchange import validate,output_result,read_result
    task=store.task(ident)
    if not task or task['payload'].get('kind')!='story_exchange':raise ServiceError('BUSINESS_PROMOTION_TASK_REJECTED',status=409)
    grant=validate(task['intent']['grant'],live=True);scope=grant['story_exchange'];root=scope['canonical_root']
    if (task['status']!='validating'or task['owner']is not None or task['cancel']or task['provider_status']!='completed'
            or not task['result'].get('business_ready')or store.grant(grant['grant_id'])!=grant):
        raise ServiceError('BUSINESS_PROMOTION_TASK_REJECTED',status=409)
    if any(e['key']=='story-promotion-intent'for e in store.events(ident)):
        raise ServiceError('BUSINESS_PROMOTION_REQUIRES_RECONCILE',status=409)
    verify_sources()  # Trusted Root checks the frozen child implementation/protected preimages.
    before=source_snapshot(grant,expected=True)
    summary=output_result(grant)
    if summary['output_sha256']!=task['result']['output_sha256']:raise ServiceError('BUSINESS_OUTPUT_VERSION_STALE',status=409)
    output=read_result(grant)
    path=Path(import_path)
    if not path.is_absolute()or path.exists()or path.parent.is_symlink():raise ServiceError('BUSINESS_IMPORT_PATH_REJECTED',status=409)
    # The local importer consumes this validated envelope; it cannot execute it.
    with path.open('x',encoding='utf-8')as out:json.dump({'revision':before['workspace_revision'],'result':output},out,ensure_ascii=False)
    store.event(ident,'story-promotion-intent',{'thread_id':task['thread_id'],'turn_id':task['turn_id'],
        'workspace_revision':before['workspace_revision'],'workspace_sha256':before['workspace_sha256'],
        'output_sha256':summary['output_sha256'],'request_id':scope['request_id'],'request_sha256':scope['request_sha256']})
    def effect():
        verify_sources();source_snapshot(grant,expected=True)
        receipt,check=child_command(root,'import-result','--input',str(path))
        snapshot=source_snapshot(grant,expected=False)
        if receipt.get('noop')or receipt.get('revision')!=before['workspace_revision']+1 or receipt['result'].get('request_id')!=scope['request_id']:
            raise ServiceError('BUSINESS_IMPORT_OUTCOME_REQUIRES_RECONCILE',status=409,outcome='UNKNOWN')
        return {'workspace_revision':snapshot['workspace_revision'],'workspace_sha256':snapshot['workspace_sha256'],
                'import_result_sha256':receipt['result']['result_sha256'],'output_refs':receipt['result']['imported'],'check':check}
    promoted=store.perform_local_promotion(ident,digest(task['result']),effect)
    checks=[promoted['check']]
    for command in ('validate','workbench-handoff'):
        _,check=child_command(root,command);checks.append(check)
    verify_sources(after=True)
    binding={'workspace_revision':promoted['workspace_revision'],'workspace_sha256':promoted['workspace_sha256'],
             'request_id':scope['request_id'],'request_sha256':scope['request_sha256'],'output_sha256':summary['output_sha256']}
    result={**task['result'],'source_promoted':True,'validation_scope':'canonical_project','source_binding':binding,
            'import_result_sha256':promoted['import_result_sha256'],'output_refs':promoted['output_refs'],'checks':checks,
            'diff':'Added exactly three unselected story candidates through the existing workspace transaction; original candidates and Canon preserved.'}
    store.finalize_local_validation(ident,digest(task['result']),result)
    return result
