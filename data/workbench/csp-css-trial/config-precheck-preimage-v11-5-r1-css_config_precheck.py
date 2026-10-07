"""Bounded native configuration metadata check; never loads a thread."""
import copy
import json
import math
import re
import threading
import time
import tomllib
from pathlib import Path

import yaml

from . import css_trial as c, css_process_config as p, css_capability_recovery as r
from .codex_adapter import AppServerAdapter, EVIDENCE_PERMISSION_PROFILE, REGISTERED_SERVERS, profile_arguments
from .css_read_diagnostic import atomic_record
from .service_contract import ServiceError

CONTRACT='HUB-LWB-2.3-CSP-CONFIG-DIAGNOSTIC-GOV-v11.4'
METHODS=('initialize','config/read')
NOTICES=frozenset({'remoteControl/status/changed','account/updated','account/rateLimits/updated','configWarning'})
REGISTRATION='config-precheck-registration-v11-4-r3.json'
AUTHORITY='config-precheck-authority-v11-4.json'
INTENT='config-precheck-intent-v11-4.json'
RESULT='config-precheck-result-v11-4.json'
SERIALIZATION=json.loads((c.HUB_ROOT/c.PRIVATE/'config-precheck-sources-v11-2.json').read_text())['spec']
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


def typed_config(raw):
    """Private, narrow ConfigToml serialization for this registered input shape.

    Every absent field/default is fixed by the pinned public serializers. This
    is not a recursive ignore-null rule; unknown fields remain a mismatch.
    """
    config={k:None for k in SERIALIZATION['top_null_fields']}
    config.update({k:{} for k in SERIALIZATION['top_empty_maps']})
    config['shell_environment_policy']={k:None for k in SERIALIZATION['nested_null_fields']['shell_environment']}
    config.update(copy.deepcopy(raw));config['allow_login_shell']=True
    for field in ('agents','memories'):
        config[field]={k:None for k in SERIALIZATION['nested_null_fields'][field]}|config[field]
    config['marketplaces']={key:{k:None for k in SERIALIZATION['nested_null_fields']['marketplace']}|row
        for key,row in config['marketplaces'].items()}
    config['history']={'max_bytes':None}|config['history']
    if not config['skills']['config']:config['skills'].pop('config')
    for row in config['mcp_servers'].values():
        row.update(environment_id='local',tool_timeout_sec=None)
        if 'command' in row:row.setdefault('args',[])
        if 'startup_timeout_sec' in row:row['startup_timeout_sec']=float(row['startup_timeout_sec'])
    for row in config['permissions'].values():
        defaults={k:None for k in SERIALIZATION['nested_null_fields']['permission_profile']}
        defaults.update(row);row.clear();row.update(defaults)
        row['filesystem']={'glob_scan_max_depth':None}|row['filesystem']
        row['network']={k:None for k in SERIALIZATION['nested_null_fields']['network']}|row['network']
    config['features']['network_proxy']=None
    return config


def effective_config(raw):
    config=typed_config(raw)
    config['features'].update(SERIALIZATION['resolved_experimental_features'])
    return config


def same_json(actual,expected):
    # Compare privately without hashing or retaining user values; unlike Python
    # equality, JSON distinguishes booleans from integers and float coercions.
    return json.dumps(actual,sort_keys=True,separators=(',',':'))==json.dumps(expected,sort_keys=True,separators=(',',':'))


def layer_shape(result):
    layers=result.get('layers')
    if type(layers)is not list:return {'array_present':False,'count':None,'names':[]}
    known=set(SERIALIZATION['wire_layers'])
    return {'array_present':True,'count':len(layers),'names':[
        row.get('name',{}).get('type')if type(row)is dict and type(row.get('name'))is dict and
        row['name'].get('type')in known else 'unknown'for row in layers[:16]]}


def original_config(pin):
    """Privately read the pinned original; return no persisted diagnostic data."""
    before=p.observe(semantic=True)
    if before['projection']!=pin:raise ServiceError('CSS_PRECHECK_ORIGINAL_CONFIG_CHANGED')
    raw,identity=c.read_file(Path(p.process_environment()['HOME'])/'.codex/config.toml')
    value=tomllib.loads(raw.decode())
    after=p.observe(semantic=True)
    if identity!=before['identity']or after['projection']!=pin or not all(p.preservation_fact(before,after).values()):
        raise ServiceError('CSS_PRECHECK_ORIGINAL_CONFIG_CHANGED')
    return value


