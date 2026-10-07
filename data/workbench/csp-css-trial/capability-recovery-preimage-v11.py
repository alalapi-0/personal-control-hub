"""One evidence-only load of the completed, exact owner-bound CSS turn."""
import json
import os
import queue
import re
import time
import threading
from pathlib import Path
import yaml

from . import css_trial as c,css_process_config as p
from .codex_adapter import AppServerAdapter,PERMISSION_PROFILE,REGISTERED_SERVERS,profile_arguments,NOTIFICATIONS,RESPONSES
from .css_read_diagnostic import atomic_record,notification_fact,ALLOWED_NOTIFICATIONS
from .service_contract import ServiceError
from .task_store import TaskStore
from .workbench_store import digest

CONTRACT='HUB-LWB-2.3-CSP-CAPABILITY-RECOVERY-GOV-v10'
THREAD='01a1155c-2200-7f10-9e3d-4e11e051e5b5'
TURN='01a1157b-f571-7c91-a799-6a3a56a20a19'
ALLOWED=frozenset({'initialize','config/read','thread/read','thread/resume','mcpServerStatus/list','experimentalFeature/list','command/exec'})
INTENT_HASH='bcef1ef45fee92309f469d2a5b3eed76df2f7cc2496cff0b4cc2ce37f676d699'
GRANT_HASH='d116bbbc46ad678876f77e2e2a6437923d2efa5722fb58aa154f7673626ab21c'


def unexpired(authority):
    now=time.time();created=authority.get('created_at');expiry=authority.get('expires_at')
    if type(created)not in (int,float)or type(expiry)not in (int,float)or not created<=now<expiry<=created+900:
        raise ServiceError('CSS_RECOVERY_AUTHORITY_EXPIRED')


def authority_check(authority):
    unexpired(authority)
    expected={'contract':CONTRACT,'task_id':c.TASK,'thread_id':THREAD,'turn_id':TURN,
        'model_turns_allowed':0,'total_thread_limit':2,'prior_resumes':3,'total_resume_limit':4,'total_model_turn_limit':1,
        'intent_hash':INTENT_HASH,'grant_hash':GRANT_HASH,'profile_arguments':list(profile_arguments(c.TRIAL)),
        'helper_wall_seconds':120,'renewal_allowed':False,'auth_mode':'ChatGPT'}
    if any(type(authority.get(k))is not type(v)or authority.get(k)!=v for k,v in expected.items()):
        raise ServiceError('CSS_RECOVERY_AUTHORITY_REJECTED')
    registration=json.loads((c.HUB_ROOT/c.PRIVATE/'capability-recovery-registration-v10.json').read_text())
    if authority['approved_candidate']!=registration['candidate_sha256']or authority['approved_evidence']!=registration['evidence_sha256']or \
        any(authority[k]!=registration[k]for k in ('files','task_hash','trial','protected_pin','source','auth_identity','history_files')):
        raise ServiceError('CSS_RECOVERY_AUTHORITY_REJECTED')
    for name,h in {**authority['files'],**authority['history_files']}.items():
        if c.sha((c.HUB_ROOT/name).read_bytes())!=h:raise ServiceError('CSS_RECOVERY_CANDIDATE_CHANGED')
    text=(c.HUB_ROOT/'STATE.yaml').read_text()
    match=re.search(r'^linux_visual_workbench:\n.*?(?=^[^ \n#][^\n]*:\n|\Z)',text,re.M|re.S)
    if match is None:raise ServiceError('CSS_RECOVERY_AUTHORITY_REJECTED')
    state=yaml.safe_load(match[0])['linux_visual_workbench'];contract=state['contract']
    owner=state.get('authorization',{}).get('conditional_pilot_write_grant',{})
    if contract.get('id')!=CONTRACT or contract.get('decision')!='APPROVE'or contract.get('status')!='APPROVED_EVIDENCE_ONLY'or \
        owner.get('state')!='active_exact_css_trial'or owner.get('task_id')!=c.TASK or \
        owner.get('grant_id')!='csp-css-trial-grant-v1'or owner.get('project_id')!='computer-study-plan':
        raise ServiceError('CSS_RECOVERY_AUTHORITY_REJECTED')
    if state.get('trial_registration',{}).get('resume_calls')!=3:
        raise ServiceError('CSS_RECOVERY_COUNTER_CHANGED')


