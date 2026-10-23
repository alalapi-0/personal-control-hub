"""Synthetic DTOs only: projection does not collect or resolve any source."""
import sys,copy,json,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.graph_service import project_graph,GraphService
from hub.project_service import ProjectService
from hub.graph_records import EXPLANATIONS
from hub.service_contract import ServiceError
import test_hub_business_tasks as task_fixtures


def dto():
 p=task_fixtures.plan();p['nodes'][-1]['dependencies']=['a']
 return {'project_id':'business','name':'Synthetic project','declared':{'connection_read_allowed':True,'access_profile':'read_only','current_state_status':'active'},
         'freshness':{'state':'fresh','authority_drift':False,'local_presence':'registered_local','observed_at':'2026-10-23T00:00:00Z','projected_at':'2026-10-23T01:00:00Z','fact_updated_at':None},
         'source':{'source_record':{'sources':[{'id':'state','sha256':'a'*64,'path':'/home/private/state.json'}],
                  'extensions':{'fact':{'task_plan':{'value':p,'proof':{'source_ref':'state','sha256':'a'*64}}}}}},
         'provenance':{'project_binding':{'registration_sha256':'b'*64},'schema_sha256':'c'*64,'adapter_version':'1','ledger_head':{'sequence':1,'hash':'d'*64},'registry_sha256':'e'*64},
         'semantics':{'explanations':{k:{'state':'known','value':k,'source':{'kind':'source_fact','source_ref':'state','path':'/home/private/state.json','sha256':'a'*64},'unknown_reason':None}for k in EXPLANATIONS},
          'business_state':{'state':'known','value':'active'},'collection_health':{'state':'success'},
          'progress':{'completed':4,'total':4,'unit':'local_steps','scope':'source_declared_units','project_completion_inferred':False},
          'availability':{'state':'unknown'},'interpretations':{'origin':'hub_authored_interpretation','fields':[]}},'design':{'available':False}}


