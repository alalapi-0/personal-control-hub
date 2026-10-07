"""Controlled evidence for the one authorized replacement metadata diagnostic."""
import json
import os
from pathlib import Path

from .service_contract import ServiceError

THREAD = '01a1155c-2200-7f10-9e3d-4e11e051e5b5'
ALLOWED_NOTIFICATIONS = frozenset({'remoteControl/status/changed','account/updated',
    'account/rateLimits/updated','configWarning','thread/status/changed'})


def atomic_record(path, value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    if path.exists():raise ServiceError('DIAGNOSTIC_RECORD_EXISTS')
    try:
        with temporary.open('x')as stream:
            json.dump(value,stream,ensure_ascii=False,sort_keys=True,indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        if path.exists():raise ServiceError('DIAGNOSTIC_RECORD_EXISTS')
        os.link(temporary,path,follow_symlinks=False)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        try:temporary.unlink()
        except FileNotFoundError:pass


def controlled_error(error):
    details=error.details
    allowed={'method','rpc_code','error_categories','message_bytes','message_sha256','raw_message_retained'}
    categories=details.get('error_categories',[])
    code=details.get('rpc_code')
    if 'unsupported_store_operation'in categories:kind='operation_unsupported'
    elif 'method_unsupported'in categories:kind='method_unsupported'
    elif 'thread_unavailable'in categories:kind='thread_unavailable'
    elif code==-32601:kind='method_or_operation_unsupported_unclassified'
    elif 'SCHEMA'in error.code:kind='schema_rejection'
    else:kind='controlled_other_error'
    return {'method':'thread/read','thread_id':THREAD,'status':kind,'error_code':error.code,
        'details':{k:v for k,v in details.items()if k in allowed}}


def response_fact(thread, expected_cwd):
    if thread['id']!=THREAD or thread['cwd']!=str(expected_cwd):
        raise ServiceError('DIAGNOSTIC_BINDING_REJECTED')
    return {'method':'thread/read','thread_id':THREAD,'status':'metadata_read_succeeded',
        'exact_bound_cwd':True,'recoverable':'UNVERIFIED','zero_turns':'UNVERIFIED'}


def notification_fact(value, schemas):
    # The adapter already validates the full fixed-version notification schema.
    method=value['method'];params=value.get('params',{})
    definitions=schemas['ServerNotification']['oneOf']
    schema_methods={row.get('properties',{}).get('method',{}).get('enum',[None])[0]
        for row in definitions}
    if method not in ALLOWED_NOTIFICATIONS or method not in schema_methods:
        raise ServiceError('DIAGNOSTIC_NOTIFICATION_REJECTED')
    if method=='thread/status/changed'and params.get('threadId')!=THREAD:
        raise ServiceError('DIAGNOSTIC_NOTIFICATION_BINDING_REJECTED')
    if method=='remoteControl/status/changed'and params.get('status')!='disabled':
        raise ServiceError('DIAGNOSTIC_REMOTE_STATUS_REJECTED')
    return {'method':method,'thread_id':THREAD if method=='thread/status/changed'else None,
        'category':'bound_thread_status'if method=='thread/status/changed'else'schema_startup_metadata',
        'body_retained':False}


def retain_response_then_notifications(response_path, fact, notifications, schemas):
    atomic_record(response_path,fact)
    return [notification_fact(value,schemas)for value in notifications]
