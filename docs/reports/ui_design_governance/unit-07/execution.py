import contextlib,hashlib,json,os,sys,subprocess
from pathlib import Path
from unittest import mock
from collections import Counter
R=Path('/Users/alalapi/PycharmProjects/personal-control-hub');U=R/'docs/reports/ui_design_governance/unit-07';sys.path.insert(0,str(R/'src'))
from hub.connection_manager_cli import DEFAULT_BUNDLES,load_bundles,result_validator,current_authority_status
from hub.connection_refresh import RefreshLedger,refresh
from hub import connection_sources
from hub.design_store import DesignStore
bs,validators=load_bundles(R,list(DEFAULT_BUNDLES));b=bs[-1];assert current_authority_status(R,b)['state']=='matched';v=connection_sources.SourceResolver(R,b['manifest'],b['adapters'],b['source_plan']);ledger=RefreshLedger(R,result_validator=result_validator(validators));before=ledger.history(current_authority=v.authority);ids=[p['project_id'] for p in b['manifest']['entries']];assert len(ids)==len(set(ids))==24
source_refresh=v.refresh;attempts=[];blocked_probes=[];writes=[];enforce=True
# Audit file opens during the accepted runtime path; no external writable opens permitted.
def audit(event,args):
 if not enforce:return
 if event=='subprocess.Popen':raise AssertionError('business process launch')
 if event=='open':
  path,mode,flags=args
  if isinstance(path,(str,bytes)) and isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
   p=Path(os.fsdecode(path)).absolute();writes.append(str(p));assert p.is_relative_to(R),('external write',str(p))
sys.addaudithook(audit)
def forbidden(*a,**kw):blocked_probes.append('attempt');raise AssertionError('Manga path probe')
def resolve(pid):
 attempts.append(pid)
 if pid=='manga-localizer':
  with contextlib.ExitStack() as stack:
   for name in ['expanduser','resolve','exists','is_file','is_dir','stat','lstat','open','read_bytes','read_text']:
    stack.enter_context(mock.patch.object(Path,name,side_effect=forbidden))
   return source_refresh(pid)
 return source_refresh(pid)
v.refresh=resolve
out=refresh(ledger,v,'tc7-real-all-20260905',ids,expected_head=before['head'])
after=ledger.history(current_authority=v.authority);projection=ledger.rebuild(current_authority=v.authority)
assert len(attempts)==24 and not blocked_probes and not out['resolver_errors']
for key in ['requests','events','results']:assert after[key][:len(before[key])]==before[key],key
with mock.patch.object(v,'refresh',side_effect=AssertionError('idempotent replay performed a source read')):
 retry=refresh(ledger,v,'tc7-real-all-20260905',ids,expected_head=before['head'])
assert ledger.history(current_authority=v.authority)==after
assert ledger.rebuild(current_authority=v.authority)==projection
store=DesignStore(R,'data/design_governance/design-store.json');assert not store.path.exists();initial=store.initialize();assert initial==store.read();assert initial['store_classification']=='real' and initial['facts']==initial['events']==[]
assert DesignStore(R,'data/design_governance/design-store.json').read()==initial
assert store.initialize()==initial
rows=[row for row in after['results'] if row['request_id']=='tc7-real-all-20260905'];assert len(rows)==24
counts=Counter(row['result']['disposition'] for row in rows)
enforce=False
packet={'schema_version':'1.0','kind':'TC7_real_operational_readiness','request_id':'tc7-real-all-20260905','active_authority':v.authority,'head_before':before['head'],'head_after':after['head'],'all_24_ids':ids,'source_dispositions':dict(counts),'refresh_results':rows,'history_prefix_preserved':True,'prior_rows':{k:len(before[k]) for k in ['requests','events','results']},'replay_added_rows':0,'replay_source_reads':0,'offline_rebuild_equal':True,'manga_path_calls':len(blocked_probes),'external_writable_opens':0,'business_process_launches':0,'writable_open_paths':sorted(set(writes)),'real_decisions':0,'design_store':{'path':'data/design_governance/design-store.json','sha256':hashlib.sha256(store.path.read_bytes()).hexdigest(),'classification':'real','revision':initial['revision'],'facts':0,'events':0,'requests':len(initial['requests']),'reopen_equal':True,'initialize_retry_equal':True},'final_connection_acceptance':False,'ui_acceptance':False,'figma_calls':0,'resolver_errors':out['resolver_errors']}
(U/'runtime-evidence.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:packet[k] for k in ['head_before','head_after','source_dispositions','history_prefix_preserved','manga_path_calls','external_writable_opens','design_store','resolver_errors']}))
