"""Static plan inputs and readiness; no executor, grants or business completion."""
from __future__ import annotations
import copy,json,re
from dataclasses import dataclass
from .service_contract import ServiceError
from .workbench_store import canonical,digest

BASE='P1-R1-PCH-BASE'
POLICY='P1-R3-PSH-POLICY'
ROUTES='P2-R1-PCH-ROUTES'
WINDOW=32
MAX_PLAN_TASKS=512
BOOTSTRAP_IDS=frozenset('''P1-R1-PCH-BASE P1-R1-PCH-FINGERPRINT P1-R1-PCH-ONBOARD P1-R1-PCH-OFFLINE P1-R1-PCH-REFRESH P1-R1-PCH-EVENTS P1-R2-PCH-SEMANTICS P2-R1-PCH-CONTRACT P2-R1-PCH-ADAPTER P2-R1-PCH-ROUTES P2-R2-PCH-DAG P2-R2-PCH-BUDGET P2-R2-PCH-CONTROL P2-R2-PCH-RECOVER P2-R2-PCH-RUNTIME P2-R3-PCH-ORDINARY P2-R3-PCH-PROXY P2-R3-PCH-ISOLATE P1-R1-RECOVER-desktop-downloads-scripts P1-R1-RECOVER-desktop-magnet P1-R3-PSH-POLICY P1-R3-PSH-DECIDE P1-R3-PSH-MIGRATE'''.split())

def reject(code='PLAN_REJECTED'):
 raise ServiceError(code,status=409)

def plan_id(value):
 if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value):reject('PLAN_ID_REJECTED')
 return value

def _validate_plan(value):
 from .task_store import stable_id
 if type(value)is not dict or not {'plan_id','revision','tasks','admission_policy'}<=set(value):reject()
 plan_id(value['plan_id'])
 if type(value['revision'])is not str or not value['revision'] or len(value['revision'])>128:reject()
 rows=value['tasks'];policy=value['admission_policy']
 if type(rows)is not list or not 1<=len(rows)<=MAX_PLAN_TASKS or len(canonical(value))>2*1024*1024:reject('PLAN_CAPACITY')
 if (type(policy)is not dict or policy.get('first_dispatch_task')!=BASE or policy.get('general_execution_ready_after')!=ROUTES
     or type(policy.get('bootstrap_task_ids'))is not list or len(policy['bootstrap_task_ids'])!=len(BOOTSTRAP_IDS) or set(policy['bootstrap_task_ids'])!=BOOTSTRAP_IDS):reject('PLAN_POLICY_REJECTED')
 by={}
 for row in rows:
  if type(row)is not dict or not {'id','phase_id','round_id','project_id','owner_route','depends_on','status','dispatch_priority','admission_class'}<=set(row):reject()
  ident=stable_id(row['id']);stable_id(row['project_id'])
  if len(ident)>128:reject()
  if ident in by:reject('PLAN_DUPLICATE')
  if not isinstance(row['owner_route'],str) or not row['owner_route'].startswith('route:'):reject()
  if not isinstance(row['round_id'],str) or not re.fullmatch(r'P[0-9]+-R[0-9]+',row['round_id']):reject()
  if row['phase_id']!=ident.split('-')[0] or row['status'] not in {'planned','done'}:reject()
  if type(row['dispatch_priority'])is not int or row['dispatch_priority']!=(0 if ident==BASE else 10 if ident==POLICY else 20 if ident=='P2-R2-PCH-DAG' and re.match(r'^1\.[12]\.0(?:-|$)',value['revision']) else 100):reject('PLAN_PRIORITY_REJECTED')
  deps=row['depends_on']
  if type(deps)is not list or len(deps)>MAX_PLAN_TASKS or len(set(deps))!=len(deps):reject()
  if ident in {BASE,POLICY} and deps:reject('PLAN_ENTRANCE_DEPENDENCY')
  for dep in deps:stable_id(dep)
  if row['admission_class']=='bootstrap' and ident not in BOOTSTRAP_IDS:reject('PLAN_BOOTSTRAP_REJECTED')
  if ident in BOOTSTRAP_IDS and row['admission_class']!='bootstrap':reject('PLAN_BOOTSTRAP_REJECTED')
  if row['admission_class'] not in {'bootstrap','routed','verified_project_execution','historical_planning_evidence'}:reject()
  if row['admission_class']=='historical_planning_evidence' and row['status']!='done':reject()
  by[ident]=row
 from .source_admission import validate_policies
 validate_policies(by)
 if BASE not in by:reject('PLAN_BASE_REQUIRED')
 incoming={key:len(row['depends_on']) for key,row in by.items()};children={key:[]for key in by}
 for ident,row in by.items():
  for dep in row['depends_on']:
   if dep not in by:reject('PLAN_MISSING_DEPENDENCY')
   if row['status']=='done' and by[dep]['status']!='done':reject('PLAN_DONE_DEPENDENCY')
   children[dep].append(ident)
 stack=[ident for ident,n in incoming.items() if not n];seen=0
 while stack:
  ident=stack.pop();seen+=1
  for child in children[ident]:
   incoming[child]-=1
   if incoming[child]==0:stack.append(child)
 if seen!=len(by):reject('PLAN_CYCLE')
 return copy.deepcopy(value)

