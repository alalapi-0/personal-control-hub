"""Pure lineage accounting and internal authority; never invokes or stops work."""
from __future__ import annotations
import copy,json,math
from dataclasses import dataclass
from .service_contract import ServiceError
from .workbench_store import canonical,digest

FORMAT='plan-dispatch-budget/1'
def reject(code='DISPATCH_BUDGET_REJECTED'):raise ServiceError(code,status=409)
def need(ok,code='DISPATCH_BUDGET_REJECTED'):
 if not ok:reject(code)
def ident(v):
 from .task_store import stable_id
 stable_id(v);need(len(v)<=128);return v
def hash_value(v):return type(v)is str and len(v)==64 and all(c in '0123456789abcdef'for c in v)
def finite(v):return type(v)in(int,float)and math.isfinite(v)
def policy(value):
 keys={'schema_version','lineage_id','source_state_sha256','cutoff_id','covered_dispatch_ids','observed_model_dispatches','historical_unknown_allowance','closure_reserve','conservative_deterministic_dispatches','limit','provenance_ref'}
 from .plan_admission import plan_id
 need(type(value)is dict and set(value)==keys);need(value['schema_version']=='1.0'and value['limit']==480 and value['historical_unknown_allowance']==8 and value['closure_reserve']==12);plan_id(value['lineage_id']);ident(value['cutoff_id']);need(hash_value(value['source_state_sha256'])and type(value['provenance_ref'])is str and 0<len(value['provenance_ref'])<=512)
 ids=value['covered_dispatch_ids'];need(type(ids)is list and len(ids)<=480 and len(set(ids))==len(ids));[ident(x)for x in ids]
 for k in ('observed_model_dispatches','conservative_deterministic_dispatches'):need(type(value[k])is int and 0<=value[k]<=480)
 need(len(ids)<=value['observed_model_dispatches']+value['conservative_deterministic_dispatches']);need(value['observed_model_dispatches']+value['conservative_deterministic_dispatches']+20<=480)
 return copy.deepcopy(value)
def request(value):
 keys={'dispatch_id','task_id','attempt_id','package_sha256','executor','purpose','effect','gpu','seconds','deadline'}
 need(type(value)is dict and set(value)==keys and len(canonical(value))<=4096)
 for k in ('dispatch_id','task_id','attempt_id'):ident(value[k])
 need(value['package_sha256']is None or hash_value(value['package_sha256']));need(value['purpose']in {'normal','closure'}and value['effect']in {'read_only','scoped_write'}and type(value['gpu'])is bool);need(type(value['seconds'])is int and 0<value['seconds']<=900 and finite(value['deadline']))
 ex=value['executor'];need(type(ex)is dict and ex.get('kind')in {'model','deterministic_host'})
 if ex['kind']=='model':
  # Native identities may be unavailable until the reserved invocation starts.
  # Explicit null is unknown, never a fabricated prior turn or authority.
  need({'thread_id','turn_id','session_id'}<=set(ex))
  need(all(ex[k]is None or type(ex[k])is str and bool(ex[k])for k in ('thread_id','turn_id','session_id')))
 else:need(ex.get('thread_id')is None and ex.get('turn_id')is None and type(ex.get('host_id'))is str and ex['host_id'])
 need(ex.get('session_id')is None or type(ex['session_id'])is str)
 return copy.deepcopy(value)
def executor_binding_matches(reserved,actual):
 """Known reservation facts must match; Root separately verifies the full IDs.

 Only explicit null native identities in model reservations are unresolved.
 The actual executor stays in the immutable v2 attempt, not rewritten here.
 """
 if type(actual)is not dict or reserved['kind']!=actual.get('kind'):return False
 unknown={'thread_id','turn_id','session_id'}if reserved['kind']=='model'else set()
 return all(k in actual and (v is None and k in unknown or canonical(actual[k])==canonical(v))for k,v in reserved.items())

def closure(value):
 need(type(value)is dict and set(value)=={'closure_id','dispatch_id','reservation_sha256','outcome','resources_closed','evidence_ref','evidence_sha256','token_usage','cost','native_usage'})
 ident(value['closure_id']);ident(value['dispatch_id']);need(hash_value(value['reservation_sha256'])and hash_value(value['evidence_sha256'])and type(value['evidence_ref'])is str and 0<len(value['evidence_ref'])<=512)
 need(value['outcome']in {'succeeded','failed','unknown'}and type(value['resources_closed'])is bool)
 # Missing telemetry is never manufactured as zero. Future native integration
 # may introduce a separately versioned trustworthy measurement representation.
 need(all(value[k]is None for k in ('token_usage','cost','native_usage')))
 return copy.deepcopy(value)