def original_timeout(pin):
    value=original_config(pin)['mcp_servers']['node_repl']['startup_timeout_sec']
    if type(value)is not int or not 1<=value<=120:raise ServiceError('CSS_PRECHECK_ORIGINAL_TIMEOUT_REJECTED')
    return value


def user_projection(body,pin):
    # The loader preserves original keys, but a Duration roundtrip changes this
    # one registered integer leaf to f64. No other numeric coercion is allowed.
    try:value=body['mcp_servers']['node_repl']['startup_timeout_sec']
    except (KeyError,TypeError):raise ServiceError('CSS_PRECHECK_TIMEOUT_REPRESENTATION_REJECTED')from None
    if type(value)is not float or not math.isfinite(value)or not value.is_integer()or not 1<=value<=120 or \
        value!=original_timeout(pin):raise ServiceError('CSS_PRECHECK_TIMEOUT_REPRESENTATION_REJECTED')
    normalized=copy.deepcopy(body);normalized['mcp_servers']['node_repl']['startup_timeout_sec']=int(value)
    return p.semantic_projection(normalized)


def config_fact(result,pin):
    """Validate bodies privately; retain no native layer version/content hash."""
    context=pin['sensitive_structure']['projects']['bound_active_context']
    if context['trust']['selected']!={'selector':None,'trust_level':None}or context['closure']['cwd_canonical']!=str(c.TRIAL):
        raise ServiceError('CSS_PRECHECK_TRUST_REJECTED')
    layers=result.get('layers');expected=SERIALIZATION['wire_layers']
    if type(layers)is not list or len(layers)!=4:raise ServiceError('CSS_PRECHECK_LAYERS_REJECTED')
    home=Path(p.process_environment()['HOME']);user=home/'.codex/config.toml'
    disabled=f'To load project-local config, hooks, and exec policies, add {c.TRIAL} as a trusted project in {user}.'
    bodies={};facts=[]
    for index,(layer,kind)in enumerate(zip(layers,expected)):
        if type(layer)is not dict or set(layer)-{'config','name','version','disabledReason'}or \
            type(layer.get('version'))is not str or not layer['version']or type(layer.get('config'))is not dict:
            raise ServiceError('CSS_PRECHECK_LAYER_STRUCTURE_REJECTED')
        name=layer.get('name');body=layer['config'];reason=layer.get('disabledReason')
        if type(name)is not dict or name.get('type')!=kind:raise ServiceError('CSS_PRECHECK_LAYER_ORDER_REJECTED')
        if kind=='system':
            accepted=name=={'type':kind,'file':'/etc/codex/config.toml'}and body=={}and reason is None
        elif kind=='user':
            accepted=name=={'type':kind,'file':str(user),'profile':None}and \
                user_projection(body,pin)==pin and reason is None
        elif kind=='project':
            accepted=name=={'type':kind,'dotCodexFolder':str(c.TRIAL/'.codex')}and body=={}and reason==disabled
        else:accepted=name=={'type':kind}and body==session_config()and reason is None
        if not accepted:raise ServiceError('CSS_PRECHECK_LAYER_CONTENT_REJECTED')
        if reason is None:bodies[kind]=body
        facts.append({'index':index,'name':kind,'source_category':kind,'project_present':kind=='project',
            'disabled':reason is not None,'disabled_reason':'missing_trust'if kind=='project'else None})
    # The wire hides packaged defaults and reverses precedence. Rebuild the
    # raw enabled merge first, then serialize, then resolve the API's 11 flags.
    combined=copy.deepcopy(PACKAGED_DEFAULTS)
    for kind in reversed(expected):
        if kind in bodies:combined=merged(combined,bodies[kind])
    effective=result.get('config')
    if type(effective)is not dict or not same_json(effective,effective_config(combined)):
        raise ServiceError('CSS_PRECHECK_EFFECTIVE_CONFIG_REJECTED')
    if effective['model']!=p.MODEL or effective['model_reasoning_effort']!=p.EFFORT or \
        effective['default_permissions']!=EVIDENCE_PERMISSION_PROFILE:
        raise ServiceError('CSS_PRECHECK_EFFECTIVE_PROFILE_REJECTED')
    return {'exact_cwd':str(c.TRIAL),'model':p.MODEL,'effort':p.EFFORT,'configured_profile':EVIDENCE_PERMISSION_PROFILE,
        'active_project_trust':None,'layer_count':len(facts),'layers':facts,'wire_precedence':'high_to_low',
        'packaged_defaults_filtered':True,'typed_serialization_verified':True,'resolved_feature_overlay_verified':True,
        'native_profile_echo':'NOT_RUN requires later cold resume',
        'raw_config_or_layer_body_retained':False,'private_layer_version_or_hash_retained':False}