def validate_plan(value):
 try:return _validate_plan(value)
 except ServiceError:raise
 except (ValueError,TypeError,KeyError,AttributeError):reject()

def ready_window(plan,states,accepted,capability,*,limit=WINDOW):
 """Candidates only. Store independently rechecks controls and all authority."""
 plan=validate_plan(plan)
 if type(limit)is not int or not 0<=limit<=WINDOW:reject('PLAN_WINDOW_REJECTED')
 ready=[]
 for row in plan['tasks']:
  ident=row['id']
  if ident in states or ident in accepted or row['status']=='done' or not set(row['depends_on'])<=accepted:continue
  if ident not in BOOTSTRAP_IDS and not capability(copy.deepcopy(row)):continue
  ready.append(row)
 return [row['id'] for row in sorted(ready,key=lambda x:(x['dispatch_priority'],x['id']))[:limit]]

def changed_definitions(old,new):
 old_by={t['id']:t for t in old['tasks']};new_by={t['id']:t for t in new['tasks']}
 if old['plan_id']!=new['plan_id'] or old_by.keys()!=new_by.keys():reject('PLAN_REVISION_IDENTITY')
 return sorted(k for k in old_by if digest(old_by[k])!=digest(new_by[k]))

def plan_history(value):
 """Strict versioned content in the existing plan_inputs row; no new schema.

 Legacy raw plans are read unchanged. Revision envelopes retain every original
 canonical plan/hash and CAS request. Old readers reject this explicit format.
 """
 from .dispatch_budget import split,validate as validate_budget
 inner,budget=split(value)
 if budget is not None:
  result=plan_history(inner);validate_budget(budget,result[0]['plan_id']);return result
 from .source_admission import split as split_source,validate_records
 history,records=split_source(value)
 if records:
  result=plan_history(history);validate_records(records,result[1]);return result
 if type(value)is not dict or value.get('schema_version')!='plan-revisions/1':
  p=validate_plan(value);return p,{digest(p):p},[]
 if set(value)!={'schema_version','plan_id','current_sha256','versions','requests'}:reject('PLAN_HISTORY_CORRUPT')
 versions=value['versions'];requests=value['requests']
 if type(versions)is not list or not 1<=len(versions)<=64 or type(requests)is not list or not 1<=len(requests)<=128:reject('PLAN_HISTORY_CAPACITY')
 known={};revisions=set()
 for row in versions:
  if type(row)is not dict or set(row)!={'sha256','plan'}:reject('PLAN_HISTORY_CORRUPT')
  p=validate_plan(row['plan']);h=digest(p)
  if h!=row['sha256']or h in known or p['plan_id']!=value['plan_id']or p['revision']in revisions:reject('PLAN_HISTORY_CORRUPT')
  known[h]=p;revisions.add(p['revision'])
 current=versions[0]['sha256'];seen={current};ids=set()
 from .task_store import stable_id
 for r in requests:
  if type(r)is not dict or set(r)!={'request_id','from_sha256','to_sha256','operation','affected_task_ids'}:reject('PLAN_HISTORY_CORRUPT')
  stable_id(r['request_id'])
  if r['request_id']in ids or r['from_sha256']!=current or r['to_sha256']not in known or r['to_sha256']==current:reject('PLAN_HISTORY_CORRUPT')
  target=r['to_sha256']
  if r['operation']not in {'reconcile','rollback'}or (r['operation']=='rollback')!=(target in seen):reject('PLAN_HISTORY_CORRUPT')
  if r['affected_task_ids']!=changed_definitions(known[current],known[target]):reject('PLAN_HISTORY_CORRUPT')
  ids.add(r['request_id']);seen.add(target);current=target
 if seen!=known.keys()or current!=value['current_sha256']:reject('PLAN_HISTORY_CORRUPT')
 return known[current],known,requests

