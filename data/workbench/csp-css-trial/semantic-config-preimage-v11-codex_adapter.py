"""Bounded private stdio transport. Never execute provider output as instructions."""
from __future__ import annotations

import hashlib
import json
import math
import os
import pwd
import queue
import re
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import jsonschema

from .service_contract import ServiceError
from .workbench_store import canonical

VERSION = 'codex-cli 0.159.3'
MAX_MESSAGE = 1024 * 1024
SCHEMA_PINS = {'ThreadStartParams':'80a40a7fac15b4bf70efb7f893fb353acc0a0d30c68f54aee4f01923deca85de',
 'ConfigReadParams':'257c54a423b47c1d209ff1076765a1564d82322fd5161670fd489a2874de1bac',
 'ConfigReadResponse':'4ec77e1a3eed746037149799c3b6bb8fe7b4b593e5930fc9f410e6282de9df50',
 'ThreadResumeParams':'cc5bb3b25f82073d24af5b6c09e4804ac307467f8400ba5f263fa86f9f0349e6',
 'TurnStartParams':'07771223642e1b61bd9aac0069fc0f98143a1c047724ca02c7ceb13653442738',
 'TurnInterruptParams':'6dff382dae73d1dbc58406ed045605f647e7a49660e2540fbd2c6c24d60c5f2b'}
PARAMS = {'initialize':'InitializeParams','thread/start':'ThreadStartParams',
 'config/read':'ConfigReadParams',
 'thread/list':'ThreadListParams',
 'experimentalFeature/list':'ExperimentalFeatureListParams','mcpServerStatus/list':'ListMcpServerStatusParams',
 'thread/resume':'ThreadResumeParams','thread/read':'ThreadReadParams',
 'turn/start':'TurnStartParams','turn/interrupt':'TurnInterruptParams', 'command/exec':'CommandExecParams'}
RESPONSES = {'initialize':'InitializeResponse','thread/start':'ThreadStartResponse',
 'config/read':'ConfigReadResponse',
 'thread/list':'ThreadListResponse',
 'experimentalFeature/list':'ExperimentalFeatureListResponse','mcpServerStatus/list':'ListMcpServerStatusResponse',
 'thread/resume':'ThreadResumeResponse','thread/read':'ThreadReadResponse',
 'turn/start':'TurnStartResponse','turn/interrupt':'TurnInterruptResponse','command/exec':'CommandExecResponse'}
NOTIFICATIONS={'turn/started':'TurnStartedNotification','turn/completed':'TurnCompletedNotification',
 'thread/tokenUsage/updated':'ThreadTokenUsageUpdatedNotification','thread/status/changed':'ThreadStatusChangedNotification',
 'item/completed':'ItemCompletedNotification'}
DISABLED_FEATURES=('apps','plugins','remote_plugin','browser_use','browser_use_external',
    'browser_use_full_cdp_access','computer_use','in_app_browser','hooks',
    'image_generation','multi_agent','multi_agent_v2','goals','memories')
REGISTERED_SERVERS=('chrome-devtools','context7','github','node_repl','openaiDeveloperDocs','playwright')
REGISTERED_CONFIG_SHA256='eca316513d41a987288204749d1d909295749020565f08cfe576add370acde52'
PERMISSION_PROFILE='hub_lwb_fixture_v1'


def profile_arguments(root):
    root=Path(root)
    if not root.is_absolute() or '..'in root.parts:raise ServiceError('CODEX_PROFILE_REJECTED',status=503)
    # The entire disposable fixture is the grant scope. File-level writable
    # mounts are unsupported by this pinned Linux helper; real-project use
    # still requires its separately accepted exact-file/overlay boundary.
    entries={':root':'deny',':minimal':'read',':tmpdir':'deny',':slash_tmp':'deny',str(root):'write'}
    if root==Path('/home/alalapi/Temp/personal-control-hub/linux-workbench/csp-css-trial-20261007'):
        entries[str(root.parent)]='deny'
    table='{'+','.join(json.dumps(k)+'='+json.dumps(v)for k,v in entries.items())+'}'
    return ('-c','default_permissions='+json.dumps(PERMISSION_PROFILE),
            '-c','permissions.'+PERMISSION_PROFILE+'.filesystem='+table,
            '-c','permissions.'+PERMISSION_PROFILE+'.network.enabled=false')