def history_check(events,authority):
    # Exactly two older RPC resumes plus the separately retained v9 intent/result.
    if sum(e['data'].get('method')=='thread/resume'for e in events)!=2:
        raise ServiceError('CSS_RECOVERY_COUNTER_CHANGED')
    prior=json.loads((c.HUB_ROOT/c.PRIVATE/'capability-recovery-result-v9.json').read_text())
    if prior.get('resume_calls_consumed')!=1 or prior.get('status')!='PARTIAL_BLOCKED'or \
        prior.get('error')!='DIAGNOSTIC_NOTIFICATION_REJECTED':raise ServiceError('CSS_RECOVERY_HISTORY_CHANGED')
    if authority['prior_resumes']!=3:raise ServiceError('CSS_RECOVERY_COUNTER_CHANGED')


def source_snapshot():
    return {f:{'sha256':c.sha(raw),'identity':identity}for f in ['progress_ui.css',*c.DOCS]
        for raw,identity in [c.read_file(c.ROOT/f,mode=0o644)]}


def auth_identity():
    info=c.CREDENTIAL_PATH.lstat()
    return [getattr(info,k)for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid')]


def mapping_check(grant):
    from .project_service import ProjectService
    from .preview_service import PreviewService
    from .task_service import root_identity
    from .readonly_preview import ReadOnlyPreview
    from .design_service import DesignService
    from .design_store import DesignStore
    projects=ProjectService(c.HUB_ROOT);resolver=projects._resolver();resolver._check_registry()
    project=resolver.projects.get('computer-study-plan',{})
    if project.get('root_path')!=str(c.ROOT)or resolver.authority['registry_hash']!=grant['css_trial']['registry_hash']or not PreviewService._eligible(project):
        raise ServiceError('CSS_RECOVERY_MAPPING_CHANGED')
    root_identity(c.ROOT)
    result=c.subprocess.run(['git','rev-parse','HEAD'],cwd=c.ROOT,capture_output=True,text=True,timeout=5)
    if result.returncode or result.stdout.strip()!=c.HEAD:raise ServiceError('CSS_RECOVERY_SOURCE_CHANGED')
    ReadOnlyPreview(c.HUB_ROOT,projects,DesignService(DesignStore(c.HUB_ROOT,'data/design_governance/design-store.json'))).check()


def probe_argv():
    paths={'canonical':str(c.ROOT/'progress_ui.css'),'credential':str(c.CREDENTIAL_PATH),
        'parent_canary':str(c.PARENT_CANARY),'tmp':str(c.OUTSIDE_PROBE),'sibling':str(c.HUB_ROOT/c.PRIVATE/'before.css'),
        'symlink':str(Path('/proc/self/root')/str(c.ROOT/'progress_ui.css').lstrip('/'))}
    script="""import os,json,hashlib,socket
fd=os.open('progress_ui.css',os.O_RDONLY|os.O_NOFOLLOW)
raw=os.read(fd,1048576);os.close(fd)
p={'css_sha256':hashlib.sha256(raw).hexdigest(),'trial_bytes_read':len(raw),'denials':{},'protected_bytes_read':0}
for name,path in PATHS.items():
 for mode,flags in [('read',os.O_RDONLY),('write',os.O_WRONLY)]:
  try:
   fd=os.open(path,flags|os.O_NOFOLLOW);os.close(fd);p['denials'][name+'_'+mode]=0
  except OSError as e:p['denials'][name+'_'+mode]=e.errno
try:
 fd=os.open(PARENT,os.O_RDONLY|os.O_NOFOLLOW);os.close(fd);p['parent_open_errno']=0
except OSError as e:p['parent_open_errno']=e.errno
try:os.listdir(PARENT);p['parent_list_errno']=0
except OSError as e:p['parent_list_errno']=e.errno
try:s=socket.socket();s.close();p['network_errno']=0
except OSError as e:p['network_errno']=e.errno
print(json.dumps(p,sort_keys=True))
""".replace('PATHS',repr(paths)).replace('PARENT',repr(str(c.TRIAL.parent)))
    return ['/usr/bin/python3','-B','-c',script]


def probe_fact(result):
    try:fact=json.loads(result['stdout'])
    except (ValueError,KeyError,TypeError):raise ServiceError('CSS_RECOVERY_PROBE_UNVERIFIED')from None
    keys={'css_sha256','trial_bytes_read','denials','protected_bytes_read','parent_open_errno','parent_list_errno','network_errno'}
    names={n+'_'+m for n in ('canonical','credential','parent_canary','tmp','sibling','symlink')for m in ('read','write')}
    if set(fact)!=keys or result['exitCode']!=0 or fact['css_sha256']!=c.AFTER_SHA or \
        type(fact['trial_bytes_read'])is not int or fact['trial_bytes_read']!=143428 or fact['protected_bytes_read']!=0 or \
        type(fact['denials'])is not dict or set(fact['denials'])!=names or \
        any(type(v)is not int or v not in (1,2,13,30)for v in fact['denials'].values())or \
        any(type(fact[n])is not int or fact[n]not in (1,2,13,30)for n in ('parent_open_errno','parent_list_errno'))or \
        fact['network_errno']not in (1,13):raise ServiceError('CSS_RECOVERY_PROBE_UNVERIFIED')
    return fact


def loaded_six_mcp(observations):
    fact=next((f for f in reversed(observations)if f.get('method')=='mcpServerStatus/list'),None)
    if not fact or fact.get('unknown_server_count')!=0 or fact.get('thread_id')!=THREAD or fact.get('has_next_page')is not False or \
        len(fact['servers'])!=6 or sorted(row['name']for row in fact['servers'])!=sorted(REGISTERED_SERVERS):
        raise ServiceError('CSS_RECOVERY_SIX_MCP_UNVERIFIED')
    return fact['servers']


def final_capability_state(a,record):
    # The helper is already closed and both readers have joined. Consume only
    # schema-validated notifications; never send a response or issue another RPC.
    if a.proc is None or a.proc.poll()!=0 or any(reader is not None and reader.is_alive()for reader in
        (getattr(a,'reader',None),getattr(a,'error_reader',None)))or getattr(a,'failed',None)not in (None,'CODEX_STREAM_EOF'):
        raise ServiceError('CSS_RECOVERY_TERMINAL_STREAM_UNVERIFIED')
    notices=record.setdefault('notifications',[])
    pending=list(a.notifications);a.notifications.clear()
    for _ in range(256):
        try:value=a.inbox.get_nowait()
        except queue.Empty:break
        if 'method'not in value or 'id'in value:
            if getattr(a,'pending_frame_observation',None)is not None:a.pending_frame_observation(value)
            raise ServiceError('CSS_RECOVERY_TERMINAL_FRAME_REJECTED')
        try:
            a._validate('ServerNotification',value)
            if value['method']in NOTIFICATIONS:a._validate(NOTIFICATIONS[value['method']],value.get('params'))
        except ServiceError:
            a._notification_observation(value,'schema_rejected')
            raise
        a._notification_observation(value,'validated')
        if value['method']=='remoteControl/status/changed':
            a.remote_control_status=value['params']['status']
            a._record_capability({'kind':'notification','method':value['method'],'status':a.remote_control_status})
        elif value['method']=='mcpServer/startupStatus/updated':
            a.mcp_startup_seen=True
            a._record_capability({'kind':'notification','method':value['method'],'unexpected_startup':True})
        pending.append(value)
    else:raise ServiceError('CSS_RECOVERY_NOTIFICATION_CAPACITY')
    for value in pending:notices.append(notification_fact(value,a.schemas))
    record['final_remote_control_status']=a.remote_control_status;record['final_mcp_startup_seen']=a.mcp_startup_seen
    if a.remote_control_status!='disabled'or a.mcp_startup_seen or a.denials:
        raise ServiceError('CSS_RECOVERY_FINAL_CAPABILITY_REJECTED')
    return True


def adapter(pin,folder,authority):
    class EvidenceAdapter(AppServerAdapter):
        def open(self):
            self.wall_deadline=time.monotonic()+100;self.wall_exceeded=False
            self.close_lock=threading.Lock()
            self.watchdog=threading.Timer(108,self._deadline_close);self.watchdog.daemon=True;self.watchdog.start()
            return super().open()
        def _deadline_close(self):
            self.wall_exceeded=True
            self.close()
        def close(self):
            lock=getattr(self,'close_lock',None)
            if lock is None:return super().close()
            with lock:return super().close()
        def _wall_check(self):
            if getattr(self,'wall_exceeded',False)or time.monotonic()>=getattr(self,'wall_deadline',float('inf')):
                raise ServiceError('CSS_RECOVERY_WALL_LIMIT')
        def _notification_observation(self,value,outcome):
            from .notification_metadata import observation
            candidate=observation(value,self.schemas,ALLOWED_NOTIFICATIONS,outcome=outcome,expected_thread=THREAD,expected_turn=TURN)
            baseline=(outcome=='validated'and value['method']=='thread/tokenUsage/updated'and self.phase=='baseline_drain'and
                self.baseline_completion_verified and self.baseline_count==0 and all(row['present']and row['matches']for row in candidate['binding'].values()))
            self.notification_allowed=ALLOWED_NOTIFICATIONS|{'thread/tokenUsage/updated'}if baseline else ALLOWED_NOTIFICATIONS
            try:super()._notification_observation(value,outcome)
            finally:self.notification_allowed=ALLOWED_NOTIFICATIONS
            if outcome=='validated':
                fact=observation(value,self.schemas,ALLOWED_NOTIFICATIONS,outcome=outcome,expected_thread=THREAD,expected_turn=TURN)
                if any(row['wrong']for row in fact['binding'].values()):raise ServiceError('CSS_RECOVERY_NOTIFICATION_BINDING_REJECTED')
                if value['method']=='remoteControl/status/changed'and value['params']['status']!='disabled':
                    self.remote_control_status=value['params']['status']
                    self._record_capability({'kind':'notification','method':value['method'],'status':self.remote_control_status})
                elif value['method']=='mcpServer/startupStatus/updated':
                    self.mcp_startup_seen=True
                    self._record_capability({'kind':'notification','method':value['method'],'unexpected_startup':True})
                if value['method']=='thread/tokenUsage/updated':
                    if self.phase!='baseline_drain'or not self.baseline_completion_verified or self.baseline_count or \
                        not all(row['present']and row['matches']for row in fact['binding'].values()):
                        raise ServiceError('CSS_RECOVERY_BASELINE_REJECTED')
                    self.baseline_count+=1
                else:notification_fact(value,self.schemas)
        def _configuration_hash(self):return p.verify(pin)['_private_content_hash']
        def _send(self,value):
            unexpired(authority);self._wall_check()
            if value.get('method')not in ALLOWED|{'initialized'}:raise ServiceError('CSS_RECOVERY_METHOD_REJECTED')
            ident=value.get('id');method=value.get('method')
            if type(ident)is int:
                self.rpc_pending[ident]={'method':method,'phase':self.phase,'sent':False,'response_schema_valid':False}
                atomic_record(folder/('rpc-intent-'+str(ident)+'.json'),{'method':method,'request_id':ident,'phase':self.phase,'response_schema_valid':False,'body_retained':False})
            result=super()._send(value)
            if type(ident)is int:self.rpc_pending[ident]['sent']=True
            return result
        def pending_frame_observation(self,value):
            ident=value.get('id');owned=type(ident)is int and ident in self.rpc_pending
            item=self.rpc_pending[ident]if owned else{};valid=False
            if owned and 'result'in value and 'error'not in value and 'method'not in value:
                try:self._validate(RESPONSES[item['method']],value['result']);valid=True
                except ServiceError:pass
            fact={'method':item.get('method','unregistered_response'),'request_id':ident if owned else None,
                'phase':'terminal_drain','known_request':owned,'schema_valid_response_received':valid,
                'prior_call_response_schema_valid':item.get('response_schema_valid',False),
                'accepted_as_success':False,'raw_body_retained':False}
            self.rpc_terminal_sequence+=1
            atomic_record(folder/('rpc-terminal-'+str(self.rpc_terminal_sequence)+'.json'),fact)
        def drain_baseline(self,thread_id):
            # Consume resume's cumulative prior-turn metadata separately from
            # subsequent capability/read/isolation phases. Values never persist.
            if thread_id!=THREAD or self.phase!='baseline_drain'or not self.baseline_completion_verified or self.active_profile!=PERMISSION_PROFILE:
                raise ServiceError('CSS_RECOVERY_BASELINE_REJECTED')
            notices=[];quiet=0;deadline=time.monotonic()+1
            for _ in range(256):
                self._wall_check();unexpired(authority)
                value=self.poll(.05)
                if value is None:
                    quiet+=1
                    if quiet==2:break
                else:
                    quiet=0
                    if value['method']=='thread/tokenUsage/updated':
                        notices.append({'method':'thread/tokenUsage/updated','completed_turn_binding':True,'body_retained':False})
                    else:notices.append(notification_fact(value,self.schemas))
                if time.monotonic()>=deadline:raise ServiceError('CSS_RECOVERY_BASELINE_CAPACITY')
            else:raise ServiceError('CSS_RECOVERY_BASELINE_CAPACITY')
            self.phase='post_resume_read'
            return {'usage_notification_count':self.baseline_count,'notices':notices,'usage_values_retained':False}
        def call(self,method,params,timeout=15):
            unexpired(authority);self._wall_check()
            if method not in ALLOWED:raise ServiceError('CSS_RECOVERY_METHOD_REJECTED')
            if method in {'thread/read','thread/resume'}and params.get('threadId')!=THREAD:
                raise ServiceError('CSS_RECOVERY_BINDING_REJECTED')
            if method=='thread/read'and params!={'threadId':THREAD,'includeTurns':False}:
                raise ServiceError('CSS_RECOVERY_METHOD_REJECTED')
            if method=='thread/resume':
                expected={'threadId':THREAD,'excludeTurns':True,'cwd':str(c.TRIAL),'permissions':PERMISSION_PROFILE,
                    'approvalPolicy':'on-request','model':p.MODEL,'config':{'model_reasoning_effort':p.EFFORT}}
                if params!=expected or getattr(self,'resumed',False):raise ServiceError('CSS_RECOVERY_RESUME_REJECTED')
                atomic_record(folder/'evidence-resume-intent-v10.json',{'contract':CONTRACT,'thread_id':THREAD,'prior_resumes':3,'total_resume_limit':4,'model_turns_allowed':0})
                self.resumed=True
            if method=='config/read':
                if getattr(self,'config_read',False)or params!={'includeLayers':False}:raise ServiceError('CSS_RECOVERY_METHOD_REJECTED')
                self.config_read=True
            if method=='command/exec':
                expected={'command':probe_argv(),'cwd':str(c.TRIAL),'permissionProfile':PERMISSION_PROFILE,'timeoutMs':5000,'outputBytesCap':4096}
                if params!=expected or getattr(self,'probed',False):raise ServiceError('CSS_RECOVERY_PROBE_REJECTED')
                self.probed=True
            if method=='mcpServerStatus/list'and params!={'threadId':THREAD,'limit':100,'detail':'toolsAndAuthOnly'}:
                raise ServiceError('CSS_RECOVERY_BINDING_REJECTED')
            if method=='experimentalFeature/list'and (set(params)!={'threadId','limit','cursor'}or params['threadId']!=THREAD or
                type(params['limit'])is not int or params['limit']!=100 or (params['cursor']is not None and type(params['cursor'])is not str)):
                raise ServiceError('CSS_RECOVERY_BINDING_REJECTED')
            self.audit.append({'method':method,'thread_id':params.get('threadId'),'cursor_present':params.get('cursor')is not None})
            ident=self.next_id+1
            try:
                result=super().call(method,params,min(timeout,max(.001,getattr(self,'wall_deadline',time.monotonic()+timeout)-time.monotonic())))
            except Exception:
                if ident in self.rpc_pending:
                    atomic_record(folder/('rpc-outcome-'+str(ident)+'.json'),{**self.rpc_pending[ident],'request_id':ident,'call_returned':False,'body_retained':False})
                raise
            if ident in self.rpc_pending:
                self.rpc_pending[ident]['response_schema_valid']=True
                atomic_record(folder/('rpc-outcome-'+str(ident)+'.json'),{**self.rpc_pending[ident],'request_id':ident,'call_returned':True,'body_retained':False})
            if method=='thread/resume':self.phase='baseline_drain'
            return result
    a=EvidenceAdapter(c.TRIAL,command=('codex','app-server','--stdio',*p.OVERRIDES));a.audit=[];a.phase='pre_resume';a.baseline_count=0;a.baseline_completion_verified=False;a.rpc_pending={};a.rpc_terminal_sequence=0
    a.capability_recorder=lambda n,f:atomic_record(folder/('capability-current-v10-'+str(n)+'.json'),f)
    a.notification_allowed=ALLOWED_NOTIFICATIONS;a.notification_thread=THREAD;a.notification_turn=TURN
    a.notification_recorder=lambda n,f:atomic_record(folder/('notification-metadata-v10-'+str(n)+'.json'),f)
    return a


def finish(a,record,folder,authority,s,t,before_source,before_trial,before,before_auth):
    """Retain all obtained facts even when a later preservation check fails."""
    def observe(name,check):
        try:record[name]=check()
        except Exception as error:
            record[name]=False
            record.setdefault('closure_errors',[]).append({'check':name,'code':error.code if isinstance(error,ServiceError)else type(error).__name__})
    proc=a.proc
    observe('closed',lambda:(a.close(),proc is None or proc.poll()is not None)[1])
    if getattr(a,'watchdog',None)is not None:a.watchdog.cancel();a.watchdog.join()
    record['helper_wall_limit_exceeded']=getattr(a,'wall_exceeded',False)
    if record['helper_wall_limit_exceeded']:record['status']='PARTIAL_BLOCKED'
    observe('final_capabilities_consistent',lambda:final_capability_state(a,record))
    record['exit']=proc.returncode if proc else None
    record['methods']=a.audit;record['resume_calls_consumed']=int(getattr(a,'resumed',False));record['capability_facts']=a.capability_observations
    observe('source_unchanged',lambda:source_snapshot()==before_source)
    observe('readonly_preview14_unchanged',lambda:(mapping_check(t['intent']['grant']),True)[1])
    observe('trial_unchanged',lambda:c.placeholder_snapshot()==before_trial)
    observe('task_row_unchanged',lambda:s.task(c.TASK)==t)
    observe('auth_metadata_unchanged',lambda:auth_identity()==before_auth)
    def config_check():
        after=p.verify(authority['protected_pin'])
        record['config_identity_changed']=before['identity']!=after['identity']
        record['config_contents_changed']=before['_private_content_hash']!=after['_private_content_hash']
        return True
    observe('protected_config_unchanged',config_check)
    observe('expiry_valid_at_close',lambda:(unexpired(authority),True)[1])
    if record['exit']!=0 or not all(record[k]for k in ('closed','final_capabilities_consistent','source_unchanged','readonly_preview14_unchanged','trial_unchanged','task_row_unchanged',
        'auth_metadata_unchanged','protected_config_unchanged','expiry_valid_at_close')):record['status']='PARTIAL_BLOCKED'
    record['historical_v7_postcheck']='PARTIAL_BLOCKED unchanged; this is fresh current loaded evidence after completed turn'
    atomic_record(folder/'capability-recovery-result-v10.json',record)


def run():
    folder=c.HUB_ROOT/c.PRIVATE
    authority=json.loads((folder/'capability-recovery-authority-v10.json').read_text())
    authority_check(authority)
    if (folder/'capability-recovery-intent-v10.json').exists():raise ServiceError('CSS_RECOVERY_ALREADY_CONSUMED')
    s=TaskStore(c.HUB_ROOT);t=s.task(c.TASK);es=s.events(c.TASK);c.validate(t['intent']['grant'])
    from .task_service import root_identity
    if root_identity(c.TRIAL)!=t['intent']['grant']['root_identity']:raise ServiceError('CSS_RECOVERY_TRIAL_CHANGED')
    if digest(t)!=authority['task_hash']or digest(t['intent'])!=INTENT_HASH or digest(t['intent']['grant'])!=GRANT_HASH or \
        t['thread_id']!=THREAD or t['turn_id']!=TURN or t['provider_status']!='completed'or \
        t['status']!='requires_reconcile'or t['cancel']or \
        sum(e['key']=='css-unique-model-turn-v5'for e in es)!=1 or \
        not any(e['data'].get('method')=='turn/completed'and e['data'].get('thread_id')==THREAD and e['data'].get('turn_id')==TURN for e in es):
        raise ServiceError('CSS_RECOVERY_TASK_CHANGED')
    history_check(es,authority)
    if authority['expires_at']-time.time()<150:raise ServiceError('CSS_RECOVERY_CLOSE_HEADROOM')
    mapping_check(t['intent']['grant']);before_source=source_snapshot()
    if {f:v['sha256']for f,v in before_source.items()}!={'progress_ui.css':c.BEFORE_SHA,**c.DOCS}or \
        before_source['progress_ui.css']['identity']!=t['intent']['grant']['css_trial']['source_identity']or before_source!=authority['source']:
        raise ServiceError('CSS_RECOVERY_SOURCE_CHANGED')
    before_trial=c.placeholder_snapshot()
    if before_trial!=authority['trial']:raise ServiceError('CSS_RECOVERY_TRIAL_CHANGED')
    before=p.verify(authority['protected_pin']);before_auth=auth_identity()
    if before_auth!=authority['auth_identity']:raise ServiceError('CSS_RECOVERY_AUTH_CHANGED')
    atomic_record(folder/'capability-recovery-intent-v10.json',{'authority_hash':digest(authority),'task_hash':digest(t),'model_turns_allowed':0,'native_threads_before':2,'resume_calls_before':3})
    a=adapter(authority['protected_pin'],folder,authority)
    if sum(e['data'].get('method')=='turn/completed'and e['data'].get('thread_id')==THREAD and e['data'].get('turn_id')==TURN for e in es)!=1:
        raise ServiceError('CSS_RECOVERY_COMPLETION_EVENT_REJECTED')
    a.baseline_completion_verified=True
    record={'contract':CONTRACT,'status':'PARTIAL_BLOCKED','model_turns':0,'new_threads':0,'source_writes':0,'taskstore_row_writes':0}
    try:
        if authority['expires_at']-time.time()<150:raise ServiceError('CSS_RECOVERY_CLOSE_HEADROOM')
        a.open();record['pid']=a.proc.pid
        record['effective_process']=p.configuration_fact(a.call('config/read',{'includeLayers':False}))
        read=a.call('thread/read',{'threadId':THREAD,'includeTurns':False})['thread']
        if read['id']!=THREAD or read['cwd']!=str(c.TRIAL):raise ServiceError('CSS_RECOVERY_BINDING_REJECTED')
        record['before_load_status']=read['status']['type']
        if read['status']['type']not in ('notLoaded','idle'):raise ServiceError('CSS_RECOVERY_IDLE_UNVERIFIED')
        resume=a.call('thread/resume',{'threadId':THREAD,'excludeTurns':True,'cwd':str(c.TRIAL),'permissions':PERMISSION_PROFILE,
            'approvalPolicy':'on-request','model':p.MODEL,'config':{'model_reasoning_effort':p.EFFORT}})
        if resume['thread']['id']!=THREAD or resume['thread']['cwd']!=str(c.TRIAL)or resume['model']!=p.MODEL or resume['reasoningEffort']!=p.EFFORT:
            raise ServiceError('CSS_RECOVERY_BINDING_REJECTED')
        a._profile_echo(resume)
        record['completed_usage_baseline']=a.drain_baseline(THREAD)
        read=a.call('thread/read',{'threadId':THREAD,'includeTurns':False})['thread']
        if read['id']!=THREAD or read['cwd']!=str(c.TRIAL)or read['status']['type']!='idle':raise ServiceError('CSS_RECOVERY_IDLE_UNVERIFIED')
        record['current_idle']={'thread_id':THREAD,'exact_cwd':True,'status':'idle','completed_turn_binding':'existing immutable exactTaskStore terminal event; no hydrated history inference'}
        a.phase='capability'
        record['current_capabilities']=a.verify_capabilities(THREAD)
        record['loaded_six_mcp']=loaded_six_mcp(a.capability_observations)
        a.phase='isolation'
        record['read_only_isolation']=probe_fact(a.call('command/exec',{'command':probe_argv(),'cwd':str(c.TRIAL),'permissionProfile':PERMISSION_PROFILE,'timeoutMs':5000,'outputBytesCap':4096}))
        notices=[]
        for _ in range(256):
            notice=a.poll(0)
            if notice is None:break
            notices.append(notification_fact(notice,a.schemas))
        else:raise ServiceError('CSS_RECOVERY_NOTIFICATION_CAPACITY')
        record['notifications']=notices
        record['status']='PASS_CURRENT_EVIDENCE_ONLY'
    except (ServiceError,OSError,ValueError,KeyError,TypeError)as e:
        record['error']=e.code if isinstance(e,ServiceError)else type(e).__name__
    finally:
        finish(a,record,folder,authority,s,t,before_source,before_trial,before,before_auth)
    return record
