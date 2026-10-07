#!/usr/bin/env python3
"""Root-only fixed no-thread isolation diagnosis; cannot start or resume a turn."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hub import css_trial as c
from hub.codex_adapter import AppServerAdapter,DISABLED_FEATURES,PERMISSION_PROFILE,REGISTERED_SERVERS
from hub.service_contract import ServiceError
from hub.task_service import fingerprint,root_identity
from hub.task_store import TaskStore
from hub.workbench_store import digest

CONTRACT='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v4-DIAGNOSTIC'
PLACEHOLDERS=('.agents','.aws','.codex','.git')
CANARY=c.TRIAL.parent/('parent-canary-'+c.TASK)
WRITE_PROBE=c.TRIAL.parent/('deny-parent-write-'+c.TASK)
ORIGINAL=c.HUB_ROOT/c.PRIVATE/'isolation-diagnostic-v3-r1.json'
RECORD=c.HUB_ROOT/c.PRIVATE/'isolation-diagnostic-v4.json'


class DiagnosticAdapter(AppServerAdapter):
    methods=frozenset({'initialize','experimentalFeature/list','mcpServerStatus/list','command/exec'})

    def call(self,method,params,timeout=15):
        if method not in self.methods:raise ServiceError('DIAGNOSTIC_METHOD_REJECTED')
        if method=='command/exec':
            expected={'command':['/usr/bin/python3','-c',probe_script()],'cwd':str(c.TRIAL),
                'permissionProfile':PERMISSION_PROFILE,'timeoutMs':5000,'outputBytesCap':4096}
            if params!=expected or getattr(self,'command_dispatched',False):raise ServiceError('DIAGNOSTIC_REQUEST_REJECTED')
            self.command_dispatched=True
        return super().call(method,params,timeout)

    def _send(self,value):
        if 'method'in value and value['method']not in self.methods|{'initialized'}:
            raise ServiceError('DIAGNOSTIC_METHOD_REJECTED')
        return super()._send(value)

    def capabilities(self):
        servers=self.call('mcpServerStatus/list',{'limit':100,'detail':'toolsAndAuthOnly'})
        enabled={};cursor=None
        for _ in range(8):
            flags=self.call('experimentalFeature/list',{'limit':100,'cursor':cursor})
            enabled.update({v['name']:v['enabled']for v in flags['data']});cursor=flags['nextCursor']
            if cursor is None:break
        remote=[v.get('params',{}).get('status')for v in self.notifications if v.get('method')=='remoteControl/status/changed']
        if servers['nextCursor']is not None or flags['nextCursor']is not None or sorted(v['name']for v in servers['data'])!=sorted(REGISTERED_SERVERS) or any(
            v['runtimeStatus']is not None or v['tools']or v['resources']or v['resourceTemplates']or v['serverCapabilities']is not None
            for v in servers['data'])or any(enabled.get(k)is not False for k in DISABLED_FEATURES)or \
            enabled.get('code_mode_host')is not True or not remote or remote[-1]!='disabled' or any(
                v.get('method')=='mcpServer/startupStatus/updated'for v in self.notifications):
            raise ServiceError('DIAGNOSTIC_CAPABILITY_UNVERIFIED',details={
                'features':{k:enabled.get(k)for k in DISABLED_FEATURES},'local_native_tool_host':enabled.get('code_mode_host'),
                'feature_cursor_present':flags['nextCursor']is not None,'mcp_cursor_present':servers['nextCursor']is not None,
                'mcp_count':len(servers['data']),'remote_disabled_observed':bool(remote and remote[-1]=='disabled'),
                'mcp_active_count':sum(v['runtimeStatus']!='disabled'or bool(v['tools'])or bool(v['resources'])or bool(v['resourceTemplates'])or v['serverCapabilities']is not None for v in servers['data'])})
        return {'scope':'process; schema threadId omitted','mcp_names':list(REGISTERED_SERVERS),
            'mcp_tools_resources_capabilities':0,'mcp_runtime_status':None,'mcp_runtime_disabled_verified':False,
            'limitation':'unscoped null runtime metadata; no loaded thread; future model must verify disabled',
            'features':{k:enabled[k]for k in DISABLED_FEATURES},
            'remote':'disabled','web_search_launch':'disabled','launch_argv':list(self.launch_command)}


def directory_state():
    mounts={v.split()[4]for v in Path('/proc/self/mountinfo').read_text().splitlines()}
    rows=[]
    for name in PLACEHOLDERS:
        p=c.TRIAL/name
        if not p.exists():continue
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode)or s.st_uid!=os.getuid()or p.is_symlink()or list(p.iterdir())or str(p)in mounts:
            raise ServiceError('DIAGNOSTIC_PLACEHOLDER_UNSAFE')
        rows.append({'name':name,'identity':[s.st_dev,s.st_ino,s.st_uid,stat.S_IMODE(s.st_mode)],'empty':True,'mount':False})
    return {'fingerprint':fingerprint(c.TRIAL),'empty_directories':rows}


def probe_script():
    paths={'canonical':str(c.ROOT/'progress_ui.css'),'sibling':str(c.HUB_ROOT/c.PRIVATE/'before.css'),
        'tmp':str(c.OUTSIDE_PROBE),'credential':str(c.CREDENTIAL_PATH),'parent_canary':str(CANARY)}
    return """import os,json,socket,hashlib