DIAGNOSTIC_TYPES=('missing','null','bool','int','float','str','list','dict')
DIAGNOSTIC_REASONS=('PRESENCE_DIFF','TYPE_DIFF','KEYS_DIFF','COUNT_DIFF','VALUE_DIFF','UNKNOWN_PRIVATE_STRUCTURE')
MISSING=object()
NODE_LIMIT=4096
COUNT_LIMIT=255
DEPTH_LIMIT=12
CONTAINER_LIMIT=512
OUTPUT_LIMIT=32768


def diagnostic_type(value):
    if value is MISSING:return 'missing'
    types={type(None):'null',bool:'bool',int:'int',float:'float',str:'str',list:'list',dict:'dict'}
    if type(value)not in types:raise ServiceError('CSS_DIAGNOSTIC_TYPE_REJECTED')
    return types[type(value)]


def bounded_tree(value):
    """Check private JSON in memory. No input-dependent text escapes."""
    nodes=0
    def visit(node,depth):
        nonlocal nodes
        nodes+=1;kind=diagnostic_type(node)
        if depth>DEPTH_LIMIT or nodes>NODE_LIMIT:raise ServiceError('CSS_DIAGNOSTIC_CAPACITY')
        if kind=='float'and not math.isfinite(node):raise ServiceError('CSS_DIAGNOSTIC_TYPE_REJECTED')
        if kind=='int'and not -(2**63)<=node<2**63:raise ServiceError('CSS_DIAGNOSTIC_TYPE_REJECTED')
        if kind=='str'and len(node)>65536:raise ServiceError('CSS_DIAGNOSTIC_CAPACITY')
        if kind in ('dict','list'):
            if len(node)>CONTAINER_LIMIT:raise ServiceError('CSS_DIAGNOSTIC_CAPACITY')
            if kind=='dict':
                if any(type(k)is not str or len(k)>4096 for k in node):raise ServiceError('CSS_DIAGNOSTIC_TYPE_REJECTED')
                children=node.values()
            else:children=node
            for child in children:visit(child,depth+1)
    visit(value,0)


LEAF=object()
ROWS=object()
ITEMS=object()


def group_mask(group,effective=False):
    """Internal fixed allowlists; none are derived from private input."""
    leaf_fields=lambda keys:{key:LEAF for key in keys}
    if group=='desktop':return {k:(ITEMS,LEAF)if t is list else LEAF for k,t in p.DESKTOP_TYPES.items()}
    if group=='agents':
        keys=('default_subagent_reasoning_effort','interrupt_message','max_concurrent_threads_per_session')
        return leaf_fields((*keys,*SERIALIZATION['nested_null_fields']['agents'])if effective else keys)
    if group=='features':return leaf_fields(('memories',*p.FROZEN_DISABLES,*SERIALIZATION['resolved_experimental_features'],'network_proxy')if effective else ('memories',))
    if group=='memories':return leaf_fields(('generate_memories','use_memories',*SERIALIZATION['nested_null_fields']['memories'])if effective else ('generate_memories','use_memories'))
    if group=='plugins':return (ROWS,{'enabled':LEAF})
    if group=='marketplaces':
        keys=('source_type','source',*SERIALIZATION['nested_null_fields']['marketplace'])if effective else ('source_type','source')
        return (ROWS,leaf_fields(keys))
    if group=='projects':return (ROWS,{'trust_level':LEAF})
    if group=='skills':return {'config':(ITEMS,{'path':LEAF,'enabled':LEAF})}
    if group=='mcp_servers':
        result={}
        for server,fields in c.CONFIG_MCP_TYPES.items():
            row={key:(ITEMS,LEAF)if kind=='list'else (ROWS,LEAF)if kind=='dict'else LEAF for key,kind in fields.items()}
            if effective:row.update(enabled=LEAF,environment_id=LEAF,tool_timeout_sec=LEAF)
            result[server]=row
        return result
    return LEAF


def effective_mask():
    mask={k:LEAF for k in SERIALIZATION['top_null_fields']}
    mask.update({k:(ROWS,LEAF)for k in SERIALIZATION['top_empty_maps']})
    mask.update({k:group_mask(k,True)for k in c.CONFIG_TOP_TYPES})
    mask['shell_environment_policy']={k:LEAF for k in SERIALIZATION['nested_null_fields']['shell_environment']}
    mask['history']={'persistence':LEAF,'max_bytes':LEAF}
    mask['project_doc_fallback_filenames']=(ITEMS,LEAF);mask['project_root_markers']=(ITEMS,LEAF)
    profile={k:LEAF for k in SERIALIZATION['nested_null_fields']['permission_profile']}
    profile['filesystem']=(ROWS,LEAF)
    profile['network']={k:LEAF for k in (*SERIALIZATION['nested_null_fields']['network'],'enabled')}
    mask['permissions']={EVIDENCE_PERMISSION_PROFILE:profile}
    return mask