def validate(value,lineage):
 need(type(value)is dict and set(value)=={'schema_version','policy','reservations'}and value['schema_version']=='dispatch-budget/1','DISPATCH_BUDGET_CORRUPT')
 p=policy(value['policy']);need(p['lineage_id']==lineage);rows=value['reservations'];need(type(rows)is list and len(rows)<=480 and len(canonical(value))<=1024*1024)
 ids=set();closed_ids=set()
 for row in rows:
  need(type(row)is dict and set(row)=={'request','sha256','reserved_at','closures'});r=request(row['request']);need(row['sha256']==digest(r)and r['dispatch_id']not in ids and r['dispatch_id']not in p['covered_dispatch_ids']);ids.add(r['dispatch_id']);need(finite(row['reserved_at'])and row['reserved_at']+r['seconds']<=r['deadline']<=row['reserved_at']+900)
  need(type(row['closures'])is list and len(row['closures'])<=4)
  for c in row['closures']:
   closure(c);need(c['dispatch_id']==r['dispatch_id']and c['reservation_sha256']==row['sha256']and c['closure_id']not in closed_ids);closed_ids.add(c['closure_id'])
  need(not any(c['resources_closed']for c in row['closures'][:-1]),'DISPATCH_CLOSURE_CONFLICT')
 total=p['observed_model_dispatches']+p['conservative_deterministic_dispatches']+len(rows)
 need(total+p['historical_unknown_allowance']<=480);need(sum(r['request']['purpose']=='closure'for r in rows)<=p['closure_reserve'])
 need(p['observed_model_dispatches']+p['conservative_deterministic_dispatches']+sum(r['request']['purpose']=='normal'for r in rows)+20<=480)
 held=[r['request']for r in rows if not any(c['resources_closed']for c in r['closures'])]
 need(sum(r['effect']=='scoped_write'for r in held)<=1 and sum(r['executor']['kind']=='model'and r['effect']=='read_only'for r in held)<=2 and sum(r['gpu']for r in held)<=1,'DISPATCH_RESOURCE_CAPACITY')
 return copy.deepcopy(value)
def split(value):
 if type(value)is dict and isinstance(value.get('schema_version'),str)and value['schema_version'].startswith('plan-dispatch-budget/')and value['schema_version']!=FORMAT:reject('DISPATCH_BUDGET_CORRUPT')
 if type(value)is dict and value.get('schema_version')==FORMAT:
  need(set(value)=={'schema_version','plan_history','dispatch_budget'},'DISPATCH_BUDGET_CORRUPT');need(not(type(value['plan_history'])is dict and value['plan_history'].get('schema_version')==FORMAT),'DISPATCH_BUDGET_CORRUPT');return value['plan_history'],value['dispatch_budget']
 return value,None
def wrap(history,budget):return {'schema_version':FORMAT,'plan_history':history,'dispatch_budget':budget}if budget is not None else history

def status(value):
 p=value['policy'];rows=value['reservations'];held=[r['request']['dispatch_id']for r in rows if not any(c['resources_closed']for c in r['closures'])]
 return {'lineage_id':p['lineage_id'],'policy_sha256':digest(p),'observed_model_dispatches':p['observed_model_dispatches'],'historical_unknown_allowance':p['historical_unknown_allowance'],'closure_reserve':p['closure_reserve'],'conservative_deterministic_dispatches':p['conservative_deterministic_dispatches'],'reserved_model_dispatches':sum(r['request']['executor']['kind']=='model'for r in rows),'reserved_deterministic_dispatches':sum(r['request']['executor']['kind']=='deterministic_host'for r in rows),'charged_total':p['observed_model_dispatches']+p['conservative_deterministic_dispatches']+len(rows),'held_dispatch_ids':held,'token_usage':None,'cost':None,'native_usage':None,'invocation_integration':'not established by accounting APIs'}

