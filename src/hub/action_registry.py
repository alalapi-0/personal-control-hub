"""Closed, read-only handlers over registered design artifacts; no executor."""
from __future__ import annotations
import copy, hashlib, json, re, secrets
from dataclasses import dataclass
from .service_contract import ServiceError
from .graph_service import project_graph

ACTIONS = {
    'open_registered_artifact': ('read', '读取登记产物', 'controlled_artifact_read'),
    'verify_registered_artifact': ('verification', '只读核验产物', 'registered_artifact_sha256'),
    'prepare_ordinary_task': ('task_form', '编写普通任务', 'ordinary_task_form'),
    'native_program': ('native', '原生软件动作尚不可用', 'unavailable'),
}
BINDING_FIELDS = frozenset(('schema_version','project_id','object_id','object_revision','artifact_id',
    'artifact_sha256','candidate_id','candidate_revision','candidate_sha256','classification',
    'source_version','host','backend_instance','binding_sha256'))

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def validate_binding(value):
    if type(value) is not dict or set(value)!=BINDING_FIELDS or value.get('schema_version')!='1.0':
        raise ServiceError('ACTION_BINDING_INVALID')
    for key in BINDING_FIELDS-{'candidate_revision'}:
        if type(value[key]) is not str or not 0<len(value[key])<=4096 or any(ord(c)<32 for c in value[key]):
            raise ServiceError('ACTION_BINDING_INVALID')
    if type(value['candidate_revision']) is not int or not 1<=value['candidate_revision']<=999999999:
        raise ServiceError('ACTION_BINDING_INVALID')
    if any(not re.fullmatch('[a-f0-9]{64}',value[k])for k in ('artifact_sha256','candidate_sha256','source_version','binding_sha256')):
        raise ServiceError('ACTION_BINDING_INVALID')
    if digest({k:v for k,v in value.items()if k!='binding_sha256'})!=value['binding_sha256']:
        raise ServiceError('ACTION_BINDING_INVALID')
    return value

def request_id(action_id,binding):
    return 'read-'+digest({'action_id':action_id,'binding':binding})

def input_schema(action_id):
    binding = dict(type='object', additionalProperties=False, required=sorted(BINDING_FIELDS),
        properties={k: (dict(type='integer', minimum=1, maximum=999999999) if k=='candidate_revision'
            else dict(type='string', minLength=1, maxLength=4096)) for k in sorted(BINDING_FIELDS)})
    for key in ('artifact_sha256','candidate_sha256','source_version','binding_sha256'):
        binding['properties'][key] = dict(type='string', pattern='^[a-f0-9]{64}$')
    binding['properties']['schema_version'] = dict(const='1.0')
    binding['properties']['backend_instance'] = dict(type='string', pattern='^[a-f0-9]{32}$')
    binding['properties']['host'] = dict(type='string', pattern=r'^http://127\.0\.0\.1:[0-9]{1,5}$')
    properties = dict(binding=binding)
    if action_id=='verify_registered_artifact':
        properties.update(action_id=dict(const=action_id), request_id=dict(type='string', pattern='^read-[a-f0-9]{64}$'))
    return dict(schema_version='action-input/1', type='object', additionalProperties=False,
                required=sorted(properties), properties=properties)

def port_contract(action_id, binding):
    """Closed capabilities of these existing handlers, never model modalities."""
    validate_binding(binding)
    if action_id not in ACTIONS: raise ServiceError('ACTION_PORT_REJECTED')
    handler=ACTIONS[action_id][2]
    task=action_id=='prepare_ordinary_task'; native=action_id=='native_program'
    input_type='task_form_navigation_context' if task else 'registered_artifact'
    output_type='ordinary_task_form' if task else 'sha256_observation' if action_id=='verify_registered_artifact' else 'bytes'
    version={k:binding[k] for k in ('binding_sha256','object_id','object_revision','source_version',
                                  'artifact_sha256','candidate_id','candidate_revision','candidate_sha256')}
    def port(direction, kind):
        return dict(id=direction,direction=direction,type=kind,version=copy.deepcopy(version))
    capabilities=([] if native else ['ordinary_task_form_navigation'] if task else
                  ['registered_artifact_bounded_read','sha256_verification'] if action_id=='verify_registered_artifact'
                  else ['registered_artifact_bounded_read'])
    tool=dict(schema_version='action-tool/1',handler=handler,available=not native,capabilities=capabilities,
              input_types=[] if native else [input_type],output_types=[] if native else [output_type],
              native_execution=False,durable_completion=False)
    ports=dict(schema_version='action-ports/1',tool=handler,
               inputs=[] if native else [port('input',input_type)],outputs=[] if native else [port('output',output_type)])
    return tool,ports


