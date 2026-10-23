"""Disposable persisted-overlay confirmation, without HTTP or actual project data."""
import copy,json,unittest
from pathlib import Path
import yaml
from unittest import mock
from connection_fixtures import Fixture
from hub.management_store import ManagementStore
from hub.project_service import ProjectService
from hub.service_contract import ServiceError
from test_hub_management_store import overlay
from test_hub_graph_relations import relation

class PreciseSaveTests(unittest.TestCase):
 def setUp(self):
  self.f=Fixture();self.addCleanup(self.f.close)
  for ident in ('a','b','c'):self.f.add(ident)
  self.root=self.f.hub;self.path=self.root/'data/connections/management_config.yaml';self.path.parent.mkdir(parents=True)
  base=yaml.safe_load((Path(__file__).parents[1]/'data/connections/management_config.yaml').read_text());base.update(schema_version='1.0',projects={});base.pop('revision',None);base.pop('history',None);self.path.write_text(yaml.safe_dump(base,allow_unicode=True,sort_keys=False))
  self.store=ManagementStore(self.root);self.service=ProjectService(self.root);self.projection=self.path.with_name('projection.json')
  self.proposal=dict(id='a-b',project_ids=['a','b'],kind='pipeline',status='proposed',shared_tasks=['Synthetic shared handoff'],differences=['Separate ownership'],not_shared=['Permissions'],inventory_index=0,evidence_refs=['fixture:evidence'])
  self.projection.write_text(json.dumps({'projects':[dict(project_id=i,status='proposed',reason=None,relations=[self.proposal],program_link_authority=False,design_selection_authority=False)for i in ('a','b')]}));self.service.relations_path=self.projection
 def command(self,value,request='save'):
  s=self.store.snapshot();return dict(request_id=request,expected_revision=s['revision'],expected_hash=s['config_hash'],project_id='a',operation='set',value=copy.deepcopy(value))
 def seed(self,value):self.service.save_management(self.command(value),authorize=lambda:None)
 def precise(self,request='confirm'):
  value=copy.deepcopy(self.store.snapshot()['projects']['a']);value['relations']=copy.deepcopy(value['relations'])if value['relations']is not None else dict(belongs_to=None,depends_on=[],related_to=[],independent=None)
  value['relations']['related_to']=list(dict.fromkeys(value['relations']['related_to']+['b']))
  c=self.command(value,request);c['proposal_ref']=self.service.get_management('a')['proposals'][0]['proposal_ref'];return c
 def test_legacy_temporal_and_typed_preserve_exact_unrelated_fields(self):
  for index,kind in enumerate(('legacy','temporal','typed')):
   with self.subTest(kind=kind):
    value=overlay(priority=4,relations=dict(belongs_to=None,depends_on=['c'],related_to=['c'],independent=True))
    if kind=='temporal':value.update(timezone='Asia/Tokyo',milestones=[dict(id='release',label='Synthetic release',date=None)])
    if kind=='typed':
     item=relation();item['from']['project_id']=item['to']['project_id']='a';value['relations']['typed']=dict(schema_version='1.0',items=[item])
    self.service.save_management(self.command(value,'seed-'+kind),authorize=lambda:None);command=self.precise('confirm-'+kind);before=copy.deepcopy(value);result=self.service.save_management(command,authorize=lambda:None)
    after=result['view']['overlay'];self.assertEqual({k:v for k,v in after.items()if k!='relations'},{k:v for k,v in before.items()if k!='relations'});self.assertEqual(after['relations'],{**before['relations'],'related_to':['c','b']});self.assertEqual(self.store.snapshot()['history'][-1]['before'],before)
 def test_collateral_fields_relations_and_optional_defaults_reject_without_write(self):
  self.seed(overlay(priority=4));original=self.precise();before=self.path.read_bytes()
  mutations=[lambda v:v.update(priority=9),lambda v:v.update(timezone=None,milestones=None),lambda v:v['relations'].update(depends_on=['c']),lambda v:v['relations'].update(independent=True),lambda v:v.update(layout={'x':1,'y':2})]
  for mutate in mutations:
   with self.subTest(mutate=mutate):
    c=copy.deepcopy(original);mutate(c['value'])
    with self.assertRaises(ServiceError)as error:self.service.save_management(c,authorize=lambda:None)
    self.assertEqual(error.exception.code,'MANAGEMENT_PROPOSAL_SCOPE_REJECTED');self.assertEqual(self.path.read_bytes(),before)
 def test_stale_revision_hash_and_proposal_identity_reject(self):
  self.seed(overlay());before=self.path.read_bytes();base=self.precise()
  for mutation in ('revision','hash','proposal'):
   c=copy.deepcopy(base)
   if mutation=='revision':c['expected_revision']-=1
   if mutation=='hash':c['expected_hash']='0'*64
   if mutation=='proposal':c['proposal_ref']['proposal_hash']='0'*64
   with self.subTest(mutation=mutation):
    with self.assertRaises(ServiceError):self.service.save_management(c,authorize=lambda:None)
    self.assertEqual(self.path.read_bytes(),before)
 def test_unknown_replay_exact_body_keeps_single_history_and_changed_body_conflicts(self):
  self.seed(overlay(priority=4));c=self.precise();apply=ManagementStore.apply
  def faulted(store,*args,**kwargs):
   store.fault=lambda phase:(_ for _ in ()).throw(OSError('synthetic lost receipt'))if phase=='after_replace'else None
   return apply(store,*args,**kwargs)
  with mock.patch.object(ManagementStore,'apply',autospec=True,side_effect=faulted):
   with self.assertRaises(ServiceError)as error:self.service.save_management(c,authorize=lambda:None)
  self.assertEqual(error.exception.outcome,'UNKNOWN');before=self.path.read_bytes();r=self.service.save_management(c,authorize=lambda:None);self.assertTrue(r['receipt']['replayed']);self.assertEqual(self.path.read_bytes(),before)
  changed=copy.deepcopy(c);changed['value']['priority']=8
  with self.assertRaises(ServiceError):self.service.save_management(changed,authorize=lambda:None)
  self.assertEqual(self.path.read_bytes(),before)
 def test_authorization_revocation_before_replace_preserves_bytes(self):
  self.seed(overlay());c=self.precise();before=self.path.read_bytes();calls=[]
  def authorize():
   calls.append(1)
   if len(calls)>1:raise ServiceError('OWNER_AUTH_REQUIRED',status=403)
  with self.assertRaises(ServiceError):self.service.save_management(c,authorize=authorize)
  self.assertEqual(self.path.read_bytes(),before)

 def write_proposal(self,members):
  proposal={**self.proposal,'project_ids':members}
  self.projection.write_text(json.dumps({'projects':[dict(project_id=i,status='proposed',reason=None,relations=[proposal],program_link_authority=False,design_selection_authority=False)for i in members]}))
 def test_exact_selected_targets_reject_supersets_and_wrong_members(self):
  self.seed(overlay());base=self.precise();before=self.path.read_bytes()
  for targets in (['b','c'],['c','b'],['c'],['b','unregistered']):
   with self.subTest(targets=targets):
    command=copy.deepcopy(base);command['value']['relations']['related_to']=targets
    with self.assertRaises(ServiceError):self.service.save_management(command,authorize=lambda:None)
    self.assertEqual(self.path.read_bytes(),before)
 def test_existing_target_order_cannot_be_removed_or_reordered(self):
  self.seed(overlay(relations=dict(belongs_to=None,depends_on=[],related_to=['c','b'],independent=None)))
  base=self.precise();before=self.path.read_bytes()
  for targets in (['b'],['b','c'],['c','b','b']):
   with self.subTest(targets=targets):
    c=copy.deepcopy(base);c['value']['relations']['related_to']=targets
    with self.assertRaises(ServiceError):self.service.save_management(c,authorize=lambda:None)
    self.assertEqual(self.path.read_bytes(),before)
  result=self.service.save_management(base,authorize=lambda:None)
  self.assertEqual(result['view']['overlay'],base['value'])
 def test_unconfigured_multi_member_and_already_related_are_exact(self):
  from hub.management_store import OVERLAY_FIELDS
  # No persisted overlay: retain the legacy raw shape without optional defaults.
  c=self.command(dict.fromkeys(OVERLAY_FIELDS),'unconfigured')
  c['value']['relations']=dict(belongs_to=None,depends_on=[],related_to=['b'],independent=None)
  c['proposal_ref']=self.service.get_management('a')['proposals'][0]['proposal_ref']
  r=self.service.save_management(c,authorize=lambda:None);self.assertEqual(r['view']['overlay'],c['value'])
  self.write_proposal(['a','c','b'])
  value=copy.deepcopy(r['view']['overlay']);value['relations']['related_to']=['b','c']
  c=self.command(value,'multi');c['proposal_ref']=self.service.get_management('a')['proposals'][0]['proposal_ref']
  self.assertEqual(self.service.save_management(c,authorize=lambda:None)['view']['overlay'],value)
  c=self.command(value,'already-related');c['proposal_ref']=self.service.get_management('a')['proposals'][0]['proposal_ref']
  self.assertEqual(self.service.save_management(c,authorize=lambda:None)['view']['overlay'],value)
 def test_unknown_exact_replay_survives_new_overlay_and_missing_proposal(self):
  self.seed(overlay(priority=4));c=self.precise();apply=ManagementStore.apply
  def faulted(store,*args,**kwargs):
   store.fault=lambda phase:(_ for _ in ()).throw(OSError('synthetic lost receipt'))if phase=='after_replace'else None
   return apply(store,*args,**kwargs)
  with mock.patch.object(ManagementStore,'apply',autospec=True,side_effect=faulted):
   with self.assertRaises(ServiceError)as error:self.service.save_management(c,authorize=lambda:None)
  self.assertEqual(error.exception.outcome,'UNKNOWN')
  original=self.store.snapshot()['history'][-1]
  current=copy.deepcopy(c['value']);current['priority']=9
  self.service.save_management(self.command(current,'later-edit'),authorize=lambda:None)
  self.projection.write_text(json.dumps({'projects':[]}));before=self.path.read_bytes()
  result=self.service.save_management(c,authorize=lambda:None)
  self.assertTrue(result['receipt']['replayed']);self.assertEqual(result['receipt']['committed_revision'],original['revision'])
  self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.store.snapshot()['history'][-2],original)
  changed=copy.deepcopy(c);changed['value']['relations']['related_to'].append('c')
  with self.assertRaises(ServiceError)as error:self.service.save_management(changed,authorize=lambda:None)
  self.assertEqual(error.exception.code,'MANAGEMENT_REQUEST_CONFLICT');self.assertEqual(self.path.read_bytes(),before)
 def test_source_or_selected_members_change_at_final_authorization_rejects(self):
  self.seed(overlay())
  for mode in ('members','body','missing'):
   with self.subTest(mode=mode):
    self.write_proposal(['a','b']);c=self.precise('revoked-'+mode);before=self.path.read_bytes();calls=[]
    def authorize():
     calls.append(1)
     if len(calls)==2:
      if mode=='members':self.write_proposal(['a','b','c'])
      elif mode=='missing':self.projection.write_text(json.dumps({'projects':[]}))
      else:
       value=json.loads(self.projection.read_text());value['projects'][0]['relations'][0]['shared_tasks']=['Changed source'];self.projection.write_text(json.dumps(value))
    with self.assertRaises(ServiceError)as error:self.service.save_management(c,authorize=authorize)
    self.assertEqual(error.exception.code,'MANAGEMENT_PROPOSAL_STALE');self.assertEqual(self.path.read_bytes(),before)
    self.assertFalse(list(self.path.parent.glob('.management-*')))
 def test_proposal_postcommit_view_failure_retains_successful_receipt(self):
  self.seed(overlay());c=self.precise()
  with mock.patch.object(self.service,'get_management',side_effect=ServiceError('MANAGEMENT_CONFIG_REJECTED',status=503)):
   result=self.service.save_management(c,authorize=lambda:None)
  self.assertIsNone(result['view']);self.assertEqual(result['view_error']['code'],'MANAGEMENT_CONFIG_REJECTED')
  self.assertEqual(self.store.snapshot()['projects']['a'],c['value'])
