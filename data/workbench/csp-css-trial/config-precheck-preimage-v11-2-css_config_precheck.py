"""Bounded native configuration metadata check; never loads a thread."""
import copy
import json
import re
import shutil
import threading
import time
import tomllib
from pathlib import Path

import yaml

from . import css_trial as c, css_process_config as p, css_capability_recovery as r
from .codex_adapter import AppServerAdapter, EVIDENCE_PERMISSION_PROFILE, REGISTERED_SERVERS, profile_arguments
from .css_read_diagnostic import atomic_record
from .service_contract import ServiceError

CONTRACT='HUB-LWB-2.3-CSP-CONFIG-PRECHECK-GOV-v11.1'
METHODS=('initialize','config/read','mcpServerStatus/list','experimentalFeature/list')
NOTICES=frozenset({'remoteControl/status/changed','account/updated','account/rateLimits/updated','configWarning'})
DTO_FIELDS=frozenset({'analytics','approval_policy','approvals_reviewer','browser_use','compact_prompt','computer_use',
    'desktop','developer_instructions','forced_chatgpt_workspace_id','forced_login_method','instructions','model',
    'model_auto_compact_token_limit','model_auto_compact_token_limit_scope','model_context_window','model_provider',
    'model_reasoning_effort','model_reasoning_summary','model_verbosity','review_model','sandbox_mode',
    'sandbox_workspace_write','service_tier','tools','web_search'})
REGISTRATION='config-precheck-registration-v11-1.json'
AUTHORITY='config-precheck-authority-v11-1.json'
INTENT='config-precheck-intent-v11-1.json'
RESULT='config-precheck-result-v11-1.json'
# Public embedded defaults from the pinned release, not a private config body.
PACKAGED_DEFAULTS=tomllib.loads('''
include_permissions_instructions = true
include_apps_instructions = true
include_collaboration_mode_instructions = true
include_environment_context = true
cli_auth_credentials_store = "file"
mcp_oauth_credentials_store = "auto"
project_doc_max_bytes = 32768
project_doc_fallback_filenames = []
background_terminal_max_timeout = 300000
file_opener = "vscode"
hide_agent_reasoning = false
chatgpt_base_url = "https://chatgpt.com/backend-api/"
project_root_markers = [".git"]
[history]
persistence = "save-all"
''')


def config_params():return {'cwd':str(c.TRIAL),'includeLayers':True}


def session_config():
    args=profile_arguments(c.TRIAL,read_only=True)
    return {'model':p.MODEL,'model_reasoning_effort':p.EFFORT,'web_search':'disabled',
        'features':{k:False for k in p.FROZEN_DISABLES},
        'mcp_servers':{k:{'enabled':False}for k in REGISTERED_SERVERS},
        **tomllib.loads('\n'.join(args[i]for i in (1,3,5)))}


def merged(base,override):
    result=copy.deepcopy(base)
    for key,value in override.items():
        result[key]=merged(result[key],value)if type(result.get(key))is dict and type(value)is dict else copy.deepcopy(value)
    return result


