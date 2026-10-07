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
SEMANTIC_CONTRACT='HUB-LWB-2.3-CSP-CONFIG-REBASELINE-GOV-v11'
SOURCE_VERSION='rust-v0.159.3'
REMOTE_DISABLED_ENV='CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED'
SOURCE_ROOT='https://raw.githubusercontent.com/openai/codex/'+SOURCE_VERSION+'/codex-rs/'
# Each reference identifies the version-specific consumer, rather than treating
# all settings as harmless simply because the diagnostic never starts a turn.
SEMANTIC_COVERAGE={
    'model':('override','core/src/config/mod.rs','model.or(cfg.model)'),
    'model_reasoning_effort':('override','core/src/config/mod.rs','model_reasoning_effort'),
    'service_tier':('no_model_request','core/src/config/mod.rs','let service_tier = match service_tier_override'),
    'approval_policy':('invariant','core/src/config/mod.rs','approval_policy'),
    'approvals_reviewer':('invariant','core/src/config/mod.rs','constrained_approvals_reviewer'),
    'sandbox_mode':('profile_override','core/src/config/mod.rs','else if profiles_are_active'),
    'desktop':('opaque_frontend','config/src/config_toml.rs','pub desktop: Option<HashMap<String, JsonValue>>'),
    'marketplaces':('plugins_off','core-plugins/src/manager.rs','if config.plugins_enabled'),
    'plugins':('plugins_off','core-plugins/src/manager.rs','if !config.plugins_enabled'),
    'mcp_servers':('six_disabled','config/src/mcp_types.rs','pub enabled: bool'),
    'features':('fixed_disabled','features/src/lib.rs','overrides.apply(&mut features)'),
    'memories':('memory_off','features/src/lib.rs','key: "memories"'),
    'projects':('bound_trust_and_layers','config/src/loader/mod.rs','fn decision_for_dir'),
    'skills':('disabled_selector_only','config/src/skills_config.rs','pub struct SkillConfig'),
    'agents':('multi_agent_off','config/src/config_toml.rs','pub struct AgentsToml'),
}
DESKTOP_TYPES={'followUpQueueMode':str,'conversationDetailMode':str,'sansFontSize':int,'codeFontSize':int,
    'ambient-suggestions-enabled':bool,'localeOverride':str,'keepRemoteControlAwakeWhilePluggedIn':bool,
    'enabled-reasoning-efforts':list,'realtimeVoiceScreenContextEnabled':bool,'preventSleepWhileRunning':bool,
    'open-link-in-target-preference':str,'appearanceTheme':str}
DESKTOP_ENUMS={'followUpQueueMode':{'steer','queue'},'conversationDetailMode':{'STEPS_COMMANDS'},
    'localeOverride':{'zh-CN'},'open-link-in-target-preference':{'external-browser'},'appearanceTheme':{'light','dark','system'}}
EFFORTS=frozenset({'low','medium','high','xhigh','ultra','max'})
FROZEN_DISABLES=('apps','plugins','remote_plugin','browser_use','browser_use_external',
    'browser_use_full_cdp_access','computer_use','in_app_browser','hooks',
    'image_generation','multi_agent','multi_agent_v2','goals','memories')


def _shape(value,expected):
    if type(value)is not dict or set(value)!=set(expected)or any(type(value[k])is not t for k,t in expected.items()):
        raise ServiceError('CSS_PROCESS_SEMANTIC_STRUCTURE_REJECTED')


def _absolute_selector(value):
    if type(value)is not str or not value.startswith('/')or '..'in Path(value).parts or '\x00'in value or len(value)>4096:
        raise ServiceError('CSS_PROCESS_SEMANTIC_SELECTOR_REJECTED')


def active_trust(projects,closure):
    """Linux lookup precedence: canonical/original cwd, project root, repo root."""
    keys=[]
    for field in ('cwd_keys','project_root_keys','repo_root_keys'):
        for key in closure[field]:
            if key not in keys:keys.append(key)
    matches=[{'selector':key,'trust_level':projects[key]['trust_level']}for key in keys if key in projects]
    selected=matches[0]if matches else {'selector':None,'trust_level':None}
    return {'lookup_keys':keys,'matching_rows':matches,'selected':selected}


