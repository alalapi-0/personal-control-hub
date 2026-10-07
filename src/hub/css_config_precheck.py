"""Bounded native configuration metadata check; never loads a thread."""
import copy
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import css_trial as c, css_process_config as p, css_capability_recovery as r
from .codex_adapter import AppServerAdapter, EVIDENCE_PERMISSION_PROFILE, REGISTERED_SERVERS, profile_arguments
from .css_read_diagnostic import atomic_record
from .service_contract import ServiceError
from .css_metadata_effects import MetadataEffects

CONTRACT='HUB-LWB-2.3-CSP-FULL-METADATA-AUTH-REFRESH-GOV-v11.7'
METHODS=('initialize','config/read','mcpServerStatus/list','experimentalFeature/list')
REFRESH_GRANT='csp-metadata-sdk-refresh-v11-7'
NOTICES=frozenset({'remoteControl/status/changed','account/updated','account/rateLimits/updated','configWarning'})
REGISTRATION='config-precheck-registration-v11-7-r1.json'
AUTHORITY='config-precheck-authority-v11-7.json'
INTENT='config-precheck-intent-v11-7.json'
RESULT='config-precheck-result-v11-7.json'
PROTOCOL_SOURCE='feature-catalog-source-closure-v11-7.json'
PROTOCOL_SOURCE_SHA='95872e17955ce713e29eb0b610048f96b2921f79a5dfc7f12b272ae8670ec38f'
DESKTOP_SOURCE='desktop-roundtrip-source-closure-v11-5-r1.json'
DESKTOP_SOURCE_SHA='d7e40c9226ccfe7049bffa388673d0223e5dbf0c5f87053b487a7bab9681fcd0'
NUMBER_TOKEN='$serde_json::private::Number'
RAW_VALUE_TOKEN='$serde_json::private::RawValue'
DESKTOP_INPUT_BYTES=262144
MODEL_CATALOG=c.HUB_ROOT/c.PRIVATE/'public-source-v11-6-mcp/bundled-models.json'
MODEL_CATALOG_SHA='fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b'
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


def mcp_params():return {'threadId':None,'limit':100,'detail':'toolsAndAuthOnly'}


def metadata_overrides():return (*p.OVERRIDES,'-c','model_catalog_json='+json.dumps(str(MODEL_CATALOG)))


def catalog_snapshot():
    if MODEL_CATALOG.is_symlink()or any(x.is_symlink()for x in MODEL_CATALOG.parents):
        raise ServiceError('CSS_PUBLIC_MODEL_CATALOG_REJECTED')
    raw,identity=c.read_file(MODEL_CATALOG,mode=0o644)
    info=MODEL_CATALOG.lstat()
    if info.st_uid!=os.getuid()or c.sha(raw)!=MODEL_CATALOG_SHA or \
        not 0<len(raw)<=1048576 or not json.loads(raw).get('models'):
        raise ServiceError('CSS_PUBLIC_MODEL_CATALOG_REJECTED')
    return tuple(identity)


