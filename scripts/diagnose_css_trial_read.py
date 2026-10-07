#!/usr/bin/env python3
"""Trusted local v5-DIAG-READ-R2; refuses repetition and every execution method."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_trial as c
from hub import css_read_diagnostic as d
from hub.task_store import TaskStore
from hub.service_contract import ServiceError


def snapshot():
    return {'source':{f:{'sha256':c.sha(raw),'identity':identity}
        for f in ('progress_ui.css',*c.DOCS)for raw,identity in [c.read_file(c.ROOT/f)]},
        'trial':c.placeholder_snapshot()}


def main():
    store=TaskStore(c.HUB_ROOT);task=store.task(c.TASK)
    events=store.events(c.TASK)
    authority=next(e['data']['authority']for e in events if e['key']=='css-recovery-authority-v5')
    runtime=next(e['data']for e in events if e['key']=='css-read-diagnostic-pin-v5-r2')
    if runtime['contract']!='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v5-RUNTIME-PIN-2'or \
        runtime['original_authority_hash']!=c.digest(authority)or authority['intent_hash']!=c.digest(task['intent']):
        raise ServiceError('DIAGNOSTIC_RUNTIME_AUTHORITY_CHANGED')
    pin=runtime['pin'];c.verify_config_pin(pin)
    record=c.HUB_ROOT/c.PRIVATE/'replacement-read-diagnostic-v5-r2.json'
    response=record.with_name('replacement-read-response-v5-r2.json')
    if record.exists()or response.exists():raise ServiceError('DIAGNOSTIC_ALREADY_CONSUMED')
    if task['thread_id']!=d.THREAD or task['status']!='requires_reconcile' or task['turn_id']is not None:
        raise ServiceError('DIAGNOSTIC_TASK_CHANGED')
    before=snapshot();base=c.readonly_recovery_adapter(c.TRIAL,pin)
    class Diagnostic(type(base)):
        def _send(self,value):
            if value.get('method')not in ('initialize','initialized','thread/read'):
                raise ServiceError('DIAGNOSTIC_METHOD_REJECTED')
            if value.get('method')=='thread/read'and value['params']!={'threadId':d.THREAD,'includeTurns':False}:
                raise ServiceError('DIAGNOSTIC_BINDING_REJECTED')
            return super()._send(value)
    adapter=Diagnostic(c.TRIAL)
    result={'contract':'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v5-DIAG-READ-R2','thread_id':d.THREAD,
        'before':before,'thread_reads':0,'model_turns':0,'new_threads':0,'source_effects':0,
        'response_ref':str(response.relative_to(c.HUB_ROOT)),'status':'FAILED'}
    try:
        adapter.open();result['process_pid']=adapter.proc.pid
        result['thread_reads']=1
        try:fact=d.response_fact(adapter.call('thread/read',{'threadId':d.THREAD,'includeTurns':False})['thread'],c.TRIAL)
        except ServiceError as error:fact=d.controlled_error(error)
        result['notifications']=d.retain_response_then_notifications(response,fact,adapter.notifications,adapter.schemas)
        result['status']='READ_ONLY_DIAGNOSTIC_COMPLETE'
    except ServiceError as error:
        result['diagnostic_error']=error.code
        result['response_retained']=response.exists()
        result['observed_notification_methods']=sorted({v['method']for v in adapter.notifications})
    finally:
        adapter.close();result['closed']=True
        result['process_exit']=adapter.proc.poll()if adapter.proc else None
        try:
            result['preimages_unchanged']=snapshot()==before
            c.verify_config_pin(pin)
            result['config_unchanged']=True
        except ServiceError as error:result['after_error']=error.code
        if not result.get('preimages_unchanged')or not result.get('config_unchanged'):result['status']='FAILED'
        d.atomic_record(record,result)
    print(result['status']+'; response retained='+str(response.exists())+'; model0/newthread0/source0')
    return 0 if result['status']=='READ_ONLY_DIAGNOSTIC_COMPLETE'else 1


if __name__=='__main__':raise SystemExit(main())