def trust_snapshot(projects):
    """Narrow actual layout: no valid ancestor repository, no local content.

    Unsupported repositories/layers fail closed rather than emulate linked
    worktrees, parse unknown config, or infer their trust from an exact key.
    """
    cwd=c.TRIAL
    if cwd.resolve(strict=True)!=cwd or any(x.is_symlink()for x in [cwd,*cwd.parents]):
        raise ServiceError('CSS_PROCESS_TRUST_CWD_ALIAS')
    git_candidates=[]
    for parent in [cwd,*cwd.parents]:
        marker=parent/'.git'
        try:info=marker.lstat()
        except FileNotFoundError:
            git_candidates.append({'parent':str(parent),'present':False});continue
        if not stat.S_ISDIR(info.st_mode)or marker.is_symlink()or (marker/'HEAD').exists()or (marker/'HEAD').is_symlink():
            raise ServiceError('CSS_PROCESS_TRUST_REPOSITORY_LAYOUT_UNSUPPORTED')
        git_candidates.append({'parent':str(parent),'present':True,'directory':True,'head_present':False,
            'identity':[info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode)]})
    closure={'cwd_original':str(cwd),'cwd_canonical':str(cwd),'cwd_keys':[str(cwd)],
        'project_root':str(cwd),'project_root_keys':[str(cwd)],'marker_found':False,
        'checkout_root':None,'repo_root':None,'repo_root_keys':[],'git_candidates':git_candidates}
    decision=active_trust(projects,closure)
    local=cwd/'.codex';layers=[]
    try:info=local.lstat()
    except FileNotFoundError:info=None
    if info is not None:
        if not stat.S_ISDIR(info.st_mode)or local.is_symlink()or list(local.iterdir()):
            raise ServiceError('CSS_PROCESS_TRUST_LOCAL_LAYER_UNVERIFIED')
        if decision['selected']['trust_level']=='trusted':
            raise ServiceError('CSS_PROCESS_TRUST_ENABLED_LOCAL_LAYER_UNVERIFIED')
        layers.append({'folder':str(local),'enabled':False,
            'disabled_reason':'explicit_untrusted'if decision['selected']['trust_level']=='untrusted'else'missing_trust',
            'config_present':False,'hooks_folder':str(local),'hooks_config_present':False,'exec_policy_present':False,
            'root_checkout_hooks_override':None,'empty_directory_verified':True,
            'identity':[info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode)]})
    from .codex_adapter import child_environment
    provider=Path(child_environment()['HOME'])/'.codex'
    auxiliary=[provider/'managed_config.toml',Path('/etc/codex/config.toml'),Path('/etc/codex/requirements.toml')]
    if any(x.exists()or x.is_symlink()for x in auxiliary):raise ServiceError('CSS_PROCESS_TRUST_AUXILIARY_LAYER_UNVERIFIED')
    return {'closure':closure,'trust':decision,'active_project':{'cwd':str(cwd),'project_root':str(cwd),
        'repo_root':None,'trust_level':decision['selected']['trust_level']},'project_layers':layers,
        'layer_candidates':[str(local)],'enabled_project_layer_count':0,
        'auxiliary_layers':[{'path':str(x),'present':False}for x in auxiliary],
        'user_layer':'native HOME/config.toml separately observed','cli_layer':'frozen process arguments',
        'native_layer_check_required':True}