def safe_shape(value,mask):
    """Unknown subtrees get one sentinel, never a recursive fingerprint."""
    counts={k:0 for k in DIAGNOSTIC_TYPES}
    unknown=0
    def visit(node,allowed):
        nonlocal unknown
        kind=diagnostic_type(node)
        approved_container=type(allowed)is dict and kind=='dict'or type(allowed)is tuple and \
            (allowed[0]is ROWS and kind=='dict'or allowed[0]is ITEMS and kind=='list')
        if kind in ('dict','list')and not approved_container:
            unknown=min(COUNT_LIMIT,unknown+1);return
        kind=diagnostic_type(node);counts[kind]=min(COUNT_LIMIT,counts[kind]+1)
        if type(allowed)is dict and kind=='dict':
            unknown=min(COUNT_LIMIT,unknown+len(node.keys()-allowed.keys()))
            for key in node.keys()&allowed.keys():visit(node[key],allowed[key])
        elif type(allowed)is tuple and allowed[0]is ROWS and kind=='dict':
            for child in node.values():visit(child,allowed[1])
        elif type(allowed)is tuple and allowed[0]is ITEMS and kind=='list':
            for child in node:visit(child,allowed[1])
    visit(value,mask)
    return counts,unknown


def shape_equality(original,wire):
    if diagnostic_type(original)!=diagnostic_type(wire):
        return type(original)not in (dict,list)and type(wire)not in (dict,list),False,0
    if type(original)is dict:
        keys=original.keys()==wire.keys();types=True;unknown=len(wire.keys()-original.keys())
        for key in original.keys()&wire.keys():
            k,t,n=shape_equality(original[key],wire[key]);keys&=k;types&=t;unknown+=n
        return keys,types,min(COUNT_LIMIT,unknown)
    if type(original)is list:
        keys=True;types=len(original)==len(wire);unknown=0
        for a,b in zip(original,wire):
            k,t,n=shape_equality(a,b);keys&=k;types&=t;unknown+=n
        return keys,types,min(COUNT_LIMIT,unknown)
    return True,True,0


def diagnostic_group(group,original,wire):
    if group not in c.CONFIG_TOP_TYPES and group!='effective_config':raise ServiceError('CSS_DIAGNOSTIC_ENUM_REJECTED')
    mask=effective_mask()if group=='effective_config'else group_mask(group)
    original_types,a=safe_shape(original,mask);wire_types,b=safe_shape(wire,mask);unknown=min(COUNT_LIMIT,a+b)
    keys,types,_=shape_equality(original,wire)if not unknown else (False,False,unknown)
    count=lambda v:min(COUNT_LIMIT,len(v))if type(v)in (dict,list)else None
    equal=False if unknown else (original is wire)if original is MISSING or wire is MISSING else same_json(original,wire)
    facts={'group':group,'original_present':original is not MISSING,'wire_present':wire is not MISSING,
        'original_type':diagnostic_type(original),'wire_type':diagnostic_type(wire),
        'original_count':count(original),'wire_count':count(wire),
        'original_types':original_types,'wire_types':wire_types,
        'keys_equal':keys,'types_equal':types,'values_equal':equal,'unknown_private_count':unknown}
    reasons=[]
    if facts['original_present']!=facts['wire_present']:reasons.append('PRESENCE_DIFF')
    if not types:reasons.append('TYPE_DIFF')
    if not keys:reasons.append('KEYS_DIFF')
    if facts['original_count']!=facts['wire_count']:reasons.append('COUNT_DIFF')
    if not equal:reasons.append('VALUE_DIFF')
    if unknown:reasons.append('UNKNOWN_PRIVATE_STRUCTURE')
    facts['reasons']=reasons
    return facts