def config_fact(result,pin):
    """Validate bodies privately; retain no native layer version/content hash."""
    context=pin['sensitive_structure']['projects']['bound_active_context']
    if context['trust']['selected']!={'selector':None,'trust_level':None}or context['closure']['cwd_canonical']!=str(c.TRIAL):
        raise ServiceError('CSS_PRECHECK_TRUST_REJECTED')
    layers=result.get('layers');expected=['packagedDefaults','system','user','project','sessionFlags']
    if type(layers)is not list or len(layers)!=5:raise ServiceError('CSS_PRECHECK_LAYERS_REJECTED')
    home=Path(p.process_environment()['HOME']);user=home/'.codex/config.toml'
    executable=str(Path(shutil.which('codex',path=p.process_environment()['PATH'])).resolve())
    disabled=f'To load project-local config, hooks, and exec policies, add {c.TRIAL} as a trusted project in {user}.'
    combined={};facts=[]
    for index,(layer,kind)in enumerate(zip(layers,expected)):
        if type(layer)is not dict or set(layer)-{'config','name','version','disabledReason'}or \
            type(layer.get('version'))is not str or not layer['version']or type(layer.get('config'))is not dict:
            raise ServiceError('CSS_PRECHECK_LAYER_STRUCTURE_REJECTED')
        name=layer.get('name');body=layer['config'];reason=layer.get('disabledReason')
        if type(name)is not dict or name.get('type')!=kind:raise ServiceError('CSS_PRECHECK_LAYER_ORDER_REJECTED')
        if kind=='packagedDefaults':
            accepted=name=={'type':kind,'file':executable}and body==PACKAGED_DEFAULTS and reason is None
        elif kind=='system':
            accepted=name=={'type':kind,'file':'/etc/codex/config.toml'}and body=={}and reason is None
        elif kind=='user':
            accepted=name in ({'type':kind,'file':str(user)},{'type':kind,'file':str(user),'profile':None})and \
                p.semantic_projection(body)==pin and reason is None
        elif kind=='project':
            accepted=name=={'type':kind,'dotCodexFolder':str(c.TRIAL/'.codex')}and body=={}and reason==disabled
        else:accepted=name=={'type':kind}and body==session_config()and reason is None
        if not accepted:raise ServiceError('CSS_PRECHECK_LAYER_CONTENT_REJECTED')
        if reason is None:combined=merged(combined,body)
        facts.append({'index':index,'name':kind,'source_category':kind,'project_present':kind=='project',
            'disabled':reason is not None,'disabled_reason':'missing_trust'if kind=='project'else None})
    effective=result.get('config')
    # The protocol's optional DTO fields may be emitted as null. Everything
    # carrying an effect must equal the privately validated enabled layers.
    if type(effective)is not dict or any(effective.get(k)!=v for k,v in combined.items())or \
        any(k not in DTO_FIELDS or v is not None for k,v in effective.items()if k not in combined):
        raise ServiceError('CSS_PRECHECK_EFFECTIVE_CONFIG_REJECTED')
    if effective['model']!=p.MODEL or effective['model_reasoning_effort']!=p.EFFORT or \
        effective['default_permissions']!=EVIDENCE_PERMISSION_PROFILE:
        raise ServiceError('CSS_PRECHECK_EFFECTIVE_PROFILE_REJECTED')
    return {'exact_cwd':str(c.TRIAL),'model':p.MODEL,'effort':p.EFFORT,'configured_profile':EVIDENCE_PERMISSION_PROFILE,
        'active_project_trust':None,'layer_count':len(facts),'layers':facts,'native_profile_echo':'NOT_RUN requires later cold resume',
        'raw_config_or_layer_body_retained':False,'private_layer_version_or_hash_retained':False}


def admit(authority):
    r.unexpired(authority)
    expected={'contract':CONTRACT,'task_id':c.TASK,'methods':list(METHODS),'config_read_params':config_params(),
        'profile_arguments':list(profile_arguments(c.TRIAL,read_only=True)),'helpers_allowed':1,
        'helper_wall_seconds':120,'threads_allowed':0,'resumes_allowed':0,'model_turns_allowed':0,'renewal_allowed':False,
        'auth_mode':'ChatGPT'}
    if any(type(authority.get(k))is not type(v)or authority.get(k)!=v for k,v in expected.items()):
        raise ServiceError('CSS_PRECHECK_AUTHORITY_REJECTED')
    reg=json.loads((c.HUB_ROOT/c.PRIVATE/REGISTRATION).read_text())
    if c.digest(reg['files'])!=reg['candidate_sha256']or c.digest({k:v for k,v in reg.items()
        if k not in ('candidate_sha256','evidence_sha256','created_at')})!=reg['evidence_sha256']:
        raise ServiceError('CSS_PRECHECK_REGISTRATION_CHANGED')
    if authority.get('approved_candidate')!=reg['candidate_sha256']or authority.get('approved_evidence')!=reg['evidence_sha256']:
        raise ServiceError('CSS_PRECHECK_AUTHORITY_REJECTED')
    for name,h in {**reg['files'],**reg['evidence_files']}.items():
        if c.sha((c.HUB_ROOT/name).read_bytes())!=h:raise ServiceError('CSS_PRECHECK_CANDIDATE_CHANGED')
    if any(authority.get(k)!=reg[k]for k in ('semantic_pin','source','trial','auth_identity','task_hash')):
        raise ServiceError('CSS_PRECHECK_AUTHORITY_REJECTED')
    text=(c.HUB_ROOT/'STATE.yaml').read_text();match=re.search(r'^linux_visual_workbench:\n.*?(?=^[^ \n#][^\n]*:\n|\Z)',text,re.M|re.S)
    state=yaml.safe_load(match[0])['linux_visual_workbench']if match else{}
    contract=state.get('contract',{});owner=state.get('authorization',{}).get('conditional_pilot_write_grant',{})
    if contract.get('id')!=CONTRACT or contract.get('decision')!='APPROVE_NATIVE_PRECHECK'or \
        contract.get('status')!='APPROVED_SINGLE_NATIVE_PRECHECK'or \
        owner.get('state')!='active_exact_css_trial'or owner.get('task_id')!=c.TASK or \
        owner.get('project_id')!='computer-study-plan'or owner.get('grant_id')!='csp-css-trial-grant-v1'or \
        state.get('trial_registration',{}).get('resume_calls')!=4:
        raise ServiceError('CSS_PRECHECK_STATE_REJECTED')
    if (c.HUB_ROOT/c.PRIVATE/INTENT).exists():raise ServiceError('CSS_PRECHECK_ALREADY_CONSUMED')
    return reg