def validate_port_compatibility(value, binding):
    """Production pre-read guard: serialized wiring never expands this registry."""
    validate_binding(binding)
    validate_descriptor(value)
    if value['binding']!=binding: raise ServiceError('ACTION_PORT_VERSION_CHANGED',status=409)
    if not value['available'] or not value['tool']['available']:
        raise ServiceError('ACTION_HANDLER_UNAVAILABLE',status=403)
    return copy.deepcopy(value)


def descriptor(action_id, binding):
    validate_binding(binding)
    if action_id not in ACTIONS: raise ServiceError('ACTION_DESCRIPTOR_INVALID')
    kind,label,handler=ACTIONS[action_id]
    task=action_id=='prepare_ordinary_task'; native=action_id=='native_program'
    tool,ports=port_contract(action_id,binding)
    return dict(schema_version='1.2',tool=tool,ports=ports,id=action_id,kind=kind,label=label,handler=handler,
        available=not native,reason='NATIVE_HANDLER_UNAVAILABLE' if native else None,binding=copy.deepcopy(binding),
        object_type='Artifact',host=dict(origin=binding['host'],backend_instance=binding['backend_instance']),
        input_schema=input_schema(action_id),required_capabilities=(['existing_ordinary_task_form'] if task else
            ['native_handler_unavailable'] if native else ['registered_artifact_bounded_read']),
        authorization=dict(session='permitted_session',owner_csrf=action_id=='verify_registered_artifact',
            current_project_binding=True,execution_permission=False,
            deferred_task_authorization='existing_owner_csrf_grant_cas_budget_executor_gates' if task else None),
        effect_class='task_form_navigation' if task else 'unavailable' if native else 'local_read_only',
        idempotency=dict(mode='existing_ordinary_api_requests' if task else 'unavailable' if native else 'content_addressed_revalidation',
            durable_receipt=False),
        reconcile=dict(method='existing_ordinary_context_draft_submit' if task else 'unavailable' if native else 'revalidate_exact_binding',
            auto_retry=False,execution_deferred=task))

def validate_descriptor(value):
    if type(value) is not dict or value.get('id') not in ACTIONS:
        raise ServiceError('ACTION_DESCRIPTOR_INVALID')
    expected=descriptor(value['id'],value.get('binding'))
    if any(value.get(k)!=expected[k] for k in ('tool','ports')): raise ServiceError('ACTION_PORT_REJECTED')
    if digest(value)!=digest(expected): raise ServiceError('ACTION_DESCRIPTOR_INVALID')
    return value

@dataclass(frozen=True)
class ControlledArtifactRead:
    response: object
    receipt: dict

def read_receipt(command,response,handler,*,declaration):
    return dict(schema_version='local-read/1',run_id='local-read-'+secrets.token_hex(16),handler=handler,
        request_id=command['request_id'],parameter_sha256=digest(command),input_sha256=command['binding']['artifact_sha256'],
        binding_sha256=command['binding']['binding_sha256'],bytes_read=len(response.data),
        frozen_input=copy.deepcopy(command['binding']),tool=copy.deepcopy(declaration['tool']),
        ports=copy.deepcopy(declaration['ports']),declaration_sha256=digest(declaration),
        durable_receipt=False,external_effect=False,native_execution=False)