@dataclass(frozen=True,slots=True)
class BudgetAuthority:
 """Root/server-only callbacks and immutable exact policy/request bindings."""
 _data:bytes
 _plan:object
 _authorize:object
 _controls:object
 _resource:object
 @classmethod
 def configured(cls,binding,*,plan_authority,authorize,controls,resource_check):
  from .plan_admission import authority
  authority(plan_authority);need(all(callable(x)for x in(authorize,controls,resource_check)),'DISPATCH_AUTHORITY_REQUIRED')
  need(type(binding)is dict and set(binding)<= {'policy_sha256','requests','resource_authority_sha256'}and hash_value(binding.get('policy_sha256'))and type(binding.get('requests'))is dict,'DISPATCH_AUTHORITY_REQUIRED')
  return cls(canonical(binding),plan_authority,authorize,controls,resource_check)
 def snapshot(self):return json.loads(self._data)
 def observe_controls(self,plan,p,operation,body):
  """Trusted callback observation, not a native Goal API or execution grant.

  revision is host-provided identity only, never invented native revision.
  Provider errors/missing controls are unknown; no fallback or background poller.
  """
  self._authorize();self._plan.check(plan);b=self.snapshot()
  need(b['policy_sha256']==digest(p)and b['requests'].get(operation)==digest(body),'DISPATCH_AUTHORITY_MISMATCH')
  try:
   value=self._controls()
   if (type(value)is not dict or len(canonical(value))>4096 or type(value.get('revision'))is not int
       or value['revision']<0 or value.get('state')not in {'active','paused','cancelled'}):raise ValueError()
   identity={k:v for k,v in value.items()if k not in {'state','observed_at'}}
   return {'state':value['state'],'revision':value['revision'],'binding_sha256':digest(identity),
           'source':'trusted_budget_authority_callback'}
  except Exception:
   return {'state':'unknown','revision':None,'binding_sha256':None,'source':'trusted_budget_authority_callback'}
 def check(self,plan,p,operation,body):
  controls=self.observe_controls(plan,p,operation,body);b=self.snapshot()
  need(controls['state']=='active','DISPATCH_CONTROL_BLOCKED')
  if operation=='reserve'and body['gpu']:need(hash_value(b.get('resource_authority_sha256'))and self._resource(copy.deepcopy(body))is True,'DISPATCH_GPU_AUTHORITY_REQUIRED')
  if operation=='close'and body['resources_closed']:need(self._resource(copy.deepcopy(body))is True,'DISPATCH_CLOSURE_UNVERIFIED')
  return controls

def authority(value):
 need(type(value)is BudgetAuthority,'DISPATCH_AUTHORITY_REQUIRED');return value

@dataclass(frozen=True,slots=True)
class NormalDispatchBinding:
 """Internal ordinary-task selection, never a grant or HTTP capability."""
 _data:bytes
 _authority:BudgetAuthority
 @classmethod
 def configured(cls,*,lineage_id,request_value,grant,authority_value):
  from .plan_admission import plan_id
  plan_id(lineage_id);r=request(request_value);cap=authority(authority_value)
  need(grant.get('kind')=='ordinary_project'and r['package_sha256']is None
       and r['executor']['kind']=='model'and r['executor']['turn_id']is None
       and r['executor']['session_id']is None and r['purpose']=='normal'
       and r['effect']==grant.get('effect')=='read_only','NORMAL_DISPATCH_BINDING_REJECTED')
  need(cap.snapshot()['requests'].get('reserve')==digest(r),'NORMAL_DISPATCH_BINDING_REJECTED')
  return cls(canonical({'lineage_id':lineage_id,'request':r,'grant_sha256':digest(grant)}),cap)
 def snapshot(self):return json.loads(self._data)
 def marker(self,task_id,grant):
  b=self.snapshot();r=b['request']
  need(r['task_id']==task_id and b['grant_sha256']==digest(grant),'NORMAL_DISPATCH_BINDING_REJECTED')
  return {'schema_version':'normal-dispatch/1','lineage_id':b['lineage_id'],'task_id':task_id,
          'dispatch_id':r['dispatch_id'],'request_sha256':digest(r),'grant_sha256':digest(grant)}

def normal_bindings(value):
 if value is None:return {}
 need(type(value)is dict,'NORMAL_DISPATCH_BINDING_REJECTED')
 for key,binding in value.items():
  ident(key);need(binding is None or type(binding)is NormalDispatchBinding,'NORMAL_DISPATCH_BINDING_REJECTED')
  if binding is not None:need(binding.snapshot()['request']['task_id']==key,'NORMAL_DISPATCH_BINDING_REJECTED')
 return dict(value)