def adapter(pin,authority):
    class ConfigOnly(AppServerAdapter):
        def _child_environment(self):return p.process_environment()
        def _configuration_hash(self):return p.verify(pin)['_private_content_hash']
        def _send(self,value):
            method=value.get('method')
            if method=='initialized':
                if value!={'method':'initialized','params':{}}or getattr(self,'initialized_sent',False)or not self.initialized_accepted:
                    raise ServiceError('CSS_PRECHECK_METHOD_REJECTED')
                self.initialized_sent=True
            elif method not in METHODS or set(value)!={'id','method','params'}or \
                type(value['id'])is not int or value['id']not in self.pending_ids or \
                self.active_request!=(method,value['params']):raise ServiceError('CSS_PRECHECK_METHOD_REJECTED')
            return super()._send(value)
        def open(self):
            admit(authority);self.deadline=time.monotonic()+100
            atomic_record(c.HUB_ROOT/c.PRIVATE/INTENT,{'contract':CONTRACT,
                'approved_candidate':authority['approved_candidate'],'approved_evidence':authority['approved_evidence'],
                'helpers_allowed':1,'threads':0,'resumes':0,'model_turns':0})
            self.watchdog=threading.Timer(120,self.deadline_close);self.watchdog.start()
            return super().open()
        def deadline_close(self):
            self.wall_exceeded=True;self.close()
        def call(self,method,params,timeout=15):
            r.unexpired(authority)
            if time.monotonic()>=getattr(self,'deadline',float('inf')):raise ServiceError('CSS_PRECHECK_WALL_EXPIRED')
            if method not in METHODS:raise ServiceError('CSS_PRECHECK_METHOD_REJECTED')
            fixed={'initialize':{'clientInfo':{'name':'personal_control_hub','version':'0.1.0'},'capabilities':{'experimentalApi':True}},
                'config/read':config_params(),'mcpServerStatus/list':{'threadId':None,'limit':100,'detail':'toolsAndAuthOnly'}}
            if method in fixed:
                if params!=fixed[method]or method in self.consumed:raise ServiceError('CSS_PRECHECK_PARAMS_REJECTED')
                self.consumed.add(method)
            else:
                if params!={'threadId':None,'limit':100,'cursor':self.cursor}or self.pages>=8 or self.feature_complete:
                    raise ServiceError('CSS_PRECHECK_PARAMS_REJECTED')
                self.pages+=1
            self.audit.append({'method':method,'unscoped':True})
            self.active_request=(method,params)
            try:result=super().call(method,params,min(timeout,max(.001,getattr(self,'deadline',time.monotonic()+timeout)-time.monotonic())))
            finally:self.active_request=None
            if method=='initialize':self.initialized_accepted=True
            if method=='experimentalFeature/list':
                self.cursor=result['nextCursor'];self.feature_complete=self.cursor is None
            return result
        def _notification_observation(self,value,outcome):
            if value.get('method')=='mcpServer/startupStatus/updated':self.mcp_startup_seen=True
            super()._notification_observation(value,outcome)
            if outcome!='validated'or value.get('method')not in NOTICES:
                raise ServiceError('CSS_PRECHECK_NOTIFICATION_REJECTED')
    a=ConfigOnly(c.TRIAL,command=('codex','app-server','--stdio',*p.OVERRIDES),read_only=True)
    a.consumed=set();a.audit=[];a.pages=0;a.cursor=None;a.feature_complete=False;a.wall_exceeded=False
    a.initialized_accepted=False;a.active_request=None
    a.notification_allowed=NOTICES
    return a


def drain(a):
    deadline=time.monotonic()+1;quiet=0
    for _ in range(256):
        value=a.poll(.05)
        if value is None:
            quiet+=1
            if quiet==2:return
        else:quiet=0
        if time.monotonic()>=deadline:break
    raise ServiceError('CSS_PRECHECK_NOTIFICATION_CAPACITY')


