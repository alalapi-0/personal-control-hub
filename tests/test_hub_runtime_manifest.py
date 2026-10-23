"""Synthetic installed packages and disposable DTO/worker observations only."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import unittest
from unittest import mock
from hub.systemd_refresh import runtime_manifest,compare_runtime_manifests,public_runtime_manifest
from hub.rr_payload import canonical_json
from hub.project_service import ProjectService
from hub.graph_service import project_graph
from test_hub_graph_service import dto

class RuntimeManifestTests(unittest.TestCase):
 def setUp(self):
  # The real program verifier correctly refuses a world-writable ancestor.
  # Reuse the refresh fixture policy without weakening that production guard.
  import stat
  from hub.rr_cli import _directories
  from hub.rr_payload import RRFeedbackError
  parent=Path(tempfile.gettempdir())
  try:
   _directories(parent);info=parent.lstat()
   private=info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700
  except (OSError,RRFeedbackError):private=False
  if not private:
   parent=Path(__file__).resolve().parents[1];_directories(parent)
   self.assertEqual(parent.lstat().st_uid,os.getuid())
  self.temp=tempfile.TemporaryDirectory(prefix='.pch-runtime-versions-',dir=parent);self.addCleanup(self.temp.cleanup)
  _directories(Path(self.temp.name))
  self.assertEqual(Path(self.temp.name).lstat().st_uid,os.getuid())
  self.assertEqual(stat.S_IMODE(Path(self.temp.name).lstat().st_mode),0o700)
  self.root=Path(self.temp.name);self.files={}
  for name in ('src/hub/systemd_refresh.py','src/hub/task_worker.py','scripts/hub_server.py','scripts/hub_sync.py','src/hub/connection_cli.py','schemas/rebuild/task-package.schema.json','schemas/rebuild/receipt.schema.json'):
   p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);raw=b'synthetic package bytes\n';p.write_bytes(raw);p.chmod(0o600);self.files[name]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
  self.program=hashlib.sha256(canonical_json(self.files)).hexdigest();self.source={'commit':'a'*40,'dirty':True}
  self.write_manifest()
  self.metadata={'build_id':'b'*64,'dirty_inputs':{'worker':'c'*64},'config_versions':{'server':'d'*64},
   'interfaces':{'project_dto':{'version':'1.0','compatible_versions':['1.0']}},
   'review_packages':{'review-a':{'version':'review-1','sha256':'e'*64,'inputs_sha256':'f'*64}},
   'artifacts':{'artifact-a':{'version':'candidate-1','sha256':'a'*64,'inputs_sha256':'b'*64}}}
 def write_manifest(self):
  p=self.root/'rr-runtime-manifest.json';p.write_bytes(canonical_json({'schema':1,'program_id':self.program,'files':self.files,'source':self.source}));p.chmod(0o600)
 def observe(self,role='web',**kw):
  with mock.patch('hub.systemd_refresh.__file__',str(self.root/'src/hub/systemd_refresh.py')):
   return runtime_manifest(role,**kw)
 def test_actual_existing_verifier_preserves_source_dirty_hashes_and_exact_self_role(self):
  before=copy.deepcopy(self.metadata);v=self.observe(metadata=self.metadata)
  self.assertEqual(v['source']['commit'],self.source['commit']);self.assertIs(v['source']['dirty'],True)
  self.assertEqual(v['source']['dirty_inputs'],self.metadata['dirty_inputs']);self.assertEqual(v['program']['program_id'],self.program)
  self.assertEqual(v['roles']['web']['pid'],os.getpid());self.assertEqual(v['roles']['web']['start_ticks'],v['observer']['start_ticks'])
  self.assertEqual(v['roles']['worker']['state'],'not_observed');self.assertIsNone(v['roles']['refresh']['pid'])
  self.assertEqual(v['schemas']['task_package']['sha256'],self.files['schemas/rebuild/task-package.schema.json']['sha256'])
  self.assertFalse(v['adoption_proven']);self.assertFalse(v['execution_admitted']);self.assertEqual(self.metadata,before)
  self.assertEqual(public_runtime_manifest(v),v);self.assertNotIn(str(self.root),json.dumps(v))
 def test_each_role_observes_only_own_process_and_foreign_identity_is_not_borrowed(self):
  for role in ('web','refresh','worker'):
   v=self.observe(role);self.assertEqual(v['roles'][role]['state'],'observed')
   self.assertTrue(all(r['pid']is None for name,r in v['roles'].items()if name!=role))
  v=self.observe('worker',process_identity={'pid':os.getpid()+1,'start_ticks':'1'})
  self.assertEqual(v['roles']['worker']['state'],'not_observed');self.assertEqual(v['observer']['pid'],os.getpid())
 def test_uninstalled_tampered_missing_linked_manifest_and_unmanifested_role_are_unknown(self):
  original=(self.root/'rr-runtime-manifest.json').read_bytes();p=self.root/'scripts/hub_server.py';p.write_bytes(b'tamper')
  self.assertEqual(self.observe()['program']['state'],'unknown');p.write_bytes(b'synthetic package bytes\n')
  manifest=self.root/'rr-runtime-manifest.json';manifest.unlink();self.assertEqual(self.observe()['source']['commit'],None)
  other=self.root/'private-copy';other.write_bytes(original);other.chmod(0o600);manifest.symlink_to(other)
  self.assertEqual(self.observe()['program']['state'],'unknown');manifest.unlink();self.write_manifest()
  self.assertEqual(self.observe(expected_program_id='f'*64)['program']['state'],'unknown')
  del self.files['scripts/hub_server.py'];self.program=hashlib.sha256(canonical_json(self.files)).hexdigest();self.write_manifest()
  self.assertEqual(self.observe()['program']['state'],'unknown')
 def test_missing_build_config_review_artifacts_and_dirty_inputs_are_explicit_unknown(self):
  v=self.observe();self.assertIsNone(v['program']['build_id']);self.assertIsNone(v['source']['dirty_inputs'])
  self.assertIn('Dirty input',v['source']['unknown_reason']);self.assertIn('review_packages',v['unknown_reasons'])
  self.assertIn('artifacts',v['unknown_reasons']);self.assertIn('not declared',v['schemas']['receipt']['unknown_reason'])
  self.assertEqual(compare_runtime_manifests(v,v)['status'],'unknown')
 def test_compatible_commit_program_dirty_differences_only_require_check(self):
  a=self.observe(metadata=self.metadata);b=copy.deepcopy(a)
  self.assertEqual(compare_runtime_manifests(a,b)['status'],'match')
  b['source']['commit']='b'*40;b['program']['program_id']='c'*64;b['source']['dirty_inputs']={'worker':'d'*64}
  self.assertEqual(compare_runtime_manifests(a,b)['status'],'check_required')
  b['interfaces']['project_dto']={'version':'2.0','compatible_versions':['1.0','2.0']}
  self.assertNotEqual(compare_runtime_manifests(a,b)['status'],'incompatible')
 def test_explicit_interface_mismatch_only_is_incompatible_schema_hash_change_is_not(self):
  a=self.observe(metadata=self.metadata);b=copy.deepcopy(a);b['schemas']['receipt']['sha256']='f'*64
  self.assertEqual(compare_runtime_manifests(a,b)['status'],'check_required')
  b['interfaces']['project_dto']={'version':'2.0','compatible_versions':['2.0']}
  self.assertEqual(compare_runtime_manifests(a,b)['status'],'incompatible')
 def test_review_artifact_input_applicability_mismatches_require_check_without_fake_failure(self):
  a=self.observe(metadata=self.metadata)
  for field in ('review_packages','artifacts'):
   b=copy.deepcopy(a);next(iter(b[field].values()))['inputs_sha256']='0'*64
   self.assertEqual(compare_runtime_manifests(a,b)['status'],'check_required')
 def test_bounded_metadata_and_no_secret_environment_or_unlisted_file_reads(self):
  from hub.rr_cli import _regular
  reads=[]
  def read(path,maximum,**kw):reads.append(Path(path).relative_to(self.root).as_posix());return _regular(path,maximum,**kw)
  (self.root/'unlisted-secret').write_text('SYNTHETIC_SECRET_DO_NOT_READ')
  with mock.patch('hub.rr_cli._regular',side_effect=read),mock.patch.dict(os.environ,{'SYNTHETIC_ACCOUNT':'SYNTHETIC_SECRET_DO_NOT_READ'}):
   v=self.observe(metadata=self.metadata)
  self.assertNotIn('unlisted-secret',reads);self.assertNotIn('SYNTHETIC_SECRET',json.dumps(v));self.assertTrue(set(reads)<=set(self.files)|{'rr-runtime-manifest.json'})
  for changes in ({'body':'raw provider message'},{'build_id':'/data/private/build'},{'dirty_inputs':{'/data/private/input':'a'*64}},{'review_packages':{'a':{'version':'x','sha256':'a'*64,'inputs_sha256':'/data/private/inputs'}}}):
   with self.subTest(changes=changes),self.assertRaises(ValueError):self.observe(metadata={**self.metadata,**changes})
 def test_project_get_integration_keeps_graph_business_versions_and_denied_reads(self):
  value=dto();value['operational']={};before=copy.deepcopy(value);callback=mock.Mock(return_value=self.observe(metadata=self.metadata))
  service=ProjectService('/synthetic/hub',runtime_observer=callback)
  with mock.patch.object(service,'_all',return_value=([value],{})):
   result=service.get_project('business')
   self.assertIn('runtime_manifest',result['operational']);self.assertEqual(result['provenance'],before['provenance'])
   self.assertEqual(project_graph(result)['source_version'],project_graph(before)['source_version']);self.assertEqual(project_graph(result)['projection_version'],project_graph(before)['projection_version'])
   self.assertEqual(value,before)
   for mode in ('denied','removed','no_current_goal_access'):
    row=copy.deepcopy(value)
    if mode=='denied':row['declared']['connection_read_allowed']=False
    elif mode=='removed':row['freshness']['local_presence']='removed_local'
    else:row['declared']['access_profile']=mode
    callback.reset_mock()
    with mock.patch.object(service,'_all',return_value=([row],{})):
     self.assertNotIn('runtime_manifest',service.get_project('business')['operational']);callback.assert_not_called()
 def test_miswired_internal_callback_is_redacted_not_raw_error_or_private_body(self):
  value=dto();value['operational']={};service=ProjectService('/synthetic/hub',runtime_observer=lambda:{'body':'SYNTHETIC_SECRET'})
  with mock.patch.object(service,'_all',return_value=([value],{})):
   result=service.get_project('business')['operational']['runtime_manifest']
  self.assertEqual(result['state'],'unknown');self.assertNotIn('SYNTHETIC_SECRET',json.dumps(result))
 def test_default_server_assembly_injects_real_web_observer_without_native_or_db(self):
  from test_hub_server_entry import ServerEntryTests
  f=ServerEntryTests();f.setUp();self.addCleanup(f.doCleanups)
  code,state=f.run_entry(['--root',str(f.data),'--port','0']);self.assertEqual(code,0)
  self.assertTrue(callable(state['projects'].runtime_observer));v=state['projects'].runtime_observer()
  self.assertEqual(v['roles']['web']['pid'],os.getpid());self.assertIsNone(v['roles']['worker']['pid']);self.assertEqual(list(f.data.iterdir()),[])
 def test_actual_worker_intent_observation_is_worker_not_refresh_or_web(self):
  from test_hub_normal_dispatch_budget import NormalDispatchBudgetTests
  import test_hub_ordinary_worker as ordinary_fixture
  from types import SimpleNamespace
  f=NormalDispatchBudgetTests();self.addCleanup(f.doCleanups)
  # Place this reused fixture under the same verified private parent too;
  # keep the actual storage/mount/native guards, not mocks of their outcomes.
  with mock.patch.object(ordinary_fixture,'tempfile',SimpleNamespace(TemporaryDirectory=lambda:tempfile.TemporaryDirectory(dir=self.temp.name))):f.setUp()
  f.admit();worker=f.worker();worker.run_one()
  event=next(e for e in f.store.events(f.f.spec['request_id'])if e['key'].startswith('dispatch-intent-'))
  v=event['data']['runtime_manifest'];self.assertEqual(v['roles']['worker']['pid'],worker.identity['pid'])
  self.assertEqual(v['roles']['worker']['start_ticks'],worker.identity['start_ticks']);self.assertIsNone(v['roles']['web']['pid']);self.assertEqual(f.calls,['open','start'])

 def test_list_detail_repeat_share_one_full_dto_snapshot_after_projection(self):
  value=dto();value['operational']={};before=copy.deepcopy(value)
  callback=mock.Mock(side_effect=lambda:self.observe(metadata=self.metadata))
  service=ProjectService('/synthetic/hub',runtime_observer=callback)
  with mock.patch.object(service,'_all',return_value=([value],{'ledger_head':{'sequence':1,'hash':'c'*64}})):
   listed=service.list_projects()['projects'][0];detail=service.get_project('business')
   self.assertEqual(listed,detail);observed=copy.deepcopy(detail['operational']['runtime_manifest'])
   for _ in range(3):
    self.assertEqual(service.list_projects()['projects'][0],detail)
    self.assertEqual(service.get_project('business')['operational']['runtime_manifest'],observed)
   callback.assert_called_once_with()
   self.assertEqual(project_graph(detail)['source_version'],project_graph(before)['source_version'])
   self.assertEqual(project_graph(detail)['projection_version'],project_graph(before)['projection_version'])
   self.assertEqual(detail['source'],before['source']);self.assertEqual(detail['provenance'],before['provenance'])
   listed['operational']['runtime_manifest']['source']['commit']='f'*40
   self.assertEqual(service.get_project('business')['operational']['runtime_manifest'],observed)
   self.assertEqual(value,before)

 def test_denied_detail_or_filtered_list_does_not_observe_other_eligible_rows(self):
  allowed=dto();allowed['operational']={};allowed['project_id']='allowed';allowed['name']='Allowed'
  for mode in ('denied','no_current_goal_access','removed','declared_removed'):
   denied=dto();denied['operational']={}
   if mode=='denied':denied['declared']['connection_read_allowed']=False
   elif mode=='no_current_goal_access':denied['declared']['access_profile']=mode
   elif mode=='declared_removed':denied['declared']['current_state_status']='removed_local'
   else:denied['freshness']['local_presence']='removed_local'
   callback=mock.Mock(side_effect=AssertionError('denied row must not observe'))
   service=ProjectService('/synthetic/hub',runtime_observer=callback)
   with self.subTest(mode=mode),mock.patch.object(service,'_all',return_value=([denied,allowed],{'ledger_head':None})):
    self.assertNotIn('runtime_manifest',service.get_project('business')['operational'])
    listed=service.list_projects({'q':'business'})['projects']
    self.assertEqual(len(listed),1);self.assertNotIn('runtime_manifest',listed[0]['operational']);callback.assert_not_called()

 def test_callback_replacement_and_cached_failure_do_not_inherit_old_observation(self):
  value=dto();value['operational']={};bad=mock.Mock(return_value={'body':'SYNTHETIC_PRIVATE_ERROR'})
  service=ProjectService('/synthetic/hub',runtime_observer=bad)
  with mock.patch.object(service,'_all',return_value=([value],{'ledger_head':None})):
   failed=service.get_project('business');self.assertEqual(failed,service.list_projects()['projects'][0]);bad.assert_called_once_with()
   self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(failed))
   good=mock.Mock(return_value=self.observe(metadata=self.metadata));service.runtime_observer=good
   fresh=service.list_projects()['projects'][0];self.assertEqual(fresh,service.get_project('business'));good.assert_called_once_with()
   self.assertNotEqual(fresh['operational']['runtime_manifest'],failed['operational']['runtime_manifest'])
   service.runtime_observer=None;self.assertNotIn('runtime_manifest',service.get_project('business')['operational'])
   legacy_rows=[{'project_id':'legacy'}];self.assertIs(service._runtime_rows(legacy_rows),legacy_rows)
   service.runtime_observer=good;service.get_project('business');self.assertEqual(good.call_count,2)
   service.runtime_observer=mock.Mock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_ERROR'))
   self.assertEqual(service.get_project('business')['operational']['runtime_manifest']['state'],'unknown')
   self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(service.get_project('business')))

 def test_concurrent_eligible_reads_capture_exactly_one_snapshot(self):
  import threading
  value=dto();value['operational']={};callback=mock.Mock(side_effect=lambda:self.observe(metadata=self.metadata))
  service=ProjectService('/synthetic/hub',runtime_observer=callback);barrier=threading.Barrier(4);results=[];errors=[]
  def read():
   try:barrier.wait(timeout=5);results.append(service.get_project('business'))
   except Exception as error:errors.append(type(error).__name__)
  with mock.patch.object(service,'_all',return_value=([value],{'ledger_head':None})):
   threads=[threading.Thread(target=read)for _ in range(4)]
   for thread in threads:thread.start()
   for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
  self.assertEqual(errors,[]);self.assertEqual(len(results),4);self.assertTrue(all(row==results[0]for row in results));callback.assert_called_once_with()
