"""Frozen process defaults with a nonsecret, task-scoped safety projection."""
import hashlib
import json
import os
import re
import stat
import tomllib
from pathlib import Path

from .service_contract import ServiceError
from . import css_trial as c

CONTRACT='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v7-PROCESS-CONFIG'
MODEL='gpt-6.1-sol'
EFFORT='xhigh'
OVERRIDES=('-c','model="'+MODEL+'"','-c','model_reasoning_effort="'+EFFORT+'"')


def structure(value):
    if isinstance(value,dict):return {k:structure(v)for k,v in value.items()}
    if isinstance(value,list):return {'type':'list','item_types':sorted({type(v).__name__ for v in value})}
    return {'type':type(value).__name__}


def protected_projection(value):
    top={k:type(v).__name__ for k,v in value.items()}
    if top!=c.CONFIG_TOP_TYPES:raise ServiceError('CSS_PROCESS_CONFIG_STRUCTURE_REJECTED')
    mcp={k:{n:type(v).__name__ for n,v in row.items()}for k,row in value['mcp_servers'].items()}
    if mcp!=c.CONFIG_MCP_TYPES:raise ServiceError('CSS_PROCESS_MCP_STRUCTURE_REJECTED')
    def safe(node):
        if isinstance(node,list):return [safe(v)for v in node]
        if not isinstance(node,dict):return node
        return {k:structure(v)if k=='mcp_servers'or re.search(r'(^|[_-])(env|header|headers|token|auth|credential|credentials|password|secret|command|args)($|[_-])',k,re.I)
            else safe(v)for k,v in node.items()}
    protected=safe({k:v for k,v in value.items()if k not in ('model','model_reasoning_effort')})
    return {'protected_sha256':c.digest(protected),'top_key_types':top,'sensitive_structure':structure(value['mcp_servers']),
        'mcp_names':sorted(mcp),'excluded_default_fields':['model','model_reasoning_effort'],
        'secret_values_or_hashes_retained':False}


def observe():
    from .codex_adapter import child_environment
    path=Path(child_environment()['HOME'])/'.codex/config.toml'
    if any(p.is_symlink()for p in path.parents):raise ServiceError('CSS_PROCESS_CONFIG_ALIAS')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb')as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode)or before.st_nlink!=1 or before.st_uid!=os.getuid()or before.st_size>1048576:
            raise ServiceError('CSS_PROCESS_CONFIG_UNSAFE')
        raw=stream.read(1048577);after=os.fstat(stream.fileno());host=path.lstat()
    fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid','st_nlink')
    if any(getattr(before,k)!=getattr(after,k)or getattr(after,k)!=getattr(host,k)for k in fields):
        raise ServiceError('CSS_PROCESS_CONFIG_UNSTABLE_READ')
    value=tomllib.loads(raw.decode());projection=protected_projection(value)
    return {'projection':projection,'identity':[getattr(after,k)for k in fields[:5]],
        'default_model_is_frozen':value['model']==MODEL,'default_effort_is_frozen':value['model_reasoning_effort']==EFFORT,
        '_private_content_hash':hashlib.sha256(raw).hexdigest()}


def public_observation(observation):
    return {k:v for k,v in observation.items()if not k.startswith('_private')}


def verify(pin):
    first=observe();c.auth_check();second=observe()
    if first['projection']!=pin or second['projection']!=pin:raise ServiceError('CSS_PROCESS_PROTECTED_CONFIG_DRIFT')
    return second


def configuration_fact(result):
    # Process response is consumed in memory; no layers, origins, secrets or body.
    config=result['config']
    if config.get('model')!=MODEL or config.get('model_reasoning_effort')!=EFFORT:
        raise ServiceError('CSS_PROCESS_DEFAULTS_UNVERIFIED')
    return {'model':MODEL,'reasoning_effort':EFFORT,'source':'native config/read effective process snapshot','body_retained':False}


def preflight_adapter(pin):
    from .codex_adapter import AppServerAdapter,PARAMS,RESPONSES,SCHEMA_PINS
    class ProcessDiagnostic(AppServerAdapter):
        methods=frozenset({'initialize','config/read','mcpServerStatus/list','experimentalFeature/list'})
        def _configuration_hash(self):return verify(pin)['_private_content_hash']
        def _send(self,value):
            if value.get('method')not in self.methods|{'initialized'}:raise ServiceError('CSS_PROCESS_DIAGNOSTIC_METHOD_REJECTED')
            return super()._send(value)
        def call(self,method,params,timeout=15):
            if method not in self.methods:raise ServiceError('CSS_PROCESS_DIAGNOSTIC_METHOD_REJECTED')
            if method=='config/read':
                if getattr(self,'read_done',False)or params!={'includeLayers':False}:raise ServiceError('CSS_PROCESS_CONFIG_READ_REJECTED')
                self.read_done=True
            return super().call(method,params,timeout)
    return ProcessDiagnostic(c.TRIAL,command=('codex','app-server','--stdio',*OVERRIDES))