def semantic_projection(value):
    """Exhaustive current-shape proof; raw selectors/transports stay in memory.

    This is a new projection, not a relabeling of the historical protected pin.
    It authorizes no process. A reviewed authority and native effective checks
    must still establish the prospective launch/resume facts.
    """
    from .codex_adapter import DISABLED_FEATURES,REGISTERED_SERVERS,VERSION,EVIDENCE_PERMISSION_PROFILE,profile_arguments
    legacy=protected_projection(value)
    if set(SEMANTIC_COVERAGE)!=set(c.CONFIG_TOP_TYPES):raise ServiceError('CSS_PROCESS_SEMANTIC_COVERAGE_MISSING')
    if VERSION!='codex-cli 0.159.3'or DISABLED_FEATURES!=FROZEN_DISABLES or \
        set(REGISTERED_SERVERS)!=set(value['mcp_servers']):raise ServiceError('CSS_PROCESS_SEMANTIC_RUNTIME_REJECTED')
    for row in value['mcp_servers'].values():
        if 'args'in row and any(type(v)is not str for v in row['args'])or \
            'env'in row and any(type(k)is not str or type(v)is not str for k,v in row['env'].items())or \
            'startup_timeout_sec'in row and not 1<=row['startup_timeout_sec']<=120:
            raise ServiceError('CSS_PROCESS_SEMANTIC_MCP_REJECTED')
    if (value['service_tier'],value['approval_policy'],value['approvals_reviewer'],value['sandbox_mode'])!= \
        ('priority','never','user','danger-full-access'):
        raise ServiceError('CSS_PROCESS_SEMANTIC_POLICY_REJECTED')
    desktop=value['desktop'];_shape(desktop,DESKTOP_TYPES)
    if any(desktop[k]not in allowed for k,allowed in DESKTOP_ENUMS.items())or \
        any(not 6<=desktop[k]<=48 for k in ('sansFontSize','codeFontSize'))or \
        any(type(v)is not str or v not in EFFORTS for v in desktop['enabled-reasoning-efforts']):
        raise ServiceError('CSS_PROCESS_SEMANTIC_DESKTOP_REJECTED')
    _shape(value['features'],{'memories':bool})
    _shape(value['memories'],{'generate_memories':bool,'use_memories':bool})
    _shape(value['agents'],{'default_subagent_reasoning_effort':str,'interrupt_message':bool,'max_concurrent_threads_per_session':int})
    if value['agents']['default_subagent_reasoning_effort']not in EFFORTS or not 1<=value['agents']['max_concurrent_threads_per_session']<=6:
        raise ServiceError('CSS_PROCESS_SEMANTIC_AGENTS_REJECTED')
    for row in value['plugins'].values():_shape(row,{'enabled':bool})
    for row in value['marketplaces'].values():
        _shape(row,{'source_type':str,'source':str})
        if row['source_type']!='local':raise ServiceError('CSS_PROCESS_SEMANTIC_MARKETPLACE_REJECTED')
        _absolute_selector(row['source'])
    for path,row in value['projects'].items():
        _absolute_selector(path);_shape(row,{'trust_level':str})
        if row['trust_level']not in ('trusted','untrusted'):raise ServiceError('CSS_PROCESS_SEMANTIC_TRUST_REJECTED')
    project_context=trust_snapshot(value['projects'])
    _shape(value['skills'],{'config':list})
    for row in value['skills']['config']:
        _shape(row,{'path':str,'enabled':bool});_absolute_selector(row['path'])
        if row['enabled']is not False:raise ServiceError('CSS_PROCESS_SEMANTIC_SKILL_REJECTED')
    # Transport values and unrelated selectors stay private. The exact active
    # project lookup and filesystem layers are bound to the task authority.
    mcp_structure=structure(value['mcp_servers'])
    # Environment variable names are dynamic input too. Disabled transports
    # need only their typed shape; no names or values enter the new fingerprint.
    for name,row in value['mcp_servers'].items():
        if 'env'in row:mcp_structure[name]['env']={'type':'dict','key_type':'str','value_type':'str'}
    sensitive={'mcp_servers':mcp_structure,
        'marketplaces':{'type':'dict','row_schema':{'source_type':{'type':'str'},'source':{'type':'str'}},'all_sources_local_absolute':True},
        'skills':{'type':'list','row_schema':{'path':{'type':'str'},'enabled':{'type':'bool'}},'all_selectors_disabled_absolute':True},
        'projects':{'type':'dict','row_schema':{'trust_level':{'type':'str'}},'unrelated_selectors_absolute_trust_only':True,
            'bound_active_context':project_context}}
    # Proved-inert preference values and selector counts are not process
    # authority. Normalize their checked schema/invariants, never a raw file.
    ordinary={k:value[k]for k in ('service_tier','approval_policy','approvals_reviewer','sandbox_mode')}
    ordinary.update(desktop={'field_types':{k:t.__name__ for k,t in DESKTOP_TYPES.items()},'known_values_only':True},
        features={'memories':{'type':'bool'}},memories={'generate_memories':{'type':'bool'},'use_memories':{'type':'bool'}},
        agents={'default_subagent_reasoning_effort':{'type':'str'},'interrupt_message':{'type':'bool'},
            'max_concurrent_threads_per_session':{'type':'int'},'known_values_only':True},
        plugins={'type':'dict','row_schema':{'enabled':{'type':'bool'}}})
    launch={'model':MODEL,'effort':EFFORT,'disabled_features':list(DISABLED_FEATURES),
        'disabled_mcp':list(REGISTERED_SERVERS),'web_search':'disabled','remote_start':'DisabledEphemeral',
        'remote_env_key':REMOTE_DISABLED_ENV,'profile':EVIDENCE_PERMISSION_PROFILE,'profile_arguments':list(profile_arguments(c.TRIAL,read_only=True)),
        'can_write_cwd':False,'cold_resume_only':True,
        'new_model_turns':0,'provider_requests':'deny','source_version':SOURCE_VERSION}
    coverage={k:{'classification':v[0],'source':SOURCE_ROOT+v[1],'anchor':v[2]}for k,v in SEMANTIC_COVERAGE.items()}
    payload={'ordinary':ordinary,'sensitive_structure':sensitive,'launch':launch,'coverage':coverage}
    return {'semantic_version':11,'contract':SEMANTIC_CONTRACT,'protected_sha256':c.digest(payload),
        'top_key_types':legacy['top_key_types'],'mcp_names':legacy['mcp_names'],'sensitive_structure':sensitive,
        'launch':launch,'coverage':coverage,'excluded_default_fields':['model','model_reasoning_effort'],
        'secret_values_or_hashes_retained':False,'unrelated_selector_values_or_hashes_retained':False,
        'active_project_selectors_bound':True}