fd=os.open('progress_ui.css',os.O_RDWR|os.O_NOFOLLOW)
raw=os.read(fd,1048576);os.lseek(fd,0,0);assert os.write(fd,raw)==len(raw);os.fsync(fd);os.close(fd)
p={'trial_sha256':hashlib.sha256(raw).hexdigest(),'read_bytes':0,'denials':{}}
for category,path in PATHS.items():
 for mode,flags in [('read',os.O_RDONLY),('write',os.O_WRONLY)]:
  try:
   fd=os.open(path,flags|os.O_NOFOLLOW);os.close(fd);p['denials'][category+'_'+mode]=0
  except OSError as e:p['denials'][category+'_'+mode]=e.errno
try:
 fd=os.open(PARENT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);os.close(fd);p['parent_open_errno']=0
except OSError as e:p['parent_open_errno']=e.errno
p['parent_file_write_errno']=p['denials']['parent_canary_write']
try:
 names=os.listdir(PARENT);p['parent_enumeration']={'errno':0,'unexpected_count':len([n for n in names if n!=TRIAL_NAME]),'names_sha256':hashlib.sha256(json.dumps(sorted(names)).encode()).hexdigest()}
except OSError as e:p['parent_enumeration']={'errno':e.errno,'unexpected_count':0,'names_sha256':None}
try:
 os.mkdir(WRITE_PROBE);p['parent_write_errno']=0
except OSError as e:p['parent_write_errno']=e.errno
try:
 s=socket.socket();s.close();p['network_errno']=0