def child_environment():
    """Provider-native home, system executables and UTF-8; no parent capabilities."""
    return {'HOME':pwd.getpwuid(os.getuid()).pw_dir,
            'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}


def config_fingerprint(config):
    """Hash an exact registered file without parsing or retaining its values."""
    try:
        if config.parent.is_symlink():raise ValueError('alias')
        fd=os.open(config,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>MAX_MESSAGE:
                raise ValueError('unsafe')
            hashed=hashlib.sha256();size=0
            for block in iter(lambda:stream.read(65536),b''):
                size+=len(block)
                if size>MAX_MESSAGE:raise ValueError('oversize')
                hashed.update(block)
            after=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
                raise ValueError('changed')
            return hashed.hexdigest()
    except (OSError,ValueError):raise ServiceError('CODEX_CONFIG_DRIFT',status=503) from None


def isolated_launch(command, *, config_path=None, root=Path('/fixture'), expected_config_sha256=None):
    """Process-local narrowing only. Never edit or export credential/config values."""
    config=Path(config_path)if config_path is not None else Path(child_environment()['HOME'])/'.codex/config.toml'
    if config_fingerprint(config)!=(expected_config_sha256 or REGISTERED_CONFIG_SHA256):
        raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
    names=list(REGISTERED_SERVERS)
    if len(names)!=6 or len(set(names))!=6 or any(type(name)is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',name)for name in names):
        raise ServiceError('CODEX_CAPABILITY_UNVERIFIED',status=503)
    args=list(command)
    for name in names:args+=['-c','mcp_servers.'+name+'.enabled=false']
    for name in DISABLED_FEATURES:args+=['--disable',name]
    args+=['-c','web_search="disabled"']
    args+=profile_arguments(root)
    return tuple(args),names


def sanitized(value, limit=2000):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
    text = re.sub(r'(?i)(authorization|api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
    return ''.join(c for c in text if c in '\n\t' or ord(c)>=32)[:limit]


class AppServerAdapter:
    transport = 'app_server_stdio'

    def __init__(self, root, *, command=('codex','app-server','--stdio'), schemas=None):
        self.root = Path(root)
        self.command = tuple(command)
        self.schemas = schemas
        self.schema_hashes = {}
        self.validators = {}
        self.proc = None
        self.inbox = queue.Queue(maxsize=256)
        self.notifications = []
        self.denials = []
        self.stderr_hash = hashlib.sha256()
        self.stderr_bytes = 0
        self.next_id = 0
        self.pending_ids = set()
        self.failed = None
        self.failed_details = {}
        self.capability_evidence = None
        self.capability_thread = None
        self.active_profile = None
        self.request_handler = None
        self.owned_completion = None
        self.remote_control_status = None
        self.mcp_startup_seen = False
        self.capability_observations = []
        self.capability_recorder = None
        self.notification_recorder = None
        self.notification_allowed = frozenset()
        self.notification_thread = None
        self.notification_turn = None
        self.notification_sequence = 0

    def _configuration_hash(self):
        # Ordinary adapters keep their registered pin. Only the exact CSS
        # recovery subclass resolves its pin from an immutable task authority.
        return REGISTERED_CONFIG_SHA256

    def _turn_overrides(self):return {}

    def open(self):
        env=child_environment()
        self.launch_command,self.disabled_servers=isolated_launch(self.command,root=self.root,
            expected_config_sha256=self._configuration_hash())
        if self.schemas is None:
            run = subprocess.run(['codex','--version'],capture_output=True,timeout=5,text=True,env=env)
            if run.returncode or run.stdout.strip()!=VERSION:
                raise ServiceError('CODEX_VERSION_UNSUPPORTED',status=503)
            self.schemas={}
            with tempfile.TemporaryDirectory(prefix='hub-protocol-schema-') as directory:
                run=subprocess.run(['codex','app-server','generate-json-schema','--experimental','--out',directory],capture_output=True,timeout=10,env=env)
                if run.returncode:raise ServiceError('CODEX_SCHEMA_UNAVAILABLE',status=503)
                for name in set(PARAMS.values())|set(RESPONSES.values())|set(NOTIFICATIONS.values())|{'ServerNotification','ServerRequest'}:
                    paths=list(Path(directory).rglob(name+'.json'))
                    if len(paths)!=1:raise ServiceError('CODEX_SCHEMA_UNAVAILABLE',status=503)
                    raw=paths[0].read_bytes()
                    self.schema_hashes[name]=hashlib.sha256(raw).hexdigest()
                    if name in SCHEMA_PINS and hashlib.sha256(raw).hexdigest()!=SCHEMA_PINS[name]:
                        raise ServiceError('CODEX_SCHEMA_CHANGED',status=503)
                    self.schemas[name]=json.loads(raw)
        if config_fingerprint(Path(env['HOME'])/'.codex/config.toml')!=self._configuration_hash():
            raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
        self.proc=subprocess.Popen(self.launch_command,cwd=self.root,env=env,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        self.reader=threading.Thread(target=self._read,name='hub-codex-stdout',daemon=True)
        self.error_reader=threading.Thread(target=self._read_errors,name='hub-codex-stderr',daemon=True)
        self.reader.start();self.error_reader.start()
        try:
            self.call('initialize',{'clientInfo':{'name':'personal_control_hub','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
            self._send({'method':'initialized','params':{}})
            if config_fingerprint(Path(env['HOME'])/'.codex/config.toml')!=self._configuration_hash():
                raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
        except BaseException:
            self.close();raise
        return self

    def _read(self):
        try:
            while True:
                raw=self.proc.stdout.readline(MAX_MESSAGE+1)
                if not raw:
                    self.failed='CODEX_STREAM_EOF';return
                if len(raw)>MAX_MESSAGE:raise ValueError('oversize')
                def pairs(values):
                    data={}
                    for key,value in values:
                        if key in data:raise ValueError('duplicate key')
                        data[key]=value
                    return data
                value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
                if type(value) is not dict or set(value)-{'id','method','params','result','error','emittedAtMs'}:
                    self.failed_details={'frame_type':type(value).__name__,
                        'unexpected_field_count':len(set(value)-{'id','method','params','result','error','emittedAtMs'}) if type(value)is dict else 0}
                    raise ValueError('invalid envelope')
                if 'emittedAtMs'in value and ('method'not in value or 'id'in value or
                    type(value['emittedAtMs'])not in {int,float} or not math.isfinite(value['emittedAtMs']) or
                    not 0<=value['emittedAtMs']<=2**53-1):
                    raise ValueError('invalid notification timestamp')
                self.inbox.put_nowait(value)
        except (ValueError,queue.Full)as error:
            self.failed_details={**self.failed_details,'failure_class':type(error).__name__,'reason':sanitized(str(error),120)}
            self.failed='CODEX_PROTOCOL_REJECTED'
        except OSError:self.failed='CODEX_STREAM_EOF'

    def _read_errors(self):
        while chunk:=self.proc.stderr.read(4096):
            self.stderr_hash.update(chunk);self.stderr_bytes+=len(chunk)
            if self.stderr_bytes>65536:
                self.failed='CODEX_OUTPUT_CAPACITY';return

    def _send(self, value):
        if len(canonical(value))>MAX_MESSAGE:raise ServiceError('CODEX_INPUT_CAPACITY')
        try:self.proc.stdin.write(canonical(value)+b'\n');self.proc.stdin.flush()
        except (OSError,BrokenPipeError):raise ServiceError('CODEX_STREAM_EOF',status=503,outcome='UNKNOWN') from None

    def _validate(self,name,value):
        try:
            if name not in self.validators:
                schema=self.schemas[name];kind=jsonschema.validators.validator_for(schema)
                kind.check_schema(schema);self.validators[name]=kind(schema)
            self.validators[name].validate(value)
        except jsonschema.ValidationError as error:
            raise ServiceError('CODEX_PROTOCOL_REJECTED',status=503,outcome='UNKNOWN',
                details={'schema':name,'path':list(error.absolute_path),'validator':error.validator,'value_type':type(error.instance).__name__})from None
        except (jsonschema.SchemaError,KeyError):
            raise ServiceError('CODEX_PROTOCOL_REJECTED',status=503,outcome='UNKNOWN',details={'schema':name}) from None

    def _message(self, timeout):
        if self.failed:raise ServiceError(self.failed,status=503,outcome='UNKNOWN',details=self.failed_details)
        try:value=self.inbox.get(timeout=timeout)
        except queue.Empty:return None
        if 'method'in value and 'id'in value:
            # No provider request is permission to broaden a grant.
            if len(self.denials)>=32:raise ServiceError('CODEX_OUTPUT_CAPACITY',status=503,outcome='UNKNOWN')
            method=value['method'];error=None;control={}
            known=method in {'item/commandExecution/requestApproval','item/fileChange/requestApproval','item/tool/requestUserInput'}
            try:
                if 'ServerRequest'in (self.schemas or {}):self._validate('ServerRequest',value)
                elif self.request_handler is not None:raise ServiceError('CODEX_PROTOCOL_REJECTED',outcome='UNKNOWN')
                if known and self.request_handler is not None:control=self.request_handler(value)
            except ServiceError as caught:error=caught
            denial={'method':sanitized(method,120),'digest':hashlib.sha256(canonical(value)).hexdigest(),**control}
            self.denials.append(denial)
            if method in {'item/commandExecution/requestApproval','item/fileChange/requestApproval'}:response={'id':value['id'],'result':{'decision':'decline'}}
            elif method=='item/tool/requestUserInput':response={'id':value['id'],'result':{'answers':{}}}
            else:response={'id':value['id'],'error':{'code':-32601,'message':'Hub denies unsupported server request'}}
            self._send(response)
            if error is not None:raise error
            return None
        try:
            if 'method'in value and 'ServerNotification'in self.schemas:
                self._validate('ServerNotification',value)
            if value.get('method')in NOTIFICATIONS:
                self._validate(NOTIFICATIONS[value['method']],value.get('params'))
        except ServiceError:
            self._notification_observation(value,'schema_rejected')
            raise
        if 'method'in value:self._notification_observation(value,'validated')
        if value.get('method')=='remoteControl/status/changed':
            self.remote_control_status=value['params']['status']
            self._record_capability({'kind':'notification','method':value['method'],'status':self.remote_control_status})
        elif value.get('method')=='mcpServer/startupStatus/updated':
            self.mcp_startup_seen=True
            self._record_capability({'kind':'notification','method':value['method'],'startup_seen':True})
        return value

    def _notification_observation(self,value,outcome):
        if self.notification_recorder is None:return
        from .notification_metadata import observation
        if self.notification_sequence>=256:raise ServiceError('NOTIFICATION_METADATA_CAPACITY')
        fact=observation(value,self.schemas,self.notification_allowed,outcome=outcome,
            expected_thread=self.notification_thread,expected_turn=self.notification_turn)
        self.notification_recorder(self.notification_sequence+1,fact)
        self.notification_sequence+=1

    def _record_capability(self, fact):
        if len(self.capability_observations)>=64:raise ServiceError('CODEX_CAPABILITY_EVIDENCE_CAPACITY',status=503)
        number=len(self.capability_observations)+1
        # The callback persists controlled metadata before a poll can drain it.
        if self.capability_recorder is not None:self.capability_recorder(number,fact)
        self.capability_observations.append(fact)

    def _capability_response(self,method,params,result):
        if method not in {'mcpServerStatus/list','experimentalFeature/list'}:return
        fact={'kind':'rpc_result','method':method,'thread_id':params.get('threadId'),
            'request_cursor_present':params.get('cursor')is not None,'has_next_page':result['nextCursor']is not None,
            'row_count':len(result['data']),'raw_body_retained':False}
        if method=='mcpServerStatus/list':
            fact['servers']=[{'name':r['name'],'runtime_status':r['runtimeStatus'],
                'tools_count':len(r['tools']),'resources_count':len(r['resources']),
                'templates_count':len(r['resourceTemplates']),'capabilities_present':r['serverCapabilities']is not None}
                for r in result['data']if r['name']in REGISTERED_SERVERS]
            fact['unknown_server_count']=sum(r['name']not in REGISTERED_SERVERS for r in result['data'])
        else:
            fact['features']=[{'name':r['name'],'enabled':r['enabled']}for r in result['data']
                if r['name']in {*DISABLED_FEATURES,'code_mode_host'}]
            fact['other_feature_count']=len(result['data'])-len(fact['features'])
        self._record_capability(fact)

    def call(self,method,params,timeout=15):
        if method not in PARAMS:raise ServiceError('CODEX_METHOD_REJECTED')
        self._validate(PARAMS[method],params)
        self.next_id+=1;ident=self.next_id;self.pending_ids.add(ident)
        self._send({'id':ident,'method':method,'params':params})
        deadline=time.monotonic()+timeout
        try:
            while time.monotonic()<deadline:
                value=self._message(min(.2,max(.001,deadline-time.monotonic())))
                if value is None:continue
                if 'method'in value:
                    if len(self.notifications)>=256:raise ServiceError('CODEX_OUTPUT_CAPACITY',status=503,outcome='UNKNOWN')
                    self.notifications.append(value);continue
                if value.get('id')!=ident or ('result'in value)==('error'in value):
                    raise ServiceError('CODEX_RESPONSE_CONFLICT',status=503,outcome='UNKNOWN')
                if 'error'in value:
                    error=value['error'];message=str(error.get('message',''))
                    lowered=message.lower()
                    classes=[name for name,pattern in (
                        ('thread_unavailable',r'not found|no rollout|no saved|not persisted|not materialized|not loaded|unavailable|does not exist|has no|not initialized|unknown thread|unknown conversation'),
                        ('unsupported_store_operation',r'^[a-z_]+ is not supported yet$'),
                        ('method_unsupported',r'method not found|unknown method|unsupported method'),
                        ('invalid_parameters',r'invalid param|invalid request|deserialize|missing field|expected|unknown variant'))
                        if re.search(pattern,lowered)]
                    raise ServiceError('CODEX_RPC_REJECTED',status=503,details={
                        'method':method,
                        'rpc_code':error.get('code')if type(error.get('code'))is int else None,
                        'error_categories':classes,'message_sha256':hashlib.sha256(message.encode()).hexdigest(),
                        'message_bytes':len(message.encode()),'raw_message_retained':False})
                self._validate(RESPONSES[method],value['result'])
                self._capability_response(method,params,value['result'])
                return value['result']
            raise ServiceError('CODEX_TIMEOUT',status=503,outcome='UNKNOWN')
        finally:self.pending_ids.discard(ident)

    def poll(self,timeout=.2):
        if self.notifications:return self.notifications.pop(0)
        value=self._message(timeout)
        if value is not None and 'method'not in value:
            raise ServiceError('CODEX_RESPONSE_CONFLICT',status=503,outcome='UNKNOWN')
        return value

    def thread(self, *, previous=None):
        self.active_profile=None;self.capability_thread=None
        if previous:
            read=self.call('thread/read',{'threadId':previous,'includeTurns':False})['thread']
            # notLoaded is never an idle fact. Only a separately retained owned
            # exact completed turn and writer check permit this fixture resume.
            owned=self.owned_completion
            unloaded_owned=read['status']['type']=='notLoaded' and type(owned)is dict and owned.get('thread_id')==previous and owned.get('provider_status')=='completed' and bool(owned.get('turn_id'))
            if read['id']!=previous or Path(read['cwd'])!=self.root or not (read['status']['type']=='idle' or unloaded_owned):
                raise ServiceError('CODEX_THREAD_NOT_IDLE',status=409)
            result=self.call('thread/resume',{'threadId':previous,'cwd':str(self.root),'permissions':PERMISSION_PROFILE,'approvalPolicy':'on-request'})
        else:
            result=self.call('thread/start',{'cwd':str(self.root),'permissions':PERMISSION_PROFILE,'approvalPolicy':'on-request'})
        thread=result['thread']
        if previous and thread['id']!=previous or Path(thread['cwd'])!=self.root:
            raise ServiceError('CODEX_THREAD_BINDING_REJECTED',status=409)
        self._profile_echo(result)
        return thread['id']

    def _profile_echo(self,result):
        self.active_profile=None
        value=result.get('activePermissionProfile')
        if type(value)is not dict or value.get('id')!=PERMISSION_PROFILE or value.get('extends')is not None:
            raise ServiceError('CODEX_PROFILE_REJECTED',status=503)
        self.active_profile=PERMISSION_PROFILE

    def start(self,thread_id,text,image=None):
        if self.active_profile!=PERMISSION_PROFILE:raise ServiceError('CODEX_PROFILE_REJECTED',status=503)
        if config_fingerprint(Path(child_environment()['HOME'])/'.codex/config.toml')!=self._configuration_hash():
            raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
        if self.capability_thread!=thread_id:self.verify_capabilities(thread_id)
        inputs=[{'type':'text','text':text}]
        if image:inputs.append({'type':'localImage','path':str(image)})
        return self.call('turn/start',{'threadId':thread_id,'cwd':str(self.root),'input':inputs,
            'approvalPolicy':'on-request','permissions':PERMISSION_PROFILE,**self._turn_overrides()})['turn']['id']

    def verify_capabilities(self,thread_id):
        if self.active_profile!=PERMISSION_PROFILE:raise ServiceError('CODEX_PROFILE_REJECTED',status=503)
        if config_fingerprint(Path(child_environment()['HOME'])/'.codex/config.toml')!=self._configuration_hash():
            raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
        servers=self.call('mcpServerStatus/list',{'threadId':thread_id,'limit':100,'detail':'toolsAndAuthOnly'})
        if servers['nextCursor']is not None or any(s['runtimeStatus']!='disabled' or s['tools'] or
            s['resources'] or s['resourceTemplates'] or s['serverCapabilities']is not None for s in servers['data']):
            raise ServiceError('CODEX_CAPABILITY_UNVERIFIED',status=503)
        flags={};cursor=None
        for _ in range(8):
            result=self.call('experimentalFeature/list',{'threadId':thread_id,'limit':100,'cursor':cursor})
            for item in result['data']:
                if item['name']in flags:raise ServiceError('CODEX_CAPABILITY_PAGE_DUPLICATE',status=503)
                flags[item['name']]=item['enabled']
            cursor=result['nextCursor']
            if cursor is None:break
        if cursor is not None or any(flags.get(name)is not False for name in DISABLED_FEATURES)or flags.get('code_mode_host')is not True:
            raise ServiceError('CODEX_CAPABILITY_UNVERIFIED',status=503,
                details={'unverified_features':[name for name in DISABLED_FEATURES if flags.get(name)is not False]})
        if self.mcp_startup_seen or self.remote_control_status!='disabled':
            raise ServiceError('CODEX_CAPABILITY_UNVERIFIED',status=503,details={
                'mcp_startup_seen':self.mcp_startup_seen,'remote_control_status':self.remote_control_status})
        self.capability_evidence={'mcp_servers':0,'disabled_features':{name:flags[name]for name in DISABLED_FEATURES},
            'web_search_launch':'disabled','disabled_configured_servers':self.disabled_servers,
            'remote_control_status':'disabled',
            'remote_state_source':'last schema-validated notification, retained independently of polling',
            'local_native_tool_host':flags['code_mode_host'],
            'permission_profile':self.active_profile,'profile_argv':list(profile_arguments(self.root)),
            'argv':list(self.launch_command),'source':'native loaded-thread feature and MCP status APIs'}
        self.capability_thread=thread_id
        return self.capability_evidence

    def drain_baseline(self,thread_id):
        """Resume sends the prior turn's cumulative usage before the next turn.

        Bound and record that pre-dispatch snapshot without assigning it to the
        new turn. Once turn/start is sent, normal exact turn binding remains.
        """
        baseline=[]
        for _ in range(256):
            note=self.poll(0)
            if note is None:return baseline
            params=note.get('params',{})
            if params.get('threadId',thread_id)!=thread_id or note['method']in {'turn/started','turn/completed'}:
                raise ServiceError('CODEX_BASELINE_BINDING_REJECTED',status=409,outcome='UNKNOWN')
            baseline.append({'method':sanitized(note['method'],120),'thread_id':params.get('threadId'),
                'turn_id':params.get('turnId'),'source_emitted_ms':note.get('emittedAtMs'),
                'digest':hashlib.sha256(canonical(note)).hexdigest(),'phase':'before_turn_dispatch'})
        raise ServiceError('CODEX_OUTPUT_CAPACITY',status=503,outcome='UNKNOWN')

    def interrupt(self,thread_id,turn_id):
        return self.call('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})

    def check(self,argv):
        if self.active_profile!=PERMISSION_PROFILE or self.capability_thread is None:
            raise ServiceError('CODEX_PROFILE_REJECTED',status=503)
        if config_fingerprint(Path(child_environment()['HOME'])/'.codex/config.toml')!=self._configuration_hash():
            raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
        return self.call('command/exec',{'command':list(argv),'cwd':str(self.root),'permissionProfile':PERMISSION_PROFILE,
            'timeoutMs':5000,'outputBytesCap':4096})

    def close(self):
        if self.proc is None:return
        if self.proc.stdin and not self.proc.stdin.closed:self.proc.stdin.close()
        try:self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
        for reader in (self.reader,self.error_reader):reader.join(timeout=1)
        for stream in (self.proc.stdout,self.proc.stderr):stream.close()


DISCOVERY_SCHEMA_PINS = {
    'ThreadListParams': '5daf371b2852c409d4405c95f79cddb2d92a1ee89da808b2c5112ef16a57432c',
    'ThreadListResponse': '70d0fb1a5d747515459317281d9aa654e930122a174e8535c56de83b9f3b5dcf',
    'ThreadReadParams': 'dfe040c6ac71d30795b8be3f3ff232e66f362a37f883b491e5d1ea367f470db4',
    'ThreadReadResponse': '2af6b987644cfb23e6f408f3cfb29e73ad6255ba2294366d36d56aeabe3854da',
}


class ReadonlyAppServerAdapter(AppServerAdapter):
    """Discovery cannot acquire an execution capability through inherited methods."""
    methods = frozenset({'initialize', 'thread/list', 'thread/read'})

    def open(self):
        try:
            super().open()
            if any(self.schema_hashes.get(k) != v for k, v in DISCOVERY_SCHEMA_PINS.items()):
                raise ServiceError('CODEX_SCHEMA_CHANGED', status=503)
            return self
        except BaseException:
            self.close()
            raise

    def call(self, method, params, timeout=15):
        if method not in self.methods:
            raise ServiceError('DISCOVERY_METHOD_REJECTED', status=403)
        return super().call(method, params, timeout=timeout)

    def _send(self, value):
        if 'method' in value and value['method'] not in self.methods | {'initialized'}:
            raise ServiceError('DISCOVERY_METHOD_REJECTED', status=403)
        return super()._send(value)

    def _message(self, timeout):
        before = len(self.denials)
        value = super()._message(timeout)
        if len(self.denials) != before:
            raise ServiceError('DISCOVERY_PROVIDER_REQUEST_DENIED', status=503)
        return value