@dataclass(frozen=True,slots=True)
class PlanAuthority:
 """Internal server capability, never a request-supplied authorizer or grant."""
 _data:bytes
 _authorize:object
 _controls:object
 _routes:object
 _package:object
 _development:object=None
 _source_verifier:object=None
 @classmethod
 def configured(cls,binding,*,authorize,controls,routes,package_for,development=None,source_evidence_verification=None):
  if not all(callable(x)for x in (authorize,controls,routes,package_for)):reject('PLAN_AUTHORITY_REQUIRED')
  if any(x is not None and not callable(x)for x in (development,source_evidence_verification)):reject('PLAN_AUTHORITY_REQUIRED')
  return cls(canonical(binding),authorize,controls,routes,package_for,development,source_evidence_verification)
 def snapshot(self):return json.loads(self._data)
 def check(self,plan):
  self._authorize();b=self.snapshot()
  if digest(plan)!=b.get('plan_sha256'):reject('PLAN_AUTHORITY_MISMATCH')
  return b
 def registered_source_workspace(self,definition):
  """Exact internal Root registration, not a project-name or execution grant."""
  self._authorize()
  from .source_admission import is_psh_source_task,is_pch_self_source_task,is_registered_project_source_task,workspace
  legacy=is_psh_source_task(definition)
  if not legacy and (is_pch_self_source_task(definition) or not is_registered_project_source_task(definition)):return False
  registered=self.snapshot().get('source_workspaces',{})
  if type(registered)is not dict:return False
  item=registered.get(definition['id']);source=definition.get('source_admission')
  keys={'project_id','workspace'} if legacy else {'project_id','workspace','original_identity'}
  if (type(item)is not dict or set(item)!=keys
      or item['project_id']!=definition['project_id'] or type(source)is not dict):return False
  if not legacy:
   # Root supplies this identity from the pinned first original plan, never
   # from a proposed definition or request. Equality does not grant execution.
   fields={'id','project_id','phase_id','admission_class','owner_route'}
   original=item['original_identity']
   if (type(original)is not dict or set(original)!=fields
       or any(type(original[k])is not str or original[k]!=definition.get(k)for k in fields)):return False
  try:
   workspace(item['workspace']);workspace(source.get('workspace'))
  except ServiceError:return False
  return item['workspace']==source['workspace']
 def development(self,definition):
  self._authorize()
  from .source_admission import is_psh_source_task,is_pch_self_source_task,is_registered_project_source_task
  if (is_psh_source_task(definition) or is_registered_project_source_task(definition) and not is_pch_self_source_task(definition)) and not self.registered_source_workspace(definition):return False
  return self._development is not None and self._development(copy.deepcopy(definition)) is True
 def verify_source(self,record):
  self._authorize()
  return self._source_verifier is not None and self._source_verifier(copy.deepcopy(record)) is True
 def verify(self,record,*,plan_sha256=None):
  self._authorize();b=self.snapshot()
  if len(canonical(record))>8192:reject('PLAN_EVIDENCE_CAPACITY')
  if (type(record)is not dict or not {'schema_version','plan_sha256','task_id','attempt_id','receipt_id','receipt_sha256','evidence_ref','evidence_sha256','effect_state','unknowns','origin','provenance','native_thread_id','session_id'}<=set(record)
      or b.get('verified_evidence',{}).get(record['task_id'])!=digest(record) and b.get('verified_evidence_versions',{}).get(record['task_id'],{}).get(record['plan_sha256'])!=digest(record)):reject('PLAN_EVIDENCE_UNVERIFIED')
  if record['schema_version']!='1.0' or record['plan_sha256']!=(b.get('plan_sha256') if plan_sha256 is None else plan_sha256):reject('PLAN_EVIDENCE_UNVERIFIED')
  if record['session_id'] is not None and not isinstance(record['session_id'],str):reject()
  if record['native_thread_id'] is not None and not isinstance(record['native_thread_id'],str):reject()
  from .task_store import stable_id
  for key in ('task_id','attempt_id','receipt_id'):stable_id(record[key])
  for key in ('receipt_sha256','evidence_sha256'):
   if not isinstance(record[key],str) or len(record[key])!=64 or any(c not in '0123456789abcdef'for c in record[key]):reject()
  if not record['evidence_ref'] or record['origin'] not in {'bootstrap','historical','contract'} or type(record['unknowns'])is not list:reject()
  if record['provenance'].get('source')!='root_verified':reject('PLAN_EVIDENCE_UNVERIFIED')
  if record['effect_state'] not in {'none','read_only','scoped_write','unknown'}:reject()
  return record['effect_state']!='unknown' and not record['unknowns']