class GraphServiceTests(unittest.TestCase):
 def test_real_project_facade_thin_injection_and_single_read_no_mutation(self):
  value=dto();before=copy.deepcopy(value);service=ProjectService('/synthetic/hub');service.get_project=mock.Mock(return_value=value)
  with mock.patch('hub.connection_sources.SourceResolver',side_effect=AssertionError('no source reader')):
   g=service.get_graph('business')
  service.get_project.assert_called_once_with('business',design_snapshot=None);self.assertEqual(value,before)
  self.assertEqual({o['type']for o in g['objects']},{'Project','Phase','Round','Task'});self.assertEqual(g['rows'][0]['object_id'],g['objects'][0]['id'])
  self.assertEqual({e['type']for e in g['relations']},{'part_of','depends_on'})
  self.assertIn('Goal',g['unknown_types']);self.assertNotIn('/home/private',json.dumps(g))
 def test_clock_global_ledger_audit_registry_do_not_change_project_version(self):
  a=dto();first=project_graph(a);a['freshness']['projected_at']='2026-10-23T02:00:00Z';a['provenance']['ledger_head']={'sequence':999,'hash':'f'*64};a['provenance']['registry_sha256']='f'*64
  second=project_graph(a);self.assertEqual(first['source_version'],second['source_version']);self.assertEqual(first['projection_version'],second['projection_version'])
  a['source']['source_record']['sources'][0]['sha256']='f'*64;self.assertNotEqual(first['source_version'],project_graph(a)['source_version'])
 def test_permission_removed_stale_unknown_do_not_expose_business_or_design(self):
  for mode in ('disabled','removed','stale','unknown','no_current_goal_access'):
   a=dto()
   if mode=='disabled':a['declared']['connection_read_allowed']=False
   elif mode=='removed':a['freshness']['local_presence']='removed_local'
   elif mode=='no_current_goal_access':a['declared']['access_profile']=mode
   else:a['freshness']['state']=mode
   a['source']['source_record']='must never inspect'
   a['semantics']={'secret':'never disclose'};a['design']={'secret':'never disclose'}
   with self.subTest(mode=mode):
    g=project_graph(a);self.assertEqual(len(g['objects']),1);self.assertNotIn('never disclose',json.dumps(g));self.assertEqual(g['objects'][0]['data']['business_state']['state'],'unknown')
 def test_progress_health_business_availability_and_five_explanations_separate(self):
  g=project_graph(dto());p=g['objects'][0];self.assertEqual(set(p['explanations']),EXPLANATIONS)
  self.assertEqual(p['data']['collection_health']['state'],'success');self.assertEqual(p['data']['business_state']['value'],'active');self.assertEqual(p['data']['availability']['state'],'unknown');self.assertFalse(p['data']['progress']['project_completion_inferred'])
 def test_unknown_timestamp_and_no_fabricated_goal_run_or_filename_identity(self):
  a=dto();a['source']['source_record']['extensions']={};g=project_graph(a)
  self.assertEqual(len(g['objects']),1);self.assertIn('Task',g['unknown_types']);self.assertIn('Run',g['unknown_types']);self.assertIsNone(g['objects'][0]['source']['fact_time'])
 def test_design_candidate_and_artifact_versions_remain_nonfacts(self):
  a=dto();a['design']={'available':True,'references':[{'kind':'artifact_ref','id':'output-a','sha256':h*64,'created_at':'2026-10-23T00:00:00Z','location':{'value':'/home/private/output'}}for h in('a','b')]}
  g=project_graph(a);art=[o for o in g['objects']if o['type']=='Artifact'];self.assertEqual(len(art),2);self.assertEqual(art[0]['id'],art[1]['id']);self.assertNotEqual(art[0]['revision'],art[1]['revision']);self.assertEqual({o['identity']for o in art},{'candidate'});self.assertNotIn('/home/private',json.dumps(g));self.assertEqual(art[0]['data']['artifact']['sha256']['value'],'a'*64);self.assertIsNone(art[0]['data']['artifact']['host']['value'])

 def test_wrong_project_from_backend_is_rejected(self):
  service=ProjectService('/synthetic/hub');service.get_project=mock.Mock(return_value=dto())
  with self.assertRaises(ServiceError):service.get_graph('foreign')

 def test_valid_maximum_source_ids_project_dependencies_and_keep_version_identity(self):
  ids=('a'*128,'b'*128);a=dto();p=task_fixtures.plan(ids);p['nodes'][-1]['dependencies']=[ids[0]]
  a['source']['source_record']['extensions']['fact']['task_plan']['value']=p
  before=copy.deepcopy(a);first=project_graph(a)
  dep=next(e for e in first['relations']if e['type']=='depends_on')
  self.assertEqual(dep['from']['id'],'business/Task/'+ids[1]);self.assertEqual(dep['to']['id'],'business/Task/'+ids[0])
  self.assertEqual(a,before)
  p2=task_fixtures.plan(ids,version='P2');p2['nodes'][-1]['dependencies']=[ids[0]];p2['labels'][ids[0]]='New title'
  a['source']['source_record']['extensions']['fact']['task_plan']['value']=p2
  newer=next(e for e in project_graph(a)['relations']if e['type']=='depends_on')
  self.assertEqual(dep['id'],newer['id']);self.assertNotEqual(dep['revision'],newer['revision'])

 def test_embedded_absolute_paths_are_unknown_in_explanations_and_metadata(self):
  paths=['/data/private/pch-original.md','/var/lib/private/state.json','/srv/private/fact','/media/disk/private.png',
         '/opt/private/model','/run/user/1000/private','/custom-mount/private/source','/文件/私有/状态.json','file:///data/private/state.json']
  for private in paths:
   a=dto();a['semantics']['explanations']['purpose']['value']='See '+private+' for details'
   a['semantics']['explanations']['actor']['source']['note']='Evidence at '+private
   a['semantics']['explanations']['outputs']['value']={'nested':['Explanation '+private]}
   a['semantics']['interpretations']['fields']=[{'text':'Interpretation '+private,'path':private}]
   a['semantics']['business_state']={'state':'known','value':'Status '+private}
   a['semantics']['availability']={'note':'Metadata '+private,'path':private}
   before=copy.deepcopy(a)
   with self.subTest(private=private):
    g=project_graph(a);encoded=json.dumps(g,ensure_ascii=False);self.assertNotIn(private,encoded)
    project=g['objects'][0]
    for name in ('purpose','actor','outputs'):
     self.assertEqual(project['explanations'][name]['state'],'unknown');self.assertIn('filesystem',project['explanations'][name]['unknown_reason'])
    self.assertEqual(project['data']['business_state']['state'],'unknown')
    self.assertIn('filesystem',project['data']['availability']['unknown_reason'])
    self.assertEqual(a,before);self.assertEqual(project['id'],'business/Project/business')

 def test_fractions_relative_references_and_https_documentation_remain_known(self):
  texts=['4/4','3/13','docs/schema.json','See docs/schema.json for details',
         'https://example.invalid/data/private/doc.md','See https://example.invalid/var/lib/docs?q=4/4 for details',
         '[Documentation](https://example.invalid/docs/schema.json)']
  for value in texts:
   a=dto();a['semantics']['explanations']['purpose']['value']=value
   with self.subTest(value=value):
    g=project_graph(a);item=g['objects'][0]['explanations']['purpose'];self.assertEqual(item['state'],'known');self.assertEqual(item['value'],value)
  a=dto();a['semantics']['explanations']['purpose']['value']='https://example.invalid/docs and /data/private/hidden.md'
  self.assertEqual(project_graph(a)['objects'][0]['explanations']['purpose']['state'],'unknown')