except OSError as e:p['network_errno']=e.errno
print(json.dumps(p,sort_keys=True))
""".replace('PATHS',repr(paths)).replace('PARENT',repr(str(c.TRIAL.parent))).replace('TRIAL_NAME',repr(c.TRIAL.name)).replace('WRITE_PROBE',repr(str(WRITE_PROBE)))


def main():
    if RECORD.exists()or CANARY.exists()or WRITE_PROBE.exists():raise ServiceError('DIAGNOSTIC_ALREADY_RUN')
    prior=json.loads(ORIGINAL.read_text())
    if not prior.get('command_dispatched')or not prior.get('result',{}).get('proof')or not prior.get('adapter_closed')or prior.get('source_css_sha256')!=c.BEFORE_SHA:
        raise ServiceError('DIAGNOSTIC_COMMAND_OUTCOME_UNKNOWN')
    store=TaskStore(ROOT);t=store.task(c.TASK);grant=t['intent']['grant']
    if t['status']!='requires_reconcile'or t['turn_id']is not None or t['thread_id']!='01a11536-4647-7030-a9aa-8e397078d146':
        raise ServiceError('DIAGNOSTIC_TASK_CHANGED')
    c.validate(grant);c.source_check(grant,diagnostic=True)
    if root_identity(c.TRIAL)!=grant['root_identity']:raise ServiceError('DIAGNOSTIC_ROOT_CHANGED')
    if not c.writer_check(grant,bound_task=t):raise ServiceError('DIAGNOSTIC_WRITER_UNKNOWN')
    old=json.loads((ROOT/c.PRIVATE/'preflight-failure.json').read_text())
    if not old['owned_adapter_closed']or not old['model_turns']==0:raise ServiceError('DIAGNOSTIC_OLD_CHILD_UNKNOWN')
    for e in old['events']:
        if e['key'].startswith('stdio-child-')and Path('/proc/'+str(e['data']['pid'])).exists():
            raise ServiceError('DIAGNOSTIC_OLD_CHILD_PRESENT')
    record={'contract':CONTRACT,'source_sha256':c.sha(Path(__file__).read_bytes()),'old_task':{'id':t['id'],'thread_id':t['thread_id'],'turn_id':None},
        'auth_before':c.auth_check(),'config_identity_before':c.config_identity(),'model_turns':0,'new_threads':0,'stages':{}}
    record['stages']['closed_before']=directory_state()
    for name in PLACEHOLDERS:
        if (c.TRIAL/name).exists():(c.TRIAL/name).rmdir()
    record['stages']['payload_before']=directory_state()
    if fingerprint(c.TRIAL)!=grant['fingerprint']:raise ServiceError('DIAGNOSTIC_PAYLOAD_CHANGED')
    CANARY.write_text('Root-owned parent nonsecret canary; native reads zero bytes.\n')
    os.chmod(CANARY,0o600);canary_raw,canary_identity=c.read_file(CANARY)
    record['canary_before']={'sha256':c.sha(canary_raw),'identity':canary_identity}
    a=DiagnosticAdapter(c.TRIAL)
    try:
        a.open();record['child']={'pid':a.proc.pid,'start_ticks':Path('/proc/'+str(a.proc.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]}
        record['stages']['initialized']=directory_state();record['capabilities_before']=a.capabilities()
        record['schemas']=dict(a.schema_hashes)
        request={'command':['/usr/bin/python3','-c',probe_script()],'cwd':str(c.TRIAL),'permissionProfile':PERMISSION_PROFILE,'timeoutMs':5000,'outputBytesCap':4096}
        a._validate('CommandExecParams',request);record['request']=request;record['stages']['before_command']=directory_state()
        RECORD.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        result=a.call('command/exec',request)
        stdout=result['stdout'];stderr=result['stderr'];proof=None
        try:proof=json.loads(stdout)
        except ValueError:pass
        expected={'trial_sha256','read_bytes','denials','parent_open_errno','parent_enumeration','parent_write_errno','parent_file_write_errno','network_errno'}
        safe=isinstance(proof,dict)and set(proof)==expected and isinstance(proof.get('denials'),dict)and \
            set(proof['denials'])=={k+'_'+m for k in ('canonical','sibling','tmp','credential','parent_canary')for m in ('read','write')}and \
            all(type(v)is int for v in proof['denials'].values())and \
            all(type(proof[k])is int for k in ('read_bytes','parent_open_errno','parent_write_errno','parent_file_write_errno','network_errno'))and \
            isinstance(proof['trial_sha256'],str)and len(proof['trial_sha256'])==64 and \
            all(v in '0123456789abcdef'for v in proof['trial_sha256'])and \
            isinstance(proof['parent_enumeration'],dict)and set(proof['parent_enumeration'])=={'errno','unexpected_count','names_sha256'}and \
            all(type(proof['parent_enumeration'][k])is int for k in ('errno','unexpected_count'))and \
            (proof['parent_enumeration']['names_sha256']is None or isinstance(proof['parent_enumeration']['names_sha256'],str)and \
             len(proof['parent_enumeration']['names_sha256'])==64 and all(v in '0123456789abcdef'for v in proof['parent_enumeration']['names_sha256']))
        record['result']={'exit_code':result['exitCode'],'stdout_sha256':c.sha(stdout.encode()),'stderr_sha256':c.sha(stderr.encode()),
            'stdout_bytes':len(stdout.encode()),'stderr_bytes':len(stderr.encode()),'proof':proof if safe else None,
            'stderr_classes':[v for v in ('PermissionError','FileNotFoundError','Traceback','bwrap')if v in stderr]}
        record['stages']['after_command']=directory_state();record['capabilities_after']=a.capabilities()
    except Exception as e:record['error']={'code':getattr(e,'code',type(e).__name__),'details':getattr(e,'details',{})}
    finally:
        a.close();record['adapter_closed']=a.proc is None or a.proc.poll()is not None
        record['stages']['closed_after']=directory_state()
        record['canary_after']={'sha256':c.sha(c.read_file(CANARY)[0]),'identity':c.read_file(CANARY)[1]}
        record['canary_unchanged']=record['canary_before']==record['canary_after']
        record['auth_after']=c.auth_check();record['config_identity_after']=c.config_identity()
        try:c.source_check(grant,diagnostic=True);record['source_check_after']='verified14source_3preimages_and_pins'
        except Exception as e:record['source_check_after']={'error':getattr(e,'code',type(e).__name__)}
        record['source_css_sha256']=c.sha(c.read_file(c.ROOT/'progress_ui.css',mode=0o644)[0])
        record['trial_css_sha256']=c.sha(c.read_file(c.TRIAL/'progress_ui.css',mode=0o644)[0])
        record['command_dispatched']=getattr(a,'command_dispatched',False)
        record['parent_write_effect']=WRITE_PROBE.exists()
        if WRITE_PROBE.exists():WRITE_PROBE.rmdir()
        CANARY.unlink();record['owned_canary_cleanup']=not CANARY.exists()and not WRITE_PROBE.exists()
        RECORD.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        store.event(c.TASK,'isolation-diagnostic-v4',{'record':str(RECORD.relative_to(ROOT)),'sha256':c.sha(RECORD.read_bytes()),'model_turns':0,'new_threads':0})
    print(json.dumps({k:record.get(k)for k in ('error','result','adapter_closed','canary_unchanged','owned_canary_cleanup','source_css_sha256','trial_css_sha256')},ensure_ascii=False))
    proof=record.get('result',{}).get('proof')or {};denied=(1,2,13,30)
    accepted=bool(proof)and not record.get('error')and record['result']['exit_code']==0 and \
        proof['trial_sha256']==c.BEFORE_SHA and proof['read_bytes']==0 and proof['network_errno']in (1,13)and \
        all(v in denied for v in proof['denials'].values())and \
        all(proof[k]in denied for k in ('parent_open_errno','parent_write_errno','parent_file_write_errno'))and \
        proof['parent_enumeration']['errno']in denied and record['canary_unchanged']and \
        not record['parent_write_effect']and record['owned_canary_cleanup']and record['source_css_sha256']==record['trial_css_sha256']==c.BEFORE_SHA and \
        record['source_check_after']=='verified14source_3preimages_and_pins'
    return 0 if accepted else 1


if __name__=='__main__':raise SystemExit(main())