def process_environment():
    from .codex_adapter import child_environment
    # Native 0.159.3 marker selects DisabledEphemeral, leaving persistence intact.
    return {**child_environment(),REMOTE_DISABLED_ENV:'1'}


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


def observe(*,semantic=False):
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
    value=tomllib.loads(raw.decode());projection=semantic_projection(value)if semantic else protected_projection(value)
    return {'projection':projection,'identity':[getattr(after,k)for k in fields[:5]],
        'default_model_is_frozen':value['model']==MODEL,'default_effort_is_frozen':value['model_reasoning_effort']==EFFORT,
        '_private_content_hash':hashlib.sha256(raw).hexdigest(),
        '_private_projects_hash':c.digest(value['projects'])}


def preservation_fact(before,after):
    # Private equality witnesses are never copied into runtime evidence.
    required={'identity','_private_content_hash','_private_projects_hash'}
    if any(not required<=set(x)for x in (before,after)):
        raise ServiceError('CSS_PROCESS_PRESERVATION_UNVERIFIED')
    return {'global_config_identity_unchanged':before['identity']==after['identity'],
        'global_config_contents_unchanged':before['_private_content_hash']==after['_private_content_hash'],
        'projects_exact_unchanged':before['_private_projects_hash']==after['_private_projects_hash']}


def public_observation(observation):
    return {k:v for k,v in observation.items()if not k.startswith('_private')}


def verify(pin):
    semantic=pin.get('semantic_version')==11
    first=observe(semantic=True)if semantic else observe();c.auth_check()
    second=observe(semantic=True)if semantic else observe()
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
        def _child_environment(self):return process_environment()
        def open(self):
            if pin.get('semantic_version')==11:
                raise ServiceError('CSS_PROCESS_SEMANTIC_PRECHECK_NOT_AUTHORIZED')
            return super().open()
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
    return ProcessDiagnostic(c.TRIAL,command=('codex','app-server','--stdio',*OVERRIDES),read_only=True)