def authority(value):
 if type(value)is not PlanAuthority:reject('PLAN_AUTHORITY_REQUIRED')
 return value


def validate_offline_snapshot(store,db):
 """Pure checks of complete known plan-v1 content; no live authorization.

 Historical Root provenance/expiry is retained as recorded, never renewed or
 promoted to current grant/capability authority by an offline restore.
 """
 from .task_store import PLAN_TABLES,MAX_TASKS,MAX_EVENTS,stable_id
 from .rebuild_contracts import validate,receipt_binding,fence
 def bad():reject('PLAN_SNAPSHOT_CORRUPT')
 def finite(n):return type(n)in (int,float) and __import__('math').isfinite(n)
 def sha(v):return isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v)
 try:
  if [r[0]for r in db.execute('SELECT version FROM plan_schema')]!=[1]:bad()
  plans={};all_versions={}
  for row in db.execute('SELECT *FROM plan_inputs'):
   p,versions,_=plan_history(store._decode(row['body'],row['hash']))
   if row['id']!=p['plan_id'] or row['id']in plans:bad()
   plans[row['id']]=p;all_versions[row['id']]=versions
  if len(plans)>4:bad()
  definitions={pid:{t['id']:t for t in p['tasks']}for pid,p in plans.items()}
  records={};conflicts=[]
  def evidence(row):
   r=store._decode(row['body'],row['hash']);pid=row['plan_id'];ident=row['task_id']
   required={'schema_version','plan_sha256','task_id','attempt_id','receipt_id','receipt_sha256','evidence_ref','evidence_sha256','effect_state','unknowns','origin','provenance','native_thread_id','session_id'}
   if (type(r)is not dict or not required<=set(r) or len(canonical(r))>8192 or pid not in plans or ident not in definitions[pid]
       or r['schema_version']!='1.0' or r['task_id']!=ident or r['plan_sha256']not in all_versions[pid]):bad()
   for k in ('task_id','attempt_id','receipt_id'):stable_id(r[k])
   if not sha(r['receipt_sha256']) or not sha(r['evidence_sha256']) or not isinstance(r['evidence_ref'],str) or not r['evidence_ref']:bad()
   if type(r['provenance'])is not dict or r['provenance'].get('source')!='root_verified':bad()
   if r['effect_state']not in {'none','read_only','scoped_write','unknown'} or type(r['unknowns'])is not list:bad()
   for key in ('native_thread_id','session_id'):
    if r[key]is not None and (not isinstance(r[key],str) or not r[key]):bad()
   if r['origin']=='bootstrap':
    if ident not in BOOTSTRAP_IDS:bad()
   elif r['origin']=='historical':
    if next(t for t in all_versions[pid][r['plan_sha256']]['tasks']if t['id']==ident)['status']!='done':bad()
   elif r['origin']!='contract':bad()
   return r
  for row in db.execute('SELECT *FROM plan_receipts'):
   r=evidence(row);records[(row['plan_id'],row['task_id'])]=r
  for row in db.execute('SELECT *FROM plan_conflicts'):
   r=evidence(row);key=(row['plan_id'],row['task_id'])
   if key not in records or r==records[key]:bad()
   conflicts.append((key,r))
  tasks={};sequences=set();archived=set()
  for table in ('tasks','plan_archive_tasks'):
   for row in db.execute('SELECT *FROM '+table):
    ident=stable_id(row['id']);seq=row['sequence']
    if type(seq)is not int or seq<=0 or seq in sequences or ident in tasks:bad()
    sequences.add(seq);task=store._task(row)
    if task['payload'].get('request_id')!=ident or task['payload'].get('project_id')!=task['project'] or task['payload'].get('parent_task_id')!=task['parent']:bad()
    if not finite(task['created']) or task['created']<0:bad()
    if table=='plan_archive_tasks':
     archived.add(ident)
     if task['status']!='checks_complete' or task['cancel']!=0:bad()
    tasks[ident]=task
  for task in tasks.values():
   if task['parent']is not None and (task['parent']not in tasks or task['parent']==task['id']):bad()
  max_tasks=max((len(p['tasks'])for p in plans.values()),default=0)
  if len(archived)>max_tasks:bad()
  archive_events=db.execute('SELECT count(*)FROM plan_archive_events').fetchone()[0]
  if archive_events>MAX_EVENTS*((max_tasks+WINDOW-1)//WINDOW):bad()
  history={ident:[]for ident in tasks};event_seq=set();event_keys=set()
  for table in ('events','plan_archive_events'):
   for row in db.execute('SELECT *FROM '+table+' ORDER BY sequence'):
    ident=row['task_id'];seq=row['sequence'];key=row['event_key']
    stable_id(key)
    if ident not in tasks or type(seq)is not int or seq<=0 or seq in event_seq or (ident,key)in event_keys or not finite(row['received'])or row['received']<0:bad()
    if (ident in archived)!=(table=='plan_archive_events'):bad()
    body=store._decode(row['body'],row['hash'])
    if len(canonical(body))>8192:bad()
    event_seq.add(seq);event_keys.add((ident,key));history[ident].append({'sequence':seq,'key':key,'data':body,'received':row['received']})
  counters={r['name']:r['seq']for r in db.execute('SELECT *FROM sqlite_sequence')}
  if any(name not in {'tasks','events','rr_feedback'}or type(n)is not int or n<0 for name,n in counters.items()):bad()
  if sequences and counters.get('tasks',0)<max(sequences) or event_seq and counters.get('events',0)<max(event_seq):bad()
  def contract(ident,r,pid):
   if ident not in tasks:bad()
   task=tasks[ident];events=sorted(history[ident],key=lambda e:e['sequence']);values={e['key']:e['data']for e in events}
   p=validate(values.get('rebuild-package'),'package');a=validate(values.get('rebuild-attempt-'+r['attempt_id']),'attempt');receipt=validate(values.get('rebuild-receipt-'+r['receipt_id']),'receipt')
   receipt_binding(receipt,p,a)
   if (p['task_id']!=ident or p['project_id']!=task['project'] or task['payload'].get('kind')!='rebuild_project'
       or task['intent'].get('package_sha256')!=digest(p) or digest(receipt)!=r['receipt_sha256']
       or task['intent'].get('grant_hash')!=digest(task['intent']['grant']) or p['grant']['sha256']!=digest(task['intent']['grant'])):bad()
   origin=all_versions[pid][r['plan_sha256']];definition=next(t for t in origin['tasks']if t['id']==ident)
   if p['plan']!={'revision':origin['revision'],'sha256':digest(origin)} or p['lineage']['goal_id']!=pid or any(p[k]!=definition[k]for k in ('project_id','phase_id','round_id'))or p['route']['owner_route']!=definition['owner_route']:bad()
   if {d['task_id']for d in p['dependencies']}!=set(definition['depends_on']):bad()
   proof=r['provenance'];expected={'task_id':ident,'package_sha256':digest(p),'attempt_sha256':digest(a),'receipt_sha256':digest(receipt)}
   if any(proof.get(k)!=v for k,v in expected.items()) or not proof.get('evidence_ref') or not sha(proof.get('evidence_sha256')) or not finite(proof.get('expires_at')):bad()
   if r['effect_state']!=receipt['effect_state'] or r['unknowns']!=receipt['unknowns']:bad()
   # Original complete settlement evidence, without consulting wall-clock expiry.
   required={c['check_id']for c in p['acceptance']if c['required']};checks={c['check_id']:c for c in receipt['checks']}
   if not required or not required<=checks.keys() or receipt['producer']['self_report'] or receipt['effect_state']=='unknown' or receipt['unknowns']:bad()
   for name in required:
    ck=checks[name]
    if ck['exit']!=0 or ck['candidate_hash']!=p['source_version'] or not ck['scope']or not ck['evidence_ref']:bad()
   for name in ('delivery','integration','sync'):
    policy=p[name];item=receipt[name]
    if policy['required'] and ((item['status']!='passed' and not(policy['allow_not_applicable']and item['status']=='not_applicable'))or not item['evidence_ref']or item['source_revision']!=p['source_version']):bad()
   if task['status']!='checks_complete' or task['cancel']:bad()
   return p
  for key,r in records.items():
   pid,ident=key
   if r['origin']=='contract':contract(ident,r,pid)
   elif ident in tasks:bad()
  for (pid,ident),r in conflicts:
   # Conflicts may be unresolved historical observations, never accepted work.
   if r['origin']=='contract':contract(ident,r,pid)
  for ident in archived:
   witnesses=[(pid,r)for (pid,key),r in [*records.items(),*conflicts]if key==ident and r['origin']=='contract' and not r['unknowns']and r['effect_state']!='unknown']
   if not witnesses:bad()
   for pid,r in witnesses:contract(ident,r,pid)
   # Every archived versioned attempt/receipt and original lease link is checked.
   events=sorted(history[ident],key=lambda e:e['sequence']);package=None;claims={};until={};attempts={};stopped=set()
   for e in events:
    key=e['key'];v=e['data']
    if key=='rebuild-package':package=validate(v,'package')
    elif key.startswith('rebuild-claim-'):
     if (set(v)!={'schema_version','provenance','task_id','owner','generation','started_at','expires_at','fence'}or v['schema_version']!='2.0' or v['provenance']!='task_store' or v['task_id']!=ident or key!='rebuild-claim-'+v['generation']
         or v['fence']!=fence(v) or v['expires_at']!=v['started_at']+15 or not 0<=e['received']-v['started_at']<=1):bad()
     claims[v['generation']]=v;until[v['generation']]=v['expires_at']
    elif key.startswith('rebuild-renew-'):
     g=v['generation'];cl=claims[g]
     if g in stopped:bad()
     if (set(v)!={'provenance','task_id','owner','generation','fence','prior_expires_at','at','expires_at'}or v['provenance']!='task_store' or v['task_id']!=ident or v['owner']!=cl['owner']or v['fence']!=cl['fence']or v['prior_expires_at']!=until[g]
         or not cl['started_at']<=v['at']<=until[g]or v['expires_at']!=v['at']+15 or not 0<=e['received']-v['at']<=1):bad()
     until[g]=v['expires_at']
    elif key.startswith('rebuild-lease-stop-'):
     if set(v)!={'at','status','generation'}or v['generation']not in claims or not finite(v['at'])or v['at']<claims[v['generation']]['started_at']or not 0<=e['received']-v['at']<=1:bad()
     stopped.add(v['generation'])
    elif key.startswith('rebuild-attempt-'):
     a=validate(v,'attempt');g=a['lease']['generation'];cl=claims[g]
     if g in stopped:bad()
     if key!='rebuild-attempt-'+a['attempt_id']or a['task_id']!=ident or a['package_sha256']!=digest(package)or a['lease']['fence']!=cl['fence']or a['lease']['owner']!=cl['owner']or a['lease']['expires_at']!=until[g]:bad()
     if a['effect_intent']!=package['effects']or a['coverage']!={'claim_started_at':cl['started_at'],'observed_until':until[g]}or not cl['started_at']<=a['started_at']<=e['received']<=until[g]:bad()
     grant=tasks[ident]['intent']['grant'];seconds=a['budget_reservation']['seconds']
     if not 0<seconds<=min(900,package['budget']['remaining_seconds'],package['budget']['timeout_seconds'],grant['max_turn_seconds'])or a['started_at']+seconds>min(package['budget']['deadline'],grant['expires_at']):bad()
     attempts[a['attempt_id']]=a
    elif key.startswith('rebuild-receipt-'):
     receipt=validate(v,'receipt');a=attempts[receipt['attempt_id']];receipt_binding(receipt,package,a);g=a['lease']['generation']
     if g in stopped or key!='rebuild-receipt-'+receipt['receipt_id']or receipt['ended_at']>until[g]or e['received']<receipt['ended_at']:bad()
   if not claims:bad()
   last=list(claims.values())[-1]
   if tasks[ident]['owner']!=last['owner']or tasks[ident]['lease_until']!=until[last['generation']]:bad()
  # All optional rows, including hashes and archive bytes, enter content equality.
  return {table:[list(r)for r in db.execute('SELECT *FROM '+table+' ORDER BY rowid')]for table in PLAN_TABLES}
 except ServiceError:raise
 except (ValueError,TypeError,KeyError,AttributeError,IndexError):bad()