def capability_fact(a,servers,flags):
    rows=servers['data']
    if servers['nextCursor']is not None or len(rows)!=6 or sorted(x['name']for x in rows)!=sorted(REGISTERED_SERVERS)or \
        any(x['runtimeStatus']!='disabled'or x['tools']or x['resources']or x['resourceTemplates']or x['serverCapabilities']is not None for x in rows)or \
        any(flags.get(k)is not False for k in p.FROZEN_DISABLES)or flags.get('code_mode_host')is not True or \
        a.remote_control_status!='disabled'or a.mcp_startup_seen or a.denials:
        raise ServiceError('CSS_PRECHECK_CAPABILITY_REJECTED')
    return {'scope':'unscoped process metadata; no loaded thread claim',
        'mcp':{k:'disabled'for k in REGISTERED_SERVERS},'features':{k:False for k in p.FROZEN_DISABLES},
        'remote':'disabled','startup_seen':False,'local_native_tool_host':True}


def run():
    folder=c.HUB_ROOT/c.PRIVATE;authority=json.loads((folder/AUTHORITY).read_text());admit(authority)
    before=p.verify(authority['semantic_pin']);source=r.source_snapshot();trial=c.placeholder_snapshot();auth=r.auth_identity()
    task=r.TaskStore(c.HUB_ROOT).task(c.TASK)
    if source!=authority['source']or trial!=authority['trial']or auth!=authority['auth_identity']or c.digest(task)!=authority['task_hash']:
        raise ServiceError('CSS_PRECHECK_INPUT_CHANGED')
    if authority['expires_at']-time.time()<150:raise ServiceError('CSS_PRECHECK_CLOSE_HEADROOM')
    r.mapping_check(task['intent']['grant'])
    a=adapter(authority['semantic_pin'],authority);record={'contract':CONTRACT,'status':'PARTIAL_BLOCKED','threads':0,'resumes':0,'model_turns':0}
    a.notification_recorder=lambda n,f:atomic_record(folder/f'config-precheck-notification-v11-1-{n}.json',f)
    try:
        a.open();record['pid']=a.proc.pid
        record['configuration']=config_fact(a.call('config/read',config_params()),authority['semantic_pin'])
        servers=a.call('mcpServerStatus/list',{'threadId':None,'limit':100,'detail':'toolsAndAuthOnly'});flags={}
        for _ in range(8):
            page=a.call('experimentalFeature/list',{'threadId':None,'limit':100,'cursor':a.cursor})
            for row in page['data']:
                if row['name']in flags:raise ServiceError('CSS_PRECHECK_DUPLICATE_FEATURE')
                flags[row['name']]=row['enabled']
            if page['nextCursor']is None:break
        if not a.feature_complete:raise ServiceError('CSS_PRECHECK_FEATURE_CAPACITY')
        drain(a);record['capabilities']=capability_fact(a,servers,flags);record['status']='PASS_PROCESS_CONFIG_METADATA_ONLY'
    except Exception as error:record['error']=error.code if isinstance(error,ServiceError)else type(error).__name__
    finally:
        def check(name,fn):
            try:record[name]=fn()
            except Exception as error:
                record[name]=False;record.setdefault('closure_errors',[]).append({'check':name,'code':error.code if isinstance(error,ServiceError)else type(error).__name__})
        check('closed',lambda:(a.close(),a.proc is not None and a.proc.poll()is not None)[1])
        if getattr(a,'watchdog',None):a.watchdog.cancel();a.watchdog.join()
        check('final_notice_state',lambda:r.final_capability_state(a,record))
        check('global_config_preserved',lambda:all(p.preservation_fact(before,p.verify(authority['semantic_pin'])).values()))
        check('source3_unchanged',lambda:r.source_snapshot()==source)
        check('trial_css_four_dirs_unchanged',lambda:c.placeholder_snapshot()==trial)
        check('auth_metadata_unchanged',lambda:r.auth_identity()==auth)
        check('task_row_unchanged',lambda:c.digest(r.TaskStore(c.HUB_ROOT).task(c.TASK))==authority['task_hash'])
        check('expiry_at_close',lambda:(r.unexpired(authority),True)[1])
        record.update(exit=a.proc.returncode if a.proc else None,methods=a.audit,wall_exceeded=a.wall_exceeded)
        if record['exit']!=0 or a.wall_exceeded or not all(record[k]for k in ('closed','final_notice_state','global_config_preserved','source3_unchanged',
            'trial_css_four_dirs_unchanged','auth_metadata_unchanged','task_row_unchanged','expiry_at_close')):record['status']='PARTIAL_BLOCKED'
        atomic_record(folder/RESULT,record)
    return record