def origin_rows(origins,expected_user=None):
    """Strict public ConfigLayerMetadata DTO; all origin data stays private."""
    if type(origins)is not dict:raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
    bounded_tree(origins)
    fields={'packagedDefaults':('file',),'mdm':('domain','key'),'system':('file',),
        'enterpriseManaged':('id','name'),'user':('file',),'project':('dotCodexFolder',),
        'sessionFlags':(),'legacyManagedConfigTomlFromFile':('file',),'legacyManagedConfigTomlFromMdm':()}
    user_seen=False
    for row in origins.values():
        if type(row)is not dict or set(row)!={'name','version'}or type(row['version'])is not str or not row['version']:
            raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
        name=row['name']
        if type(name)is not dict or type(name.get('type'))is not str or name['type']not in fields:
            raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
        kind=name['type'];required={'type',*fields[kind]}|({'profile'}if kind=='user'else set())
        if set(name)!=required or any(type(name[k])is not str for k in fields[kind])or \
            'profile'in name and name['profile']is not None and type(name['profile'])is not str:
            raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
        if kind=='user':
            user_seen=True
            if expected_user is not None and name!=expected_user:raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
        if any(not name[k].startswith('/')or '\x00'in name[k]or '..'in Path(name[k]).parts for k in ('file','dotCodexFolder')if k in name):
            raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
    if expected_user is not None and not user_seen:raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')


def diagnostic_matrix(result,original):
    """One aggregated sample, independent of every acceptance predicate."""
    if type(result)is not dict or set(result)!={'config','layers','origins'}or type(original)is not dict or \
        type(result.get('layers'))is not list or len(result['layers'])!=4 or type(result.get('config'))is not dict:
        raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
    bounded_tree(original);bounded_tree(result['config'])
    if set(original)!=set(c.CONFIG_TOP_TYPES):raise ServiceError('CSS_DIAGNOSTIC_ORIGINAL_REJECTED')
    home=Path(p.process_environment()['HOME']);user=home/'.codex/config.toml'
    disabled=f'To load project-local config, hooks, and exec policies, add {c.TRIAL} as a trusted project in {user}.'
    bodies={}
    for layer,kind in zip(result['layers'],SERIALIZATION['wire_layers']):
        if type(layer)is not dict or set(layer)-{'config','name','version','disabledReason'}or \
            type(layer.get('version'))is not str or not layer['version']or type(layer.get('config'))is not dict:
            raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
        name=layer.get('name');body=layer['config'];reason=layer.get('disabledReason')
        if kind=='system':accepted=name=={'type':kind,'file':'/etc/codex/config.toml'}and body=={}and reason is None
        elif kind=='user':accepted=name=={'type':kind,'file':str(user),'profile':None}and reason is None
        elif kind=='project':accepted=name=={'type':kind,'dotCodexFolder':str(c.TRIAL/'.codex')}and body=={}and reason==disabled
        else:accepted=name=={'type':kind}and body==session_config()and reason is None
        if not accepted:raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
        bounded_tree(body);bodies[kind]=body
    origin_rows(result['origins'],{'type':'user','file':str(user),'profile':None})
    wire=bodies['user'];raw=merged(PACKAGED_DEFAULTS,original);raw=merged(raw,session_config());expected=effective_config(raw)
    effective=result['config']
    # Public schema changes fail closed; private differences get a safe matrix.
    if set(effective)!=set(expected)or any(type(effective[k])is not type(v)for k,v in expected.items()):
        raise ServiceError('CSS_DIAGNOSTIC_PUBLIC_STRUCTURE_REJECTED')
    rows=[diagnostic_group(group,original[group],wire.get(group,MISSING))for group in sorted(c.CONFIG_TOP_TYPES)]
    rows.append(diagnostic_group('effective_config',expected,effective))
    unknown=min(COUNT_LIMIT,len(wire.keys()-c.CONFIG_TOP_TYPES.keys()))
    matrix={'schema':'fixed_groups_v11_4','groups':rows,'unknown_private_count':unknown,
        'unknown_private_reason':'UNKNOWN_PRIVATE_STRUCTURE'if unknown else None,
        'diagnostic_only':True,'semantic_typed_effective_capabilities_acceptance':'NOT_RUN'}
    if len(json.dumps(matrix,separators=(',',':')).encode())>OUTPUT_LIMIT:raise ServiceError('CSS_DIAGNOSTIC_OUTPUT_CAPACITY')
    return matrix


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
    a.notification_recorder=lambda n,f:atomic_record(folder/f'config-precheck-notification-v11-4-{n}.json',f)
    try:
        a.open();record['pid']=a.proc.pid
        response=a.call('config/read',config_params());record['layer_shape']=layer_shape(response)
        record['diagnostic']=diagnostic_matrix(response,original_config(authority['semantic_pin']))
        drain(a);record['status']='PASS_CONFIG_STRUCTURE_DIAGNOSTIC_ONLY'
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