def credential_snapshot():
    """Stat only; the private identity is never serialized or hashed."""
    path=c.CREDENTIAL_PATH
    if any(parent.is_symlink()for parent in path.parents):
        raise ServiceError('CSS_SDK_CREDENTIAL_STORAGE_UNSAFE')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or info.st_nlink!=1 or \
        stat.S_IMODE(info.st_mode)&0o077 or not 0<info.st_size<=1048576:
        raise ServiceError('CSS_SDK_CREDENTIAL_STORAGE_UNSAFE')
    return tuple(getattr(info,k)for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid','st_nlink'))


def credential_fact(before,after,outcome):
    def safe(identity):
        return type(identity)is tuple and len(identity)==8 and all(type(x)is int for x in identity)and \
            stat.S_ISREG(identity[5])and identity[6]==os.getuid()and identity[7]==1 and \
            not(stat.S_IMODE(identity[5])&0o077)and 0<identity[2]<=1048576
    if not safe(before)or not safe(after)or outcome not in (
        'SDK_REFRESH_FAILURE_REPORTED','NO_REFRESH_FAILURE_SIGNAL','UNKNOWN_STDERR'):
        raise ServiceError('CSS_SDK_CREDENTIAL_EFFECT_UNVERIFIED')
    return {'before_exists':True,'after_exists':True,'safe_attributes':True,
        'before_safe':dict.fromkeys(('nofollow','owner','regular','single_link','private_mode'),True),
        'after_safe':dict.fromkeys(('nofollow','owner','regular','single_link','private_mode'),True),
        'identity_changed':before!=after,'official_storage_only_source_bound':True,
        'refresh_outcome':outcome,'refresh_success':'NOT_OBSERVED',
        'child_network_visibility':'NOT_RUN','physical_request_count':'NOT_OBSERVED',
        'content_read_copied_hashed':False}


def session_config():
    args=profile_arguments(c.TRIAL,read_only=True)
    return {'model':p.MODEL,'model_reasoning_effort':p.EFFORT,'web_search':'disabled',
        'model_catalog_json':str(MODEL_CATALOG),
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
    original=original_config(pin)
    desktop=desktop_roundtrip_fact(original.get('desktop',MISSING),body.get('desktop',MISSING))
    if not desktop['roundtrip_supported']or not desktop['values_equal']:
        raise ServiceError('CSS_PRECHECK_DESKTOP_ROUNDTRIP_REJECTED')
    normalized=copy.deepcopy(body);normalized['mcp_servers']['node_repl']['startup_timeout_sec']=int(value)
    # Only after exact forward equality: use the known original semantic value.
    # Never decode a native wrapper or overwrite the actual API config.
    normalized['desktop']=copy.deepcopy(original['desktop'])
    return p.semantic_projection(normalized)


def config_fact(result,pin):
    """Validate bodies privately; retain no native layer version/content hash."""
    if type(result)is not dict or set(result)!={'config','layers','origins'}or type(result['config'])is not dict:
        raise ServiceError('CSS_PRECHECK_PUBLIC_STRUCTURE_REJECTED')
    bounded_tree(result['config'])
    context=pin['sensitive_structure']['projects']['bound_active_context']
    if context['trust']['selected']!={'selector':None,'trust_level':None}or context['closure']['cwd_canonical']!=str(c.TRIAL):
        raise ServiceError('CSS_PRECHECK_TRUST_REJECTED')
    layers=result.get('layers');expected=SERIALIZATION['wire_layers']
    if type(layers)is not list or len(layers)!=4:raise ServiceError('CSS_PRECHECK_LAYERS_REJECTED')
    home=Path(p.process_environment()['HOME']);user=home/'.codex/config.toml'
    origin_rows(result['origins'],{'type':'user','file':str(user),'profile':None})
    disabled=f'To load project-local config, hooks, and exec policies, add {c.TRIAL} as a trusted project in {user}.'
    original=original_config(pin)
    desktop=desktop_roundtrip_fact(original.get('desktop',MISSING),result.get('config',{}).get('desktop',MISSING),raw_layer=False)
    if not desktop['roundtrip_supported']or not desktop['values_equal']:
        raise ServiceError('CSS_PRECHECK_DESKTOP_ROUNDTRIP_REJECTED')
    bodies={};facts=[]
    for index,(layer,kind)in enumerate(zip(layers,expected)):
        # Native serde omits None; only the disabled project emits a reason.
        fields={'config','name','version'}|({'disabledReason'}if kind=='project'else set())
        if type(layer)is not dict or set(layer)!=fields or \
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
        if reason is None:
            bodies[kind]=copy.deepcopy(body)
            if kind=='user':bodies[kind]['desktop']=copy.deepcopy(original['desktop'])
        facts.append({'index':index,'name':kind,'source_category':kind,'project_present':kind=='project',
            'disabled':reason is not None,'disabled_reason':'missing_trust'if kind=='project'else None})
    # The wire hides packaged defaults and reverses precedence. Rebuild the
    # raw enabled merge first, then serialize, then resolve the API's 11 flags.
    combined=copy.deepcopy(PACKAGED_DEFAULTS)
    for kind in reversed(expected):
        if kind in bodies:combined=merged(combined,bodies[kind])
    effective=result.get('config')
    expected_config=effective_config(combined)
    if not same_json(typed_config(combined)['desktop'],original['desktop'])or \
        not same_json(expected_config['desktop'],original['desktop']):
        raise ServiceError('CSS_PRECHECK_DESKTOP_BUILDER_REJECTED')
    if type(effective)is not dict or not same_json(effective,expected_config):
        raise ServiceError('CSS_PRECHECK_EFFECTIVE_CONFIG_REJECTED')
    if effective.get('cli_auth_credentials_store')!='file':
        raise ServiceError('CSS_SDK_CREDENTIAL_STORAGE_MODE_REJECTED')
    if effective['model']!=p.MODEL or effective['model_reasoning_effort']!=p.EFFORT or \
        effective['default_permissions']!=EVIDENCE_PERMISSION_PROFILE:
        raise ServiceError('CSS_PRECHECK_EFFECTIVE_PROFILE_REJECTED')
    if set(effective['mcp_servers'])!=set(REGISTERED_SERVERS)or any(
        effective['mcp_servers'][name].get('enabled')is not False for name in REGISTERED_SERVERS):
        raise ServiceError('CSS_PRECHECK_MCP_CONFIGURATION_REJECTED')
    return {'exact_cwd':str(c.TRIAL),'model':p.MODEL,'effort':p.EFFORT,'configured_profile':EVIDENCE_PERMISSION_PROFILE,
        'active_project_trust':None,'layer_count':len(facts),'layers':facts,'wire_precedence':'high_to_low',
        'packaged_defaults_filtered':True,'typed_serialization_verified':True,'resolved_feature_overlay_verified':True,
        'configured_mcp_disabled':True,'configured_mcp_count':len(REGISTERED_SERVERS),
        'desktop':{'desktop_present':True,'roundtrip_supported':True,'raw_forward_equal':True,
            'final_desktop_equal':True,'expected_builder_preserves_desktop':True,'final_config_equal':True,'reasons':[]},
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


def desktop_source_snapshot():
    """Immutable public provenance from one verified read, never private data."""
    try:
        raw=(c.HUB_ROOT/c.PRIVATE/DESKTOP_SOURCE).read_bytes()
        if c.sha(raw)!=DESKTOP_SOURCE_SHA:return None
        proof=json.loads(raw)
        if proof['feature_graph_proven']is not True or proof['arbitrary_precision']is not True:return None
        for name,digest in proof['sources'].items():
            if c.sha((c.HUB_ROOT/name).read_bytes())!=digest:return None
        binary=Path(proof['binary_path']);identity=binary.lstat()
        stamp=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid)
        if not stat.S_ISREG(identity.st_mode)or c.sha(binary.read_bytes())!=proof['binary_sha256']or \
            stamp(identity)!=stamp(binary.lstat()):return None
        return (DESKTOP_SOURCE_SHA,proof['binary_sha256'],tuple(sorted(proof['sources'].items())),stamp(identity))
    except (OSError,ValueError,KeyError,TypeError):return None


def desktop_source_verified():
    return desktop_source_snapshot()is not None


def _desktop_forward(original,arbitrary_precision=True):
    """Pinned Number -> TOML table -> JSON map, never a reverse decoder.

    The inactive branch exists for public serializer regression fixtures only.
    Admission always requires the exact active feature graph above.
    """
    if type(arbitrary_precision)is not bool:raise ServiceError('ROUNDTRIP_UNSUPPORTED')
    bounded_tree(original)
    def visit(value):
        kind=type(value)
        if kind is dict:
            if NUMBER_TOKEN in value or RAW_VALUE_TOKEN in value:raise ServiceError('ROUNDTRIP_UNSUPPORTED')
            for key in value:key.encode('utf-8',errors='strict')
            return {key:visit(child)for key,child in value.items()}
        if kind is list:return [visit(child)for child in value]
        if kind is int:return {NUMBER_TOKEN:str(value)}if arbitrary_precision else value
        if kind is str:value.encode('utf-8',errors='strict');return value
        if kind is bool:return value
        # TOML cannot represent null. Floating number formatting is unproved.
        raise ServiceError('ROUNDTRIP_UNSUPPORTED')
    try:
        expected=visit(original)
        if len(json.dumps(original,ensure_ascii=False,separators=(',',':')).encode('utf-8'))>DESKTOP_INPUT_BYTES:
            raise ServiceError('ROUNDTRIP_UNSUPPORTED')
        bounded_tree(expected)
        return expected
    except UnicodeError:raise ServiceError('ROUNDTRIP_UNSUPPORTED')from None


def desktop_roundtrip_fact(original,wire,*,raw_layer=True):
    """Opaque private comparison; emit no names, values, shapes or hashes."""
    fact={'desktop_present':original is not MISSING and wire is not MISSING,
        'roundtrip_supported':False,'values_equal':False,'reasons':[]}
    if not desktop_source_verified():fact['reasons']=['FEATURE_GRAPH_UNPROVEN'];return fact
    if not fact['desktop_present']:fact['reasons']=['PRESENCE_DIFF'];return fact
    try:
        if type(original)is not dict or type(raw_layer)is not bool:raise ServiceError('ROUNDTRIP_UNSUPPORTED')
        expected=_desktop_forward(original)
        bounded_tree(wire)
        # Strict Unicode also applies to the actual response, including keys.
        encoded=json.dumps(wire,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        if len(encoded)>DESKTOP_INPUT_BYTES:raise ServiceError('ROUNDTRIP_UNSUPPORTED')
        fact['roundtrip_supported']=True
        fact['values_equal']=same_json(wire,expected if raw_layer else original)
        if not fact['values_equal']:fact['reasons']=['VALUE_DIFF']
    except (ServiceError,UnicodeError):fact['reasons']=['ROUNDTRIP_UNSUPPORTED']
    return fact


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
    if group=='desktop':return {'group':group,**desktop_roundtrip_fact(original,wire)}
    # The API desktop is compared separately and completely; its internal
    # shape must not contribute to even the aggregate effective histogram.
    if group=='effective_config':
        original={k:v for k,v in original.items()if k!='desktop'}
        wire={k:v for k,v in wire.items()if k!='desktop'}
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
    raw_desktop=next(x for x in rows if x['group']=='desktop')
    final_desktop=desktop_roundtrip_fact(original.get('desktop',MISSING),effective.get('desktop',MISSING),raw_layer=False)
    builder_equal=same_json(typed_config(raw)['desktop'],original['desktop'])and same_json(expected['desktop'],original['desktop'])
    desktop={'desktop_present':raw_desktop['desktop_present']and final_desktop['desktop_present'],
        'roundtrip_supported':raw_desktop['roundtrip_supported']and final_desktop['roundtrip_supported'],
        'raw_forward_equal':raw_desktop['values_equal'],'final_desktop_equal':final_desktop['values_equal'],
        'expected_builder_preserves_desktop':builder_equal,'final_config_equal':same_json(expected,effective),
        'reasons':list(dict.fromkeys(raw_desktop['reasons']+final_desktop['reasons']+
            ([]if builder_equal else ['BUILDER_DIFF'])+([]if same_json(expected,effective)else ['FINAL_CONFIG_DIFF'])))}
    matrix={'schema':'fixed_groups_v11_5','groups':rows,'desktop':desktop,'unknown_private_count':unknown,
        'unknown_private_reason':'UNKNOWN_PRIVATE_STRUCTURE'if unknown else None,
        'diagnostic_only':True,'semantic_typed_effective_capabilities_acceptance':'NOT_RUN'}
    if len(json.dumps(matrix,separators=(',',':')).encode())>OUTPUT_LIMIT:raise ServiceError('CSS_DIAGNOSTIC_OUTPUT_CAPACITY')
    return matrix


@dataclass(frozen=True)
class Admission:
    candidate: str
    evidence: str
    source: tuple
    binary: str
    protected_inputs: str


def protocol_source_snapshot():
    """Exact public experimental exports and DTO/source closure, before effects."""
    try:
        raw=(c.HUB_ROOT/c.PRIVATE/PROTOCOL_SOURCE).read_bytes()
        if c.sha(raw)!=PROTOCOL_SOURCE_SHA:return None
        proof=json.loads(raw)
        for name,digest in proof['sources'].items():
            if c.sha((c.HUB_ROOT/name).read_bytes())!=digest:return None
        bundle=json.loads((c.HUB_ROOT/proof['schema_bundle']['file']).read_text())['json_schema']
        if len(bundle)!=440 or {k:c.sha(v.encode())for k,v in bundle.items()}!=proof['schema_bundle']['files']:
            return None
        if len(proof['templates'])!=152 or len({t['name']for t in proof['templates']})!=152:return None
        return (PROTOCOL_SOURCE_SHA,tuple(sorted(proof['sources'].items())),catalog_snapshot())
    except (OSError,ValueError,KeyError,TypeError,ServiceError):return None


def protocol_proof():
    if protocol_source_snapshot()is None:raise ServiceError('PUBLIC_PROTOCOL_UNPROVEN')
    return json.loads((c.HUB_ROOT/c.PRIVATE/PROTOCOL_SOURCE).read_text())


@dataclass(frozen=True)
class AuthReceipt:
    mode: str
    exit: int


def authentication_receipt():
    fact=c.auth_check()  # Raw stdout/stderr remain private to the existing check.
    return AuthReceipt(fact['mode'],fact['exit'])


def observed_verified(pin,receipt):
    if type(receipt)is not AuthReceipt or type(receipt.exit)is not int or type(receipt.mode)is not str or receipt!=AuthReceipt('ChatGPT',0):
        raise ServiceError('CSS_PRECHECK_AUTH_RECEIPT_REJECTED')
    first=p.observe(semantic=True);second=p.observe(semantic=True)
    if first['projection']!=pin or second['projection']!=pin or not all(p.preservation_fact(first,second).values()):
        raise ServiceError('CSS_PROCESS_PROTECTED_CONFIG_DRIFT')
    return second


def load_native_schemas(a,effects):
    """Generate once into a new owned directory; verify every public export."""
    proof=protocol_proof();env=p.process_environment()
    run=subprocess.run(['codex','--version'],cwd=c.HUB_ROOT,env=env,capture_output=True,timeout=5)
    if run.returncode or run.stdout.strip()!=b'codex-cli 0.159.3'or run.stderr:
        raise ServiceError('CODEX_VERSION_UNSUPPORTED')
    folder=c.HUB_ROOT/c.PRIVATE
    if folder.is_symlink()or not folder.is_dir():raise ServiceError('CSS_PRECHECK_SCHEMA_DIRECTORY_REJECTED')
    directory=None;a.schema_temp_cleaned=False
    try:
        with tempfile.TemporaryDirectory(prefix='schema-native-v11-6-',dir=folder)as directory:
            target=Path(directory);info=target.lstat()
            if not stat.S_ISDIR(info.st_mode)or stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.getuid():
                raise ServiceError('CSS_PRECHECK_SCHEMA_DIRECTORY_REJECTED')
            effects.schema_directory=directory
            previous=os.umask(0o077)
            try:
                run=subprocess.run(['codex','app-server','generate-json-schema','--experimental','--out',directory],
                    cwd=c.HUB_ROOT,env=env,capture_output=True,timeout=10)
            finally:os.umask(previous)
            if run.returncode or run.stdout or run.stderr:raise ServiceError('CODEX_SCHEMA_UNAVAILABLE')
            expected=proof['schema_bundle']['files'];actual={};schemas={};total=0
            directories={str(parent)for name in expected for parent in Path(name).parents if str(parent)!='.'}
            for path in target.rglob('*'):
                info=path.lstat()
                if info.st_uid!=os.getuid()or stat.S_IMODE(info.st_mode)not in (0o600,0o700):
                    raise ServiceError('CSS_PRECHECK_SCHEMA_DIRECTORY_REJECTED')
                if stat.S_ISDIR(info.st_mode):
                    if str(path.relative_to(target))not in directories:raise ServiceError('CODEX_SCHEMA_CHANGED')
                    continue
                if not stat.S_ISREG(info.st_mode)or info.st_nlink!=1 or info.st_size>1048576:
                    raise ServiceError('CSS_PRECHECK_SCHEMA_DIRECTORY_REJECTED')
                name=str(path.relative_to(target))
                if name not in expected or len(actual)>=440:raise ServiceError('CODEX_SCHEMA_CHANGED')
                raw=path.read_bytes();total+=len(raw)
                if total>8388608 or c.sha(raw)!=expected[name]:raise ServiceError('CODEX_SCHEMA_CHANGED')
                actual[name]=c.sha(raw)
                if path.stem in schemas:raise ServiceError('CODEX_SCHEMA_CHANGED')
                schemas[path.stem]=json.loads(raw)
            if actual!=expected:raise ServiceError('CODEX_SCHEMA_CHANGED')
            a.schemas=schemas
            a.schema_hashes={Path(k).stem:v for k,v in actual.items()}
            if any(a.schema_hashes.get(k)!=v['sha256']for k,v in proof['schemas'].items()):
                raise ServiceError('CODEX_SCHEMA_CHANGED')
    finally:
        a.schema_temp_cleaned=directory is not None and not Path(directory).exists()


def fixed_inputs_current(reg):
    """Read-only checks before creating any adapter or consuming authority."""
    before=p.observe(semantic=True)
    task=r.TaskStore(c.HUB_ROOT).task(c.TASK)
    r.mapping_check(task['intent']['grant'])
    after=p.observe(semantic=True)
    credential_snapshot()
    return before['projection']==reg['semantic_pin']and after['projection']==reg['semantic_pin']and \
        all(p.preservation_fact(before,after).values())and r.source_snapshot()==reg['source']and \
        c.placeholder_snapshot()==reg['trial']and reg.get('credential_storage_safe')is True and \
        original_config(reg['semantic_pin']).get('cli_auth_credentials_store','file')=='file'and \
        c.digest(task)==reg['task_hash']


def admit(authority):
    source=desktop_source_snapshot()
    if source is None:raise ServiceError('FEATURE_GRAPH_UNPROVEN')
    protocol=protocol_source_snapshot()
    if protocol is None:raise ServiceError('PUBLIC_PROTOCOL_UNPROVEN')
    source=(source,protocol)
    r.unexpired(authority)
    expected={'contract':CONTRACT,'task_id':c.TASK,'methods':list(METHODS),'config_read_params':config_params(),
        'mcp_status_params':mcp_params(),'official_sdk_refresh_allowed':True,'effect_grant_id':REFRESH_GRANT,
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
    if any(authority.get(k)!=reg[k]for k in ('semantic_pin','source','trial','credential_storage_safe','task_hash')):
        raise ServiceError('CSS_PRECHECK_AUTHORITY_REJECTED')
    text=(c.HUB_ROOT/'STATE.yaml').read_text();match=re.search(r'^linux_visual_workbench:\n.*?(?=^[^ \n#][^\n]*:\n|\Z)',text,re.M|re.S)
    state=yaml.safe_load(match[0])['linux_visual_workbench']if match else{}
    contract=state.get('contract',{});owner=state.get('authorization',{}).get('conditional_pilot_write_grant',{})
    refresh=state.get('authorization',{}).get('sdk_metadata_refresh_grant',{})
    if contract.get('id')!=CONTRACT or contract.get('decision')!='APPROVE_NATIVE_PRECHECK'or \
        contract.get('status')!='APPROVED_SINGLE_NATIVE_PRECHECK'or \
        owner.get('state')!='active_exact_css_trial'or owner.get('task_id')!=c.TASK or \
        owner.get('project_id')!='computer-study-plan'or owner.get('grant_id')!='csp-css-trial-grant-v1'or \
        state.get('trial_registration',{}).get('resume_calls')!=4 or \
        refresh.get('state')!='explicit_one_sample'or refresh.get('grant_id')!=REFRESH_GRANT or \
        refresh.get('contract')!=CONTRACT or refresh.get('candidate_sha256')!=reg['candidate_sha256']or \
        refresh.get('evidence_sha256')!=reg['evidence_sha256']or refresh.get('official_sdk_refresh_allowed')is not True:
        raise ServiceError('CSS_PRECHECK_STATE_REJECTED')
    if (c.HUB_ROOT/c.PRIVATE/INTENT).exists():raise ServiceError('CSS_PRECHECK_ALREADY_CONSUMED')
    if not fixed_inputs_current(reg):raise ServiceError('CSS_PRECHECK_INPUT_CHANGED')
    return Admission(reg['candidate_sha256'],reg['evidence_sha256'],source,source[0][1],
        c.digest({k:reg[k]for k in ('semantic_pin','source','trial','credential_storage_safe','task_hash')}))


def adapter(pin,authority,receipt=None,effects=None):
    admission=admit(authority)
    if pin!=authority.get('semantic_pin'):raise ServiceError('CSS_PRECHECK_AUTHORITY_REJECTED')
    class ConfigOnly(AppServerAdapter):
        def _child_environment(self):return p.process_environment()
        def _configuration_hash(self):return observed_verified(pin,receipt)['_private_content_hash']
        def _read_errors(self):
            # A fixed prefix matcher carries only its index between chunks.
            # No inherited stderr hash, length, raw log or dynamic suffix.
            marker=b'Failed to refresh token: ';matched=0
            try:
                while chunk:=self.proc.stderr.read(4096):
                    if self.stderr_outcome!='SDK_REFRESH_FAILURE_REPORTED':self.stderr_outcome='UNKNOWN_STDERR'
                    for byte in chunk:
                        matched=matched+1 if byte==marker[matched]else (1 if byte==marker[0]else 0)
                        if matched==len(marker):
                            self.stderr_outcome='SDK_REFRESH_FAILURE_REPORTED';matched=0
                    self.failed='CSS_SDK_REFRESH_EFFECT_UNCERTAIN'
                    del chunk
            except (OSError,ValueError):
                if self.stderr_outcome!='SDK_REFRESH_FAILURE_REPORTED':self.stderr_outcome='UNKNOWN_STDERR'
                self.failed='CSS_SDK_REFRESH_EFFECT_UNCERTAIN'
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
            # A directly called open cannot reuse a stale validated snapshot.
            if admit(authority)!=admission:raise ServiceError('CSS_PRECHECK_ADMISSION_CHANGED')
            observed_verified(pin,receipt)
            if type(effects)is not MetadataEffects:raise ServiceError('CSS_PRECHECK_EFFECT_AUDIT_REQUIRED')
            self.deadline=time.monotonic()+100
            atomic_record(c.HUB_ROOT/c.PRIVATE/INTENT,{'contract':CONTRACT,
                'approved_candidate':authority['approved_candidate'],'approved_evidence':authority['approved_evidence'],
                'helpers_allowed':1,'threads':0,'resumes':0,'model_turns':0})
            self.watchdog=threading.Timer(120,self.deadline_close);self.watchdog.start()
            load_native_schemas(self,effects)
            effects.bind_helper(self._configuration_hash())
            return super().open()
        def deadline_close(self):
            self.wall_exceeded=True;self.close()
        def call(self,method,params,timeout=15):
            r.unexpired(authority)
            if time.monotonic()>=getattr(self,'deadline',float('inf')):raise ServiceError('CSS_PRECHECK_WALL_EXPIRED')
            if method not in METHODS:raise ServiceError('CSS_PRECHECK_METHOD_REJECTED')
            fixed={'initialize':{'clientInfo':{'name':'personal_control_hub','version':'0.1.0'},'capabilities':{'experimentalApi':True}},
                'config/read':config_params(),'mcpServerStatus/list':mcp_params()}
            if method in fixed:
                if params!=fixed[method]or method in self.consumed:raise ServiceError('CSS_PRECHECK_PARAMS_REJECTED')
                if method=='config/read'and (self.snapshots!=1 or not self.feature_complete or self.feature_active):
                    raise ServiceError('CSS_PRECHECK_PHASE_REJECTED')
                if method=='mcpServerStatus/list'and (self.snapshots!=1 or not self.feature_complete or self.feature_active or \
                    'config/read'not in self.consumed):raise ServiceError('CSS_PRECHECK_PHASE_REJECTED')
                self.consumed.add(method)
            else:
                if params!={'threadId':None,'limit':100,'cursor':self.cursor}or self.pages>=8 or self.feature_complete or \
                    not self.feature_active:
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
            if value['method']=='remoteControl/status/changed'and value['params']['status']!='disabled':
                raise ServiceError('CSS_PRECHECK_REMOTE_STATUS_REJECTED')
            if value['method']=='account/updated'and value['params'].get('authMode')not in {'chatgpt','chatgptAuthTokens'}:
                raise ServiceError('CSS_PRECHECK_AUTH_MODE_NOTIFICATION_REJECTED')
    a=ConfigOnly(c.TRIAL,command=('codex','app-server','--stdio',*metadata_overrides()),read_only=True)
    a.consumed=set();a.audit=[];a.pages=0;a.cursor=None;a.feature_complete=False;a.wall_exceeded=False
    a.feature_active=False;a.snapshots=0;a.schema_temp_cleaned=False
    a.stderr_outcome='NO_REFRESH_FAILURE_SIGNAL'
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


def configured_feature_fact(a,configuration,flags):
    if configuration.get('configured_mcp_disabled')is not True or configuration.get('configured_mcp_count')!=6 or \
        any(flags.get(k)is not False for k in p.FROZEN_DISABLES)or flags.get('code_mode_host')is not True or \
        a.remote_control_status!='disabled'or a.mcp_startup_seen or a.denials:
        raise ServiceError('CSS_PRECHECK_CAPABILITY_REJECTED')
    return {'scope':'configuration and features only; no MCP runtime or loaded thread claim',
        'mcp_configuration':{k:{'enabled':False}for k in REGISTERED_SERVERS},
        'mcp_status_rpc_count':0,'mcp_runtime':{k:'NOT_RUN'for k in (
            'runtimeStatus','authStatus','tools','resources','resourceTemplates','serverCapabilities','loaded_thread')},
        'full_metadata_acceptance':'BLOCKED','features':{k:False for k in p.FROZEN_DISABLES},
        'remote':'disabled','startup_seen':False,'local_native_tool_host':True}


def capability_fact(a,servers,configuration,flags):
    configured_feature_fact(a,configuration,flags)
    fields={'name','runtimeStatus','pluginId','httpOrigin','serverInfo','serverCapabilities',
        'tools','toolsError','resources','resourceTemplates','authStatus'}
    if type(servers)is not dict or set(servers)!={'data','nextCursor'}or \
        type(servers['data'])is not list or servers['nextCursor']is not None or len(servers['data'])!=6:
        raise ServiceError('CSS_PRECHECK_CAPABILITY_REJECTED')
    rows=servers['data']
    if any(type(row)is not dict or set(row)!=fields or type(row['name'])is not str for row in rows)or \
        [x['name']for x in rows]!=sorted(REGISTERED_SERVERS)or \
        any(x['runtimeStatus']is not None or x['authStatus']!='unsupported'or \
            type(x['tools'])is not dict or x['tools']!={}or type(x['resources'])is not list or x['resources']!=[]or \
            type(x['resourceTemplates'])is not list or x['resourceTemplates']!=[]or \
            any(x[k]is not None for k in ('pluginId','serverInfo','serverCapabilities','toolsError'))or \
            x['httpOrigin']is not None and type(x['httpOrigin'])is not str for x in rows):
        raise ServiceError('CSS_PRECHECK_CAPABILITY_REJECTED')
    return {'scope':'unscoped process metadata; runtimeStatus null is no loaded-thread status',
        'mcp':{k:{'configured_enabled':False,'runtimeStatus':None,'authStatus':'unsupported',
            'tools_empty':True,'resources_empty':True,'templates_empty':True,'capabilities_null':True}
            for k in REGISTERED_SERVERS},'loaded_thread':'NOT_RUN','mcp_status_rpc_count':1,
        'remote':'disabled','startup_seen':False,'private_fields_retained':False}


def feature_page(result,templates,start):
    """Public source-ordered page, exact emitted DTO, and canonical cursor."""
    if type(result)is not dict or set(result)!={'data','nextCursor'}or type(result['data'])is not list:
        raise ServiceError('CSS_PRECHECK_FEATURE_CATALOG_REJECTED')
    end=min(start+100,len(templates));expected=templates[start:end]
    if len(result['data'])!=len(expected)or not expected:
        raise ServiceError('CSS_PRECHECK_FEATURE_CATALOG_REJECTED')
    flags={}
    for row,template in zip(result['data'],expected):
        if type(row)is not dict or set(row)!={*template,'enabled'}or type(row['enabled'])is not bool or \
            not same_json({k:v for k,v in row.items()if k!='enabled'},template):
            raise ServiceError('CSS_PRECHECK_FEATURE_CATALOG_REJECTED')
        flags[row['name']]=row['enabled']
    cursor=str(end)if end<len(templates)else None
    if type(result['nextCursor'])is not type(cursor)or result['nextCursor']!=cursor:
        raise ServiceError('CSS_PRECHECK_FEATURE_CURSOR_REJECTED')
    return flags,end


def feature_snapshot(a):
    if a.snapshots>=2 or a.feature_active or not a.initialized_accepted or not a.initialized_sent:
        raise ServiceError('CSS_PRECHECK_PHASE_REJECTED')
    if a.snapshots==0 and a.consumed!={'initialize'}or a.snapshots==1 and a.consumed!=set(METHODS[:-1]):
        raise ServiceError('CSS_PRECHECK_PHASE_REJECTED')
    templates=protocol_proof()['templates'];flags={};start=0
    a.snapshots+=1;a.cursor=None;a.feature_complete=False;a.feature_active=True
    try:
        while not a.feature_complete:
            response=a.call('experimentalFeature/list',{'threadId':None,'limit':100,'cursor':a.cursor})
            page,start=feature_page(response,templates,start)
            if flags.keys()&page.keys():raise ServiceError('CSS_PRECHECK_FEATURE_CATALOG_REJECTED')
            flags.update(page)
        if len(flags)!=152 or any(flags.get(k)is not False for k in p.FROZEN_DISABLES)or flags.get('code_mode_host')is not True:
            raise ServiceError('CSS_PRECHECK_CAPABILITY_REJECTED')
    finally:a.feature_active=False
    return flags


def run():
    folder=c.HUB_ROOT/c.PRIVATE;authority=json.loads((folder/AUTHORITY).read_text())
    effects=MetadataEffects();effects.install()
    record={'contract':CONTRACT,'status':'PARTIAL_BLOCKED','threads':0,'resumes':0,'model_turns':0,
        'full_metadata_acceptance':'BLOCKED','mcp_status_rpc_count':0,'fifth_resume_allowed':False}
    a=None;before=None;source=None;trial=None;auth=None;receipt=None;mcp_after=None
    try:
        auth=credential_snapshot()
        initial=admit(authority)
        effects.source_receipt=initial.source
        effects.source_check=lambda:(desktop_source_snapshot(),protocol_source_snapshot())
        if authority['expires_at']-time.time()<150:raise ServiceError('CSS_PRECHECK_CLOSE_HEADROOM')
        receipt=authentication_receipt()
        if credential_snapshot()!=auth:raise ServiceError('CSS_SDK_CREDENTIAL_CHANGED_DURING_FIRST_AUTH')
        before=observed_verified(authority['semantic_pin'],receipt)
        source=r.source_snapshot();trial=c.placeholder_snapshot()
        task=r.TaskStore(c.HUB_ROOT).task(c.TASK)
        if source!=authority['source']or trial!=authority['trial']or authority.get('credential_storage_safe')is not True or c.digest(task)!=authority['task_hash']:
            raise ServiceError('CSS_PRECHECK_INPUT_CHANGED')
        r.mapping_check(task['intent']['grant'])
        a=adapter(authority['semantic_pin'],authority,receipt,effects)
        a.notification_recorder=lambda n,f:atomic_record(folder/f'config-precheck-notification-v11-7-{n}.json',f)
        a.open();record['pid']=a.proc.pid
        drain(a);flags_before=feature_snapshot(a)
        record['catalog']={'public_catalog_count':152,'expected_complete_snapshots':2,'complete_snapshots':1,
            'shared_pages':a.pages,'before_templates_exact':True,'after_templates_exact':False,
            'all_enabled_flags_stable':'NOT_RUN','source_sha256':PROTOCOL_SOURCE_SHA}
        response=a.call('config/read',config_params());record['configuration']=config_fact(response,authority['semantic_pin'])
        record['configuration_features']=configured_feature_fact(a,record['configuration'],flags_before)
        if credential_snapshot()!=auth:raise ServiceError('CSS_SDK_CREDENTIAL_CHANGED_OUTSIDE_MCP')
        record['mcp_status_rpc_count']=1
        servers=a.call('mcpServerStatus/list',mcp_params());mcp_after=credential_snapshot()
        record['capabilities']=capability_fact(a,servers,record['configuration'],flags_before)
        drain(a);flags_after=feature_snapshot(a)
        record['catalog'].update(complete_snapshots=2,after_templates_exact=True,shared_pages=a.pages,
            all_enabled_flags_stable=same_json(flags_before,flags_after))
        if not same_json(flags_before,flags_after):raise ServiceError('CSS_PRECHECK_FEATURE_STATE_CHANGED')
        record['configuration_features']=configured_feature_fact(a,record['configuration'],flags_after)
        record['capabilities']=capability_fact(a,servers,record['configuration'],flags_after)
        record['catalog']['all_public_templates_exact']=True
        drain(a);record['status']='PASS_NATIVE_CONFIG_PRECHECK'
    except Exception as error:record['error']=error.code if isinstance(error,ServiceError)else type(error).__name__
    finally:
        def check(name,fn):
            try:record[name]=fn()
            except Exception as error:
                record[name]=False;record.setdefault('closure_errors',[]).append({'check':name,'code':error.code if isinstance(error,ServiceError)else type(error).__name__})
        check('closed',lambda:(a.close(),a.proc is not None and a.proc.poll()is not None)[1]if a else False)
        if getattr(a,'watchdog',None):a.watchdog.cancel();a.watchdog.join()
        check('final_notice_state',lambda:r.final_capability_state(a,record))
        check('auth_mode_preserved',lambda:receipt is not None and authentication_receipt()==receipt)
        check('global_config_preserved',lambda:before is not None and all(p.preservation_fact(before,
            observed_verified(authority['semantic_pin'],receipt)).values()))
        check('source3_unchanged',lambda:r.source_snapshot()==source)
        check('trial_css_four_dirs_unchanged',lambda:c.placeholder_snapshot()==trial)
        def credential_close():
            after=credential_snapshot()
            record['credential_effect']=credential_fact(auth,after,getattr(a,'stderr_outcome','UNKNOWN_STDERR'))
            return mcp_after is not None and after==mcp_after and a.stderr_outcome=='NO_REFRESH_FAILURE_SIGNAL'
        check('auth_storage_effect_bounded',credential_close)
        check('task_row_unchanged',lambda:before is not None and c.digest(r.TaskStore(c.HUB_ROOT).task(c.TASK))==authority['task_hash'])
        check('public_catalog_unchanged',lambda:initial.source[1]==protocol_source_snapshot())
        check('expiry_at_close',lambda:(r.unexpired(authority),True)[1])
        record.update(exit=a.proc.returncode if a and a.proc else None,methods=a.audit if a else[],
            wall_exceeded=a.wall_exceeded if a else False,schema_temp_cleaned=a.schema_temp_cleaned if a else False,
            effects=effects.fact())
        if a and a.proc:record['pid']=a.proc.pid
        if record['exit']!=0 or record['wall_exceeded']or not record['schema_temp_cleaned']or \
            not record['effects']['effect_limits_satisfied']or not all(record[k]for k in ('closed','final_notice_state','auth_mode_preserved',
            'global_config_preserved','source3_unchanged','trial_css_four_dirs_unchanged','auth_storage_effect_bounded',
            'task_row_unchanged','public_catalog_unchanged','expiry_at_close')):record['status']='PARTIAL_BLOCKED'
        if record['status']=='PASS_NATIVE_CONFIG_PRECHECK':record['full_metadata_acceptance']='PASS_UNSCOPED_PROCESS_ONLY'
        atomic_record(folder/RESULT,record)
    return record
