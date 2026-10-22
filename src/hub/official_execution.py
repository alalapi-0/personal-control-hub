"""Opt-in official existing-thread transport. No TaskStore or execution admission.

Native readOnly means broad read access, never exact input-leaf isolation.
Future live use requires separate authority; construction performs no native work.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import time

import jsonschema
from .codex_adapter import child_environment, _official_binary_hash
from .service_contract import ServiceError
from .workbench_store import canonical

BINARY_SHA256 = '399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215'
VERSION = 'codex-cli 0.163.0'
MAX_FRAME = 1024 * 1024
MAX_NOTIFICATIONS = 64
INITIALIZE = {'clientInfo': {'name': 'personal_control_hub_execution', 'version': '1.1.0'},
              'capabilities': {'experimentalApi': False, 'explicitGatewayOauth': True}}
SCHEMA_PINS = {'InitializeParams': 'b55124e7c0f36a1789396daa5578217a92be67f053903696236686e59f6f212e', 'InitializeResponse': '62ad689c2cb6379913c1d72749cfd8de5089d35760214123518eb92eef11acc9', 'ItemCompletedNotification': '940dcfba0740e86a7e62b0eacf1bb53c634fdbe8b75fa3163b6e366a55f85fae', 'RemoteControlStatusChangedNotification': '56aa1d58294238557cd8b16199909c2c157a7542b9259ef2deddf40fe4c02c91', 'ThreadReadParams': 'dfe040c6ac71d30795b8be3f3ff232e66f362a37f883b491e5d1ea367f470db4', 'ThreadReadResponse': '59d458102e90380c1a436346aba78ad3cd64178b022113a63a0d0731a925d60f', 'ThreadResumeParams': '8dceca415b2c16693b55658ea629cba5ffc82ac300dcd403db0ba7455e491496', 'ThreadResumeResponse': '3f19a9c3a9891526685b0f61ce781098ddd65bfa2aed8c24ba4722fef10e08fe', 'ThreadStatusChangedNotification': '26f3c60c1b73f7fa2d31c74429cdc36f8746c76c33e3d314b3fb61d3661f05f6', 'TurnCompletedNotification': '69413578ec3ecd02691424bdeb9d640c7244b4ca4c5c3ef26f69a2efe7c37db8', 'TurnInterruptParams': '6dff382dae73d1dbc58406ed045605f647e7a49660e2540fbd2c6c24d60c5f2b', 'TurnInterruptResponse': '531de6be06fe979b5963f249bab82498a175e614bf65ac12fb2e849dfe60bcf1', 'TurnStartParams': '835e0d4ca1a0ddc4fb6db1e2b3249dd67f07c50ac2506092ba81543efc92829b', 'TurnStartResponse': '20e837d07826b9c01578ff40b962ab08f58a355a9984e96226733498514e9bad', 'TurnStartedNotification': '74050381ae8a90db4893fc15fc26ef14560762c3d320c3fa8d9df9b1aba1273b'}
METHODS = {'initialize': ('InitializeParams', 'InitializeResponse'),
           'thread/resume': ('ThreadResumeParams', 'ThreadResumeResponse'),
           'turn/start': ('TurnStartParams', 'TurnStartResponse'),
           'turn/interrupt': ('TurnInterruptParams', 'TurnInterruptResponse')}
PASSIVE_NOTIFICATIONS = {
    'account/updated': 'AccountUpdatedNotification',
    'account/rateLimits/updated': 'AccountRateLimitsUpdatedNotification',
    'thread/tokenUsage/updated': 'ThreadTokenUsageUpdatedNotification',
    'item/started': 'ItemStartedNotification',
    'item/agentMessage/delta': 'AgentMessageDeltaNotification',
    'mcpServer/startupStatus/updated': 'McpServerStatusUpdatedNotification'}
SCHEMA_PINS.update({
    'AccountUpdatedNotification': '268dba6977cc3adcced751a97367772a1e201a199c14f454741d851fe8bbacb1',
    'AccountRateLimitsUpdatedNotification': '37326ef0364007be0c8f92bf5cd228941b513f87266edd566d694409b5aa5b8b',
    'ThreadTokenUsageUpdatedNotification': 'aba4f6c7e4a19b2b842c08ee793b57000c07dafd57b922ad0d8e7c76609108c2',
    'ItemStartedNotification': '692c5876f44a4c12a9b9211912860ceddb65d34497ab4fe9aa46cbebe1a63166',
    'AgentMessageDeltaNotification': '996e6c0ea65e57bed5a00f410b94381fe5ebf804333e5d00c2b6e6d47e5c55f6',
    'McpServerStatusUpdatedNotification': 'd6f4cb120b2363d321247592f03b9afbbf2762ad6d8af31b88c1b1bf39c4c7aa'})
NOTIFICATIONS = {'remoteControl/status/changed': 'RemoteControlStatusChangedNotification',
                 'thread/status/changed': 'ThreadStatusChangedNotification',
                 'turn/started': 'TurnStartedNotification', 'turn/completed': 'TurnCompletedNotification',
                 'item/completed': 'ItemCompletedNotification', **PASSIVE_NOTIFICATIONS}
GLOBAL_NOTIFICATIONS = frozenset(('remoteControl/status/changed', 'account/updated',
                                  'account/rateLimits/updated'))
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_RESUME_ERROR_FIELDS = {
    (): 'ROOT', ('thread',): 'THREAD', ('cwd',): 'CWD',
    ('model',): 'MODEL', ('modelProvider',): 'MODEL_PROVIDER',
    ('approvalPolicy',): 'APPROVAL_POLICY', ('approvalsReviewer',): 'APPROVALS_REVIEWER',
    ('sandbox',): 'SANDBOX', ('sandbox','type'): 'SANDBOX_TYPE',
    ('sandbox','networkAccess'): 'SANDBOX_NETWORK_ACCESS',
    ('collaborationMode',): 'COLLABORATION_MODE',
    ('reasoningEffort',): 'REASONING_EFFORT', ('serviceTier',): 'SERVICE_TIER',
    ('thread','id'): 'THREAD_ID', ('thread','sessionId'): 'THREAD_SESSION_ID',
    ('thread','cwd'): 'THREAD_CWD', ('thread','turns'): 'THREAD_TURNS',
    ('thread','status'): 'THREAD_STATUS', ('thread','status','type'): 'THREAD_STATUS_TYPE',
    ('thread','source'): 'THREAD_SOURCE', ('thread','cliVersion'): 'THREAD_CLI_VERSION',
    ('thread','createdAt'): 'THREAD_CREATED_AT', ('thread','updatedAt'): 'THREAD_UPDATED_AT',
    ('thread','ephemeral'): 'THREAD_EPHEMERAL', ('thread','preview'): 'THREAD_PREVIEW',
    ('thread','projectId'): 'THREAD_PROJECT_ID', ('thread','modelProvider'): 'THREAD_MODEL_PROVIDER'}
_RESUME_ERROR_VALIDATORS = {
    'required': 'REQUIRED', 'type': 'TYPE', 'enum': 'ENUM',
    'oneOf': 'ONE_OF', 'anyOf': 'ANY_OF', 'allOf': 'ALL_OF',
    'additionalProperties': 'ADDITIONAL_PROPERTIES', 'pattern': 'PATTERN',
    'minItems': 'MIN_ITEMS', 'maxItems': 'MAX_ITEMS', 'format': 'FORMAT'}


def _resume_schema_error_code(error):
    # Diagnostic classification only, not protocol success, permission evidence
    # or exact-leaf isolation. Never render messages, values or unknown key names.
    path=tuple(error.absolute_path)
    if len(path)>3 or any(type(key) is not str for key in path):path=None
    validator=error.validator
    kind=_RESUME_ERROR_VALIDATORS.get(validator,'OTHER') if type(validator) is str else 'OTHER'
    field=_RESUME_ERROR_FIELDS.get(path,'OTHER')
    if type(validator) is str and validator=='required':
        field='OTHER'
        required=error.validator_value;instance=error.instance
        if type(required) is list and len(required)<=32 and type(instance) is dict:
            for key in required:
                if type(key) is not str:break
                if key not in instance:
                    field=_RESUME_ERROR_FIELDS.get(path+(key,),'OTHER') if path is not None else 'OTHER'
                    break
    return 'OFFICIAL_RESUME_SCHEMA_'+field+'_'+kind+'_REJECTED'


def _error(code, outcome='NOT_COMMITTED'):
    return ServiceError(code, status=503, outcome=outcome)


def _stable_resume_response(result):
    # Passive response compatibility only: no permission or path authority.
    if (any(name in result and result[name] is not None
            for name in ('activePermissionProfile', 'initialTurnsPage'))
            or 'multiAgentMode' in result and (type(result['multiAgentMode']) is not str
                or result['multiAgentMode'] != 'explicitRequestOnly')):
        raise _error('OFFICIAL_RESUME_METADATA_REJECTED', 'UNKNOWN')
    if 'runtimeWorkspaceRoots' in result:
        roots = result['runtimeWorkspaceRoots']
        if (type(roots) is not list or len(roots) > 64
                or any(type(value) is not str or not 1 <= len(value) <= 4096
                    or not value.startswith('/') or '\x00' in value for value in roots)):
            raise _error('OFFICIAL_RESUME_METADATA_REJECTED', 'UNKNOWN')
    stable = result.copy()
    for name in ('activePermissionProfile', 'initialTurnsPage',
                 'multiAgentMode', 'runtimeWorkspaceRoots'):
        stable.pop(name, None)
    return stable


def _ticks(pid):
    # Only the exact owned child, never a process-name search.
    text = Path(f'/proc/{pid}/stat').read_text()
    return int(text[text.rfind(')')+2:].split()[19])


@dataclass(frozen=True)
class ExecutionConfig:
    thread_id: str
    cwd: Path
    schema_dir: Path
    binary: Path
    max_seconds: int = 60

    def __post_init__(self):
        if (type(self.thread_id) is not str or not UUID.fullmatch(self.thread_id)
                or type(self.max_seconds) is not int or not 4 <= self.max_seconds <= 900):
            raise _error('OFFICIAL_EXECUTION_CONFIG_REJECTED')
        for key in ('cwd', 'schema_dir', 'binary'):
            value = getattr(self, key)
            if (not isinstance(value, (str, Path)) or not Path(value).is_absolute()
                    or '..' in Path(value).parts or str(Path(value)) != str(value)):
                raise _error('OFFICIAL_EXECUTION_CONFIG_REJECTED')
            object.__setattr__(self, key, Path(value))


def load_schemas(directory):
    result = {}
    try:
        for name, pin in SCHEMA_PINS.items():
            path = directory / ('v1' if name.startswith('Initialize') else 'v2') / (name+'.json')
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
                raw = stream.read(MAX_FRAME+1)
            if len(raw) > MAX_FRAME or hashlib.sha256(raw).hexdigest() != pin:
                raise ValueError('pin')
            schema = json.loads(raw)
            jsonschema.validators.validator_for(schema).check_schema(schema)
            result[name] = _closed_notification_schema(schema) if name in PASSIVE_NOTIFICATIONS.values() else schema
    except (OSError, ValueError, jsonschema.SchemaError):
        raise _error('OFFICIAL_SCHEMA_REJECTED') from None
    return result


def _closed_notification_schema(schema):
    # Generated IDL omits additionalProperties:false. Close declared structures,
    # including nested variants; preserve explicit maps and opaque JSON values.
    result = copy.deepcopy(schema)
    def close(node):
        if type(node) is dict:
            if node.get('type') == 'object' or 'properties' in node:
                node.setdefault('additionalProperties', False)
            for value in node.values():close(value)
        elif type(node) is list:
            for value in node:close(value)
    close(result)
    return result


class _ProcessWire:
    """Synchronous bounded pipes; no reader threads or credential inspection."""
    def __init__(self, command, cwd, env, clock):
        self.clock, self._identity, self.buffer = clock, None, b''
        self.stderr_hash, self.stderr_bytes = hashlib.sha256(), 0
        self.startup_error = False
        self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            pid = self.proc.pid
            self._identity = (pid, None, None)
            self._identity = (pid, _ticks(pid), None)
            self._identity = (pid, self._identity[1], os.getpgid(pid))
            if self.identity['pgid'] != pid:
                raise ValueError('group')
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                os.set_blocking(stream.fileno(), False)
        except BaseException:
            # Return the owned handle so the caller retains any uncertain closure.
            self.startup_error = True

    @property
    def identity(self):
        return dict(zip(('pid','start_ticks','pgid'), self._identity)) if self._identity is not None else None

    def send(self, data, deadline):
        view = memoryview(data)
        while view:
            remaining = deadline-self.clock()
            if remaining <= 0: raise TimeoutError()
            if not select.select([], [self.proc.stdin], [], remaining)[1]: raise TimeoutError()
            try: size = os.write(self.proc.stdin.fileno(), view)
            except BlockingIOError: continue
            if size <= 0: raise EOFError()
            view = view[size:]

    def receive(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline-self.clock()
            if remaining <= 0: return None
            ready = select.select([self.proc.stdout, self.proc.stderr], [], [], remaining)[0]
            if not ready: return None
            for stream in ready:
                chunk = os.read(stream.fileno(), 65536)
                if stream is self.proc.stderr:
                    if not chunk: continue
                    self.stderr_hash.update(chunk); self.stderr_bytes += len(chunk)
                    if self.stderr_bytes > 65536: raise ValueError('stderr capacity')
                else:
                    if not chunk: raise EOFError()
                    self.buffer += chunk
                    if len(self.buffer) > MAX_FRAME: raise ValueError('frame capacity')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return line

    def close(self):
        result = {'status': 'UNKNOWN', 'identity': self.identity}
        try:
            if self.identity is None or any(type(v) is not int or v<=0 for v in self.identity.values()): return result
            pid, pgid = self.identity['pid'], self.identity['pgid']
            if pgid != pid:return result
            def group_absent():
                try: os.killpg(pgid, 0)
                except ProcessLookupError: return True
                return False
            self.proc.stdin.close()
            try: self.proc.wait(timeout=.2)
            except subprocess.TimeoutExpired: pass
            if self.proc.poll() is not None and group_absent():
                result['status'] = 'CLOSED'; return result
            # Refuse foreign/reused or unverifiable identities. Never broad-kill.
            if _ticks(pid) != self.identity['start_ticks'] or os.getpgid(pid) != pgid or pgid != pid:
                return result
            os.killpg(pgid, signal.SIGTERM)
            try: self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if _ticks(pid) != self.identity['start_ticks'] or os.getpgid(pid) != pgid: return result
                os.killpg(pgid, signal.SIGKILL)
                self.proc.wait(timeout=1)
            if group_absent(): result['status'] = 'CLOSED'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try: stream.close()
                except OSError: pass
        return result


def _managed(method):
    def run(self, *args, **kwargs):
        try:
            if self.poisoned or self.closed:
                raise _error('OFFICIAL_EXECUTION_CLOSED', self.failure['outcome'] if self.failure else 'NOT_COMMITTED')
            return method(self, *args, **kwargs)
        except BaseException as error:
            self.poisoned = True
            failure = {'code': error.code if isinstance(error, ServiceError) else 'OFFICIAL_TRANSPORT_FAILED',
                            'outcome': error.outcome if isinstance(error, ServiceError) else
                            ('UNKNOWN' if self.sent else 'NOT_COMMITTED')}
            if self.failure is None:self.failure=failure
            self.close()
            if isinstance(error, (KeyboardInterrupt, SystemExit)): raise
            if isinstance(error, ServiceError):
                raise _error(error.code if error.code.startswith('OFFICIAL_') else 'OFFICIAL_TRANSPORT_FAILED',
                             error.outcome) from None
            raise _error('OFFICIAL_TRANSPORT_FAILED', self.failure['outcome']) from None
    return run


class OfficialExecution:
    """One existing thread, one turn, one interrupt; no generic public RPC."""
    def __init__(self, config, *, enabled=False, wire_factory=None, clock=time.monotonic):
        if enabled is not True or type(config) is not ExecutionConfig:
            raise _error('OFFICIAL_EXECUTION_OPT_IN_REQUIRED')
        self.config, self.clock = config, clock
        self.wire_factory = wire_factory or _ProcessWire
        self.wire, self.schemas = None, {}
        self.process_identity = None
        self.pending = False
        self.sent, self.events = [], []
        self.item_ids = set()
        self.items = {}  # Bounded independent ID -> (type, completed); no bodies.
        self.native_thread_id = self.session_id = self.turn_id = None
        self.session_reason = 'NOT_OBSERVED'
        self.initialized = self.resumed = self.started_event = self.start_reply = False
        self.terminal = None
        self.poisoned = self.closed = False
        self.closure = {'status': 'NOT_STARTED', 'identity': None}
        self.failure = None
        self.deadline = None

    def _validate(self, name, value):
        try: jsonschema.validate(value, self.schemas[name])
        except jsonschema.ValidationError as error:
            code=_resume_schema_error_code(error) if name=='ThreadResumeResponse' else 'OFFICIAL_PROTOCOL_REJECTED'
            raise _error(code, 'UNKNOWN' if self.sent else 'NOT_COMMITTED') from None
        except (jsonschema.SchemaError, KeyError):
            raise _error('OFFICIAL_PROTOCOL_REJECTED', 'UNKNOWN' if self.sent else 'NOT_COMMITTED') from None

    @_managed
    def open(self):
        if self.wire is not None: raise _error('OFFICIAL_SEQUENCE_REJECTED')
        self.schemas = load_schemas(self.config.schema_dir)
        if _official_binary_hash(self.config.binary) != BINARY_SHA256:
            raise _error('OFFICIAL_BINARY_REJECTED')
        for path in (self.config.cwd, self.config.schema_dir, self.config.binary):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise _error('OFFICIAL_INPUT_ALIAS')
        self.deadline = self.clock()+self.config.max_seconds-3  # Reserve bounded child closure.
        self.wire = self.wire_factory((str(self.config.binary), 'app-server'), self.config.cwd,
                                      child_environment(), self.clock)
        identity = self.wire.identity
        self.process_identity = copy.deepcopy(identity)
        if (type(identity) is not dict or set(identity) != {'pid','start_ticks','pgid'}
                or any(type(v) is not int or v <= 0 for v in identity.values())
                or identity['pgid'] != identity['pid'] or getattr(self.wire, 'startup_error', False)):
            raise _error('OFFICIAL_PROCESS_IDENTITY_UNKNOWN', 'UNKNOWN')
        self.process_identity = dict(identity)
        self._rpc('initialize', copy.deepcopy(INITIALIZE))
        self.wire.send(canonical({'method':'initialized','params':{}})+b'\n', self._deadline())
        self.initialized = True
        return self

    def _deadline(self):
        if self.deadline is None or self.clock() >= self.deadline:
            raise _error('OFFICIAL_TIMEOUT', 'UNKNOWN' if self.sent else 'NOT_COMMITTED')
        return self.deadline

    def _frame(self, deadline):
        raw = self.wire.receive(deadline)
        if self.clock() >= deadline or raw is None: raise _error('OFFICIAL_TIMEOUT', 'UNKNOWN')
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME: raise _error('OFFICIAL_FRAME_REJECTED','UNKNOWN')
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result: raise ValueError('duplicate')
                result[key] = value
            return result
        try: value = json.loads(raw, object_pairs_hook=pairs,
            parse_float=lambda x: float(x) if math.isfinite(float(x)) else (_ for _ in ()).throw(ValueError()),
            parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError): raise _error('OFFICIAL_FRAME_REJECTED','UNKNOWN') from None
        if type(value) is not dict: raise _error('OFFICIAL_FRAME_REJECTED','UNKNOWN')
        return value

    def _notify(self, value):
        method = value.get('method')
        if ('id' in value or method not in NOTIFICATIONS or set(value)-{'method','params','emittedAtMs'}):
            raise _error('OFFICIAL_SERVER_REQUEST_REJECTED','UNKNOWN')
        timestamp=value.get('emittedAtMs')
        if 'emittedAtMs' in value and (type(timestamp) not in (int,float) or not math.isfinite(timestamp)
                                       or not 0 <= timestamp <= 2**53-1):
            raise _error('OFFICIAL_FRAME_REJECTED','UNKNOWN')
        params=value.get('params'); self._validate(NOTIFICATIONS[method],params)
        if type(params) is not dict or set(params)-set(self.schemas[NOTIFICATIONS[method]].get('properties',{})):
            raise _error('OFFICIAL_PROTOCOL_REJECTED','UNKNOWN')
        if len(self.events) >= MAX_NOTIFICATIONS: raise _error('OFFICIAL_NOTIFICATION_CAPACITY','UNKNOWN')
        if method == 'mcpServer/startupStatus/updated':
            # Nullable/global startup metadata cannot establish native identity
            # or trigger authentication. Scoped observations need a sent resume.
            thread=params.get('threadId')
            if thread is not None and (thread!=self.config.thread_id or 'thread/resume' not in self.sent):
                raise _error('OFFICIAL_NOTIFICATION_BINDING_REJECTED','UNKNOWN')
        elif method not in GLOBAL_NOTIFICATIONS:
            if params['threadId'] != self.config.thread_id or 'thread/resume' not in self.sent:
                raise _error('OFFICIAL_NOTIFICATION_BINDING_REJECTED','UNKNOWN')
            if method in ('turn/started','turn/completed'):
                turn=params['turn']; native=turn['id']
                if 'turn/start' not in self.sent or not UUID.fullmatch(native):
                    raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                if self.turn_id is not None and self.turn_id != native:
                    raise _error('OFFICIAL_TURN_MISMATCH','UNKNOWN')
                if method=='turn/started':
                    if self.started_event or self.terminal or turn['status']!='inProgress':
                        raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                    self.started_event=True;self.turn_id=native
                else:
                    if not (self.started_event or self.start_reply) or self.terminal or turn['status'] not in ('completed','interrupted','failed'):
                        raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                    self.turn_id=native;self.terminal=turn['status']
            elif method in ('thread/tokenUsage/updated','item/started','item/agentMessage/delta','item/completed'):
                # Pending-start telemetry may describe an older turn; observe
                # it without binding any identity or changing turn lifecycle.
                pending_token = (method=='thread/tokenUsage/updated' and self.pending
                    and self.sent[-1:]==['turn/start'] and self.turn_id is None
                    and type(params['turnId']) is str and UUID.fullmatch(params['turnId']))
                if (not self.resumed or self.native_thread_id != self.config.thread_id
                        or self.terminal or not pending_token and (
                            self.turn_id is None or params['turnId']!=self.turn_id)):
                    raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                if method.startswith('item/'):
                    item=params.get('item')
                    ident=params.get('itemId') if method=='item/agentMessage/delta' else item.get('id')
                    if type(ident) is not str or not 0<len(ident)<=256:
                        raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                    prior=self.items.get(ident)
                    if method=='item/started':
                        kind=item.get('type')
                        if prior is not None or type(kind) is not str or not 0<len(kind)<=128:
                            raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                        self.items[ident]=(kind,False)
                    elif method=='item/agentMessage/delta':
                        if prior != ('agentMessage',False):
                            raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                    else:
                        kind=item.get('type')
                        # The original completion-only stream may introduce a
                        # finished item, never a live delta target. Explicitly
                        # started items must still match and complete once.
                        if (type(kind) is not str or not 0<len(kind)<=128
                                or prior is not None and (prior[1] or kind!=prior[0])):
                            raise _error('OFFICIAL_NOTIFICATION_ORDER_REJECTED','UNKNOWN')
                        self.items[ident]=(kind,True)
                        self.item_ids.add(ident)
        # Hash and order only; never retain provider bodies or remote identities.
        encoded=canonical(value)
        self.events.append({'sequence':len(self.events)+1,'method':method,
                            'size_bytes':len(encoded),'sha256':hashlib.sha256(encoded).hexdigest()})

    def _rpc(self, method, params):
        if method not in METHODS or method in self.sent or self.pending:
            raise _error('OFFICIAL_METHOD_OR_REPLAY_REJECTED')
        if method == 'initialize':
            valid = params == INITIALIZE and not self.initialized
        elif method == 'thread/resume':
            valid = self.initialized and not self.resumed and params == {
                'threadId':self.config.thread_id,'cwd':str(self.config.cwd),'sandbox':'read-only',
                'approvalPolicy':'never','approvalsReviewer':'user','excludeTurns':True}
        elif method == 'turn/start':
            valid = (self.resumed and type(params) is dict and set(params)=={
                'threadId','cwd','input','sandboxPolicy','approvalPolicy','approvalsReviewer'}
                and params['threadId']==self.config.thread_id and params['cwd']==str(self.config.cwd)
                and params['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
                and params['sandboxPolicy'].get('networkAccess') is False
                and params['approvalPolicy']=='never' and params['approvalsReviewer']=='user'
                and type(params['input']) is list and len(params['input'])==1
                and type(params['input'][0]) is dict and set(params['input'][0])=={'type','text'}
                and params['input'][0]['type']=='text' and type(params['input'][0]['text']) is str)
        else:
            valid = (self.start_reply and not self.terminal and params=={
                'threadId':self.config.thread_id,'turnId':self.turn_id})
        if not valid:raise _error('OFFICIAL_SEQUENCE_REJECTED')
        return self._exchange(method,params)

    def _exchange(self, method, params):
        if method not in METHODS or method in self.sent:
            raise _error('OFFICIAL_METHOD_OR_REPLAY_REJECTED')
        self._validate(METHODS[method][0], params)
        deadline=self._deadline(); ident=len(self.sent)+1
        encoded=canonical({'id':ident,'method':method,'params':params})+b'\n'
        if len(encoded)>MAX_FRAME:raise _error('OFFICIAL_INPUT_CAPACITY')
        self.pending=True
        self.sent.append(method)  # Recorded before any possibly partial write; never replay.
        self.wire.send(encoded,deadline)
        while True:
            value=self._frame(deadline)
            if 'method' in value:
                self._notify(value); continue
            if (set(value) not in ({'id','result'},{'id','error'}) or type(value.get('id')) is not int or value['id']!=ident):
                raise _error('OFFICIAL_RESPONSE_CONFLICT','UNKNOWN')
            if 'error' in value:
                error=value['error']
                if (type(error) is not dict or set(error)-{'code','message','data'}
                        or type(error.get('code')) is not int or type(error.get('message')) is not str):
                    raise _error('OFFICIAL_PROTOCOL_REJECTED','UNKNOWN')
                # A sent request may have taken effect before an RPC error.
                # Neither an error code nor an absent turn ID proves no effect;
                # no pinned conclusive no-effect evidence is available here.
                raise _error('OFFICIAL_RPC_REFUSED','UNKNOWN')
            result=value['result']
            if method=='thread/resume' and type(result) is dict:
                result=_stable_resume_response(result)
            schema=self.schemas[METHODS[method][1]]
            if type(result) is not dict or set(result)-set(schema.get('properties',{})):
                if method=='thread/resume' and type(result) is dict:
                    raise _error('OFFICIAL_RESUME_ROOT_FIELDS_REJECTED','UNKNOWN')
                raise _error('OFFICIAL_PROTOCOL_REJECTED','UNKNOWN')
            if method=='thread/resume' and type(result) is dict and type(result.get('thread')) is dict:
                # Session identity may be unavailable, never borrowed from thread ID.
                schema=copy.deepcopy(self.schemas[METHODS[method][1]])
                thread=schema.get('definitions',{}).get('Thread')
                if thread:
                    thread['required']=[v for v in thread['required'] if v!='sessionId']
                    thread['properties']['sessionId']['type']=['string','null']
                try: jsonschema.validate(result,schema)
                except jsonschema.ValidationError as error:
                    raise _error(_resume_schema_error_code(error),'UNKNOWN') from None
            else:self._validate(METHODS[method][1],result)
            self.pending=False
            return result

    @_managed
    def resume(self, *, sandbox='readOnly', read_paths=None, write_paths=None, exact_input_leaves=False):
        if sandbox!='readOnly' or read_paths is not None or write_paths is not None or exact_input_leaves is not False:
            raise _error('OFFICIAL_CAPABILITY_UNSUPPORTED')
        if not self.initialized or self.resumed: raise _error('OFFICIAL_SEQUENCE_REJECTED')
        result=self._rpc('thread/resume',{'threadId':self.config.thread_id,'cwd':str(self.config.cwd),
            'sandbox':'read-only','approvalPolicy':'never','approvalsReviewer':'user','excludeTurns':True})
        thread=result['thread'];policy=result['sandbox']
        if (thread['id']!=self.config.thread_id or thread['cwd']!=str(self.config.cwd)
                or result['cwd']!=str(self.config.cwd) or thread['turns']!=[]
                or thread.get('status',{}).get('type')!='idle'
                or result['approvalPolicy']!='never' or result['approvalsReviewer']!='user'
                or policy.get('type')!='readOnly' or policy.get('networkAccess',False) is not False
                or set(policy)-{'type','networkAccess'}):
            raise _error('OFFICIAL_RESUME_BINDING_REJECTED','UNKNOWN')
        session=thread.get('sessionId')
        if session is not None and (type(session) is not str or not UUID.fullmatch(session)):
            raise _error('OFFICIAL_SESSION_REJECTED','UNKNOWN')
        self.native_thread_id=thread['id'];self.session_id=session
        self.session_reason=None if session is not None else 'NATIVE_SESSION_UNAVAILABLE'
        self.resumed=True
        return self.snapshot()

    @_managed
    def start(self, text, *, sandbox='readOnly', read_paths=None, write_paths=None, exact_input_leaves=False):
        if sandbox!='readOnly' or read_paths is not None or write_paths is not None or exact_input_leaves is not False:
            raise _error('OFFICIAL_CAPABILITY_UNSUPPORTED')
        if not self.resumed or 'turn/start' in self.sent or type(text) is not str or not 0<len(text.encode())<=65536:
            raise _error('OFFICIAL_SEQUENCE_REJECTED')
        result=self._rpc('turn/start',{'threadId':self.config.thread_id,'cwd':str(self.config.cwd),
            'input':[{'type':'text','text':text}],'sandboxPolicy':{'type':'readOnly','networkAccess':False},
            'approvalPolicy':'never','approvalsReviewer':'user'})
        turn=result['turn'];native=turn['id']
        if (not UUID.fullmatch(native) or self.turn_id is not None and self.turn_id!=native
                or self.terminal or turn['status']!='inProgress'):
            raise _error('OFFICIAL_TURN_MISMATCH','UNKNOWN')
        self.turn_id=native;self.start_reply=True
        return self.snapshot()

    @_managed
    def interrupt(self, turn_id):
        if not self.start_reply or self.terminal or turn_id!=self.turn_id or type(turn_id) is not str:
            raise _error('OFFICIAL_INTERRUPT_BINDING_REJECTED')
        result=self._rpc('turn/interrupt',{'threadId':self.config.thread_id,'turnId':self.turn_id})
        if result!={}:raise _error('OFFICIAL_PROTOCOL_REJECTED','UNKNOWN')
        return self.snapshot()

    @_managed
    def wait(self):
        if not self.start_reply:raise _error('OFFICIAL_SEQUENCE_REJECTED')
        deadline=self._deadline()
        while not self.terminal:
            value=self._frame(deadline)
            if 'method' not in value:raise _error('OFFICIAL_RESPONSE_CONFLICT','UNKNOWN')
            self._notify(value)
        self.close()
        return self.snapshot()

    def close(self):
        if self.closed:return copy.deepcopy(self.closure)
        self.closed=True
        if self.wire is not None:
            try:
                result=self.wire.close()
                status='CLOSED' if (type(self.process_identity) is dict
                    and set(self.process_identity)=={'pid','start_ticks','pgid'}
                    and all(type(v) is int and v>0 for v in self.process_identity.values())
                    and self.process_identity['pid']==self.process_identity['pgid']
                    and type(result) is dict and result.get('status')=='CLOSED'
                    and result.get('identity')==self.process_identity) else 'UNKNOWN'
                self.closure={'status':status,'identity':copy.deepcopy(self.process_identity)}
            except BaseException:self.closure={'status':'UNKNOWN','identity':copy.deepcopy(self.process_identity)}
        return copy.deepcopy(self.closure)

    def snapshot(self):
        return {'configured_thread_id':self.config.thread_id,'native_thread_id':self.native_thread_id,
                'thread_id':self.native_thread_id,'session_id':self.session_id,'session_reason':self.session_reason,
                'turn_id':self.turn_id,'request_methods':list(self.sent),
                'requests':[{'id':i+1,'method':method}for i,method in enumerate(self.sent)],'process':copy.deepcopy(self.process_identity),
                'events':copy.deepcopy(self.events),'terminal':self.terminal,'closure':copy.deepcopy(self.closure),'failure':copy.deepcopy(self.failure),
                'permission_boundary':'broad_standard_readOnly','exact_leaf_isolation':False,
                'native_execution_admitted':False}

    def __enter__(self):return self.open()
    def __exit__(self,*exc):self.close()