class ActionRegistry:
    def __init__(self,projects,designs,*,host,backend_instance):
        # Trusted server composition only, never supplied by an HTTP caller.
        if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',host) or not re.fullmatch('[a-f0-9]{32}',backend_instance):
            raise ServiceError('ACTION_HOST_UNAVAILABLE',status=503)
        self.projects,self.designs,self.host,self.instance=projects,designs,host,backend_instance

    def catalog(self,project_id):
        self.projects.assert_action_project(project_id) # Before design/content access.
        if self.designs is None:raise ServiceError('ACTION_CATALOG_UNAVAILABLE',status=503)
        snapshot=self.designs.snapshot()
        dto=self.projects.action_project(project_id,design_snapshot=snapshot)
        if project_id == 'personal-control-hub':
            # Reuse the already authorized DTO and its exact trusted design data.
            # Domain augmentation must precede the catalog's source/version binding.
            from .pch_domain_actions import snapshot as pch_snapshot
            _, _, graph = pch_snapshot(self.projects, design_snapshot=snapshot, dto=dto)
        else:
            graph=project_graph(dto)
        entries=[]
        for obj,row in zip(graph['objects'],graph['rows']):
            if obj['type']!='Artifact':continue
            candidates=obj['data'].get('action_candidates',[])
            for candidate in candidates:
                binding=dict(schema_version='1.0',project_id=project_id,object_id=obj['id'],object_revision=obj['revision'],
                    artifact_id=obj['native_id'],artifact_sha256=obj['data']['artifact']['sha256']['value'],
                    candidate_id=candidate['id'],candidate_revision=candidate['revision'],candidate_sha256=candidate['content_hash'],
                    classification=candidate['classification'],source_version=graph['source_version'],host=self.host,backend_instance=self.instance)
                binding['binding_sha256']=digest(binding);validate_binding(binding)
                actions=[validate_descriptor(descriptor(ident,binding)) for ident in ACTIONS]
                entries.append(dict(object=copy.deepcopy(obj),row=copy.deepcopy(row),actions=actions))
            if not candidates:
                entries.append(dict(object=copy.deepcopy(obj),row=copy.deepcopy(row),actions=[],reason='NO_EXACT_REGISTERED_CANDIDATE_BINDING'))
        if len(entries)>1000:raise ServiceError('ACTION_CATALOG_CAPACITY',status=503)
        return dict(schema_version='1.0',project_id=project_id,source_version=graph['source_version'],
                    projection_version=graph['projection_version'],host=self.host,backend_instance=self.instance,
                    entries=entries,read_only=True,native_execution_available=False)

    def _current(self,binding,action_id='open_registered_artifact'):
        validate_binding(binding)
        if binding['host']!=self.host or binding['backend_instance']!=self.instance:
            raise ServiceError('ACTION_HOST_CHANGED',status=409)
        catalog=self.catalog(binding['project_id'])
        matches=[action for entry in catalog['entries']for action in entry['actions']
                 if action.get('id')==action_id and action.get('binding')==binding]
        if len(matches)!=1: raise ServiceError('ACTION_BINDING_STALE',status=409)
        return validate_port_compatibility(matches[0],binding)

    def _read_bound(self,binding,*,authorize,action_id):
        authorize();declaration=self._current(binding,action_id)
        response=self.designs.artifact(binding['artifact_id'],candidate_id=binding['candidate_id'],candidate_revision=binding['candidate_revision'])
        if response.sha256!=binding['artifact_sha256'] or hashlib.sha256(response.data).hexdigest()!=binding['artifact_sha256']:
            raise ServiceError('ACTION_ARTIFACT_CHANGED',status=409)
        authorize();current=self._current(binding,action_id)
        if current!=declaration: raise ServiceError('ACTION_PORT_VERSION_CHANGED',status=409)
        return response,declaration

    def read(self,binding,*,authorize):
        response,_=self._read_bound(binding,authorize=authorize,action_id='open_registered_artifact')
        return response

    def verify(self,command,*,authorize):
        if type(command)is not dict or set(command)!={'action_id','binding','request_id'} or command['action_id']!='verify_registered_artifact':
            raise ServiceError('ACTION_COMMAND_INVALID')
        binding=validate_binding(command['binding'])
        if command['request_id']!=request_id(command['action_id'],binding):
            raise ServiceError('ACTION_REQUEST_CONFLICT',status=409)
        response,declaration=self._read_bound(binding,authorize=authorize,action_id=command['action_id'])
        return dict(read_receipt(command,response,'registered_artifact_sha256',declaration=declaration),schema_version='1.0',status='verified_read')

    def read_with_receipt(self,binding,*,authorize):
        # One actual read; receipt is an observation, never a durable execution claim.
        response,declaration=self._read_bound(binding,authorize=authorize,action_id='open_registered_artifact')
        command=dict(action_id='open_registered_artifact',binding=binding,
                     request_id=request_id('open_registered_artifact',binding))
        return ControlledArtifactRead(response,read_receipt(command,response,'controlled_artifact_read',declaration=declaration))
