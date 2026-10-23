"""Socket-free actual DesignService bytes and authorized ProjectService facade."""
import copy,hashlib,json,unittest
from types import SimpleNamespace
from unittest import mock
import test_hub_design_service as design_fixtures
from test_hub_graph_service import dto
from hub.project_service import ProjectService
from hub.action_registry import ActionRegistry,request_id,validate_binding,digest,descriptor,validate_descriptor
from hub.service_contract import ServiceError

class GraphActionTests(unittest.TestCase):
 def setUp(self):
  self.f=design_fixtures.DesignServiceTests();self.f.setUp();self.addCleanup(self.f.tearDown)
  self.projects=ProjectService(self.f.root)
  self.projects._resolver=mock.Mock(return_value=SimpleNamespace(projects={'fixture-project':{}}))
  self.projects._management_context=mock.Mock(return_value=({'fixture-project'},[]))
  self.value=dto();self.value.update(project_id='fixture-project',name='Synthetic artifacts')
  self.value['source']['source_record']['extensions']={}
  self.projects.get_project=mock.Mock(side_effect=self.project)
  self.actions=ActionRegistry(self.projects,self.f.service,host='http://127.0.0.1:1234',backend_instance='a'*32)
 def project(self,project_id,design_snapshot=None):
  result=copy.deepcopy(self.value);result['design']=ProjectService._design_for(project_id,design_snapshot);return result
 def binding(self):
  return next(a['binding']for e in self.actions.catalog('fixture-project')['entries']for a in e['actions']if a['id']=='verify_registered_artifact')
 def test_exact_read_verify_and_revalidate_same_request(self):
  b=self.binding();before=self.f.store.path.read_bytes();calls=[]
  for _ in range(3):
   response=self.actions.read(b,authorize=lambda:calls.append(1));self.assertEqual(response.data,self.f.png_data)
  c=dict(action_id='verify_registered_artifact',binding=b,request_id=request_id('verify_registered_artifact',b))
  r=self.actions.verify(c,authorize=lambda:None);other=self.actions.verify(c,authorize=lambda:None)
  self.assertNotEqual(r['run_id'],other['run_id']);self.assertEqual(r['input_sha256'],hashlib.sha256(self.f.png_data).hexdigest())
  self.assertEqual(r['parameter_sha256'],digest(c));self.assertFalse(r['durable_receipt']);self.assertFalse(r['external_effect']);self.assertEqual(self.f.store.path.read_bytes(),before)
 def test_changed_host_instance_revision_hash_classification_or_candidate_reject(self):
  b=self.binding();before=self.f.store.path.read_bytes()
  for key,value in [('host','http://127.0.0.1:1235'),('backend_instance','b'*32),('object_revision','wrong'),('artifact_sha256','0'*64),('candidate_id','foreign'),('candidate_revision',999),('classification','real'),('project_id','foreign')]:
   with self.subTest(key=key):
    bad={**b,key:value};bad['binding_sha256']=digest({k:v for k,v in bad.items()if k!='binding_sha256'})
    with self.assertRaises(ServiceError):self.actions.read(bad,authorize=lambda:None)
    self.assertEqual(self.f.store.path.read_bytes(),before)
 def test_denied_removed_or_no_access_never_reads_artifact_or_design(self):
  for mode in ('registration','removed','no_current_goal_access'):
   with self.subTest(mode=mode):
    value=copy.deepcopy(self.value)
    if mode=='registration':self.projects._management_context.return_value=(set(),[])
    elif mode=='removed':self.value['freshness']['local_presence']='removed_local'
    else:self.value['declared']['access_profile']='no_current_goal_access'
    with mock.patch.object(self.f.service,'artifact',side_effect=AssertionError('no content read')):
     with self.assertRaises(ServiceError):self.actions.catalog('fixture-project')
    self.value=value;self.projects._management_context.return_value=({'fixture-project'},[])
 def test_changed_file_symlink_and_missing_file_fail_without_write(self):
  b=self.binding();before=self.f.store.path.read_bytes();data=self.f.png_path.read_bytes()
  for mode in ('changed','symlink','missing'):
   with self.subTest(mode=mode):
    self.f.png_path.unlink(missing_ok=True)
    if mode=='changed':self.f.png_path.write_bytes(b'changed')
    elif mode=='symlink':self.f.png_path.symlink_to(self.f.html_path)
    with self.assertRaises(ServiceError):self.actions.read(b,authorize=lambda:None)
    self.assertEqual(self.f.store.path.read_bytes(),before)
  self.f.png_path.write_bytes(data)
 def test_owner_source_and_instance_change_at_response_boundary_discard(self):
  for mode in ('owner','source','instance'):
   with self.subTest(mode=mode):
    b=self.binding();calls=[];before=copy.deepcopy(self.value);instance=self.actions.instance
    def authorize():
     calls.append(1)
     if len(calls)==2:
      if mode=='owner':raise ServiceError('OWNER_AUTH_REQUIRED',status=401)
      if mode=='source':self.value['provenance']['project_binding']['registration_sha256']='f'*64
      if mode=='instance':self.actions.instance='c'*32
    with self.assertRaises(ServiceError):self.actions.read(b,authorize=authorize)
    self.value=before;self.actions.instance=instance
 def test_closed_request_no_shell_task_or_native_execution(self):
  b=self.binding();c=dict(action_id='verify_registered_artifact',binding=b,request_id=request_id('verify_registered_artifact',b))
  for mutation in ({'action_id':'native_program'},{'url':'file:///private'},{'request_id':'reuse'},{'command':'sh'}):
   with self.subTest(mutation=mutation):
    with self.assertRaises(ServiceError):self.actions.verify({**c,**mutation},authorize=lambda:None)
  catalog=self.actions.catalog('fixture-project');actions=next(e['actions']for e in catalog['entries']if e['actions']);self.assertFalse(next(a for a in actions if a['id']=='native_program')['available']);self.assertEqual(next(a for a in actions if a['id']=='prepare_ordinary_task')['handler'],'ordinary_task_form')
  self.assertFalse((self.f.root/'data/connections/tasks.sqlite3').exists());self.assertNotIn(str(self.f.root),json.dumps(catalog))
 def test_unknown_unbound_artifact_and_unrelated_global_observation(self):
  first=self.actions.catalog('fixture-project');b=self.binding();self.value['provenance']['ledger_head']['hash']='f'*64
  second=self.actions.catalog('fixture-project');self.assertEqual(first,second);self.assertEqual(self.binding(),b)
  self.assertTrue(first['entries']);self.assertEqual({e['object']['identity']for e in first['entries']},{'candidate'})
 def test_local_http_adapter_static_boundary_and_assets(self):
  from hub.local_service import WEB_ASSETS,HubRequestHandler
  self.assertIn('/assets/actions.mjs',WEB_ASSETS)
  # Transport is exercised by Root; importing and inspecting does not bind sockets.
  import inspect
  body=inspect.getsource(HubRequestHandler._handle)
  self.assertIn('require_owner=True',body);self.assertIn('self._boundary(mutation=True)',body)

 def test_closed_descriptors_and_versioned_input_schemas(self):
  import jsonschema
  b=self.binding()
  for ident in ('open_registered_artifact','verify_registered_artifact','prepare_ordinary_task','native_program'):
   d=descriptor(ident,b);self.assertEqual(validate_descriptor(d),d)
   self.assertEqual(d['object_type'],'Artifact');self.assertFalse(d['authorization']['execution_permission'])
   command={'binding':b}
   if ident=='verify_registered_artifact':command.update(action_id=ident,request_id=request_id(ident,b))
   jsonschema.validate(command,d['input_schema'])
   with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({**command,'shell':'sh'},d['input_schema'])
   for field,value in [('extra',True),('required_capabilities',['shell']),('effect_class','native_execution'),('host',{'origin':'foreign'}),('input_schema',{}),('idempotency',{'durable_receipt':True}),('reconcile',{'auto_retry':True})]:
    with self.subTest(action=ident,field=field):
     with self.assertRaises(ServiceError)as error:validate_descriptor({**d,field:value})
     self.assertEqual(error.exception.code,'ACTION_DESCRIPTOR_INVALID')
  d=descriptor('prepare_ordinary_task',b);self.assertEqual(d['effect_class'],'task_form_navigation');self.assertTrue(d['reconcile']['execution_deferred'])
 def test_each_read_receipt_correlates_without_double_read_or_durable_claim(self):
  b=self.binding();command=dict(action_id='open_registered_artifact',binding=b,request_id=request_id('open_registered_artifact',b))
  with mock.patch.object(self.f.service,'artifact',wraps=self.f.service.artifact)as read:
   first=self.actions.read_with_receipt(b,authorize=lambda:None);self.assertEqual(read.call_count,1)
   second=self.actions.read_with_receipt(b,authorize=lambda:None);self.assertEqual(read.call_count,2)
  self.assertEqual(first.response.data,self.f.png_data);self.assertNotEqual(first.receipt['run_id'],second.receipt['run_id'])
  for receipt in (first.receipt,second.receipt):
   self.assertEqual(receipt['parameter_sha256'],digest(command));self.assertEqual(receipt['request_id'],command['request_id'])
   self.assertEqual(receipt['input_sha256'],hashlib.sha256(first.response.data).hexdigest());self.assertEqual(receipt['binding_sha256'],b['binding_sha256'])
   self.assertEqual(receipt['bytes_read'],len(first.response.data));self.assertFalse(receipt['durable_receipt']);self.assertFalse(receipt['native_execution'])
  # The old direct read contract still returns ArtifactResponse, with one read.
  from hub.service_contract import ArtifactResponse
  self.assertIsInstance(self.actions.read(b,authorize=lambda:None),ArtifactResponse)
 def test_mismatched_bytes_or_hash_and_revocation_yield_no_read_receipt(self):
  from hub.service_contract import ArtifactResponse
  b=self.binding()
  for response in (ArtifactResponse(b'changed','image/png','test.png',sha256=b['artifact_sha256']),ArtifactResponse(self.f.png_data,'image/png','test.png',sha256='0'*64)):
   with mock.patch.object(self.f.service,'artifact',return_value=response):
    with self.assertRaises(ServiceError)as error:self.actions.read_with_receipt(b,authorize=lambda:None)
    self.assertEqual(error.exception.code,'ACTION_ARTIFACT_CHANGED')
  calls=[]
  def revoke():
   calls.append(1)
   if len(calls)==2:raise ServiceError('OWNER_AUTH_REQUIRED',status=401)
  with self.assertRaises(ServiceError)as error:self.actions.read_with_receipt(b,authorize=revoke)
  self.assertEqual(error.exception.code,'OWNER_AUTH_REQUIRED')

 def test_transport_serialization_preserves_safe_bytes_headers_and_local_receipt(self):
  from hub.local_service import HubRequestHandler
  from types import SimpleNamespace
  b=self.binding();result=self.actions.read_with_receipt(b,authorize=lambda:None);sent=[]
  handler=SimpleNamespace(_send=lambda *args,**kwargs:sent.append((args,kwargs)))
  HubRequestHandler._success(handler,result)
  self.assertEqual(len(sent),1);args,kwargs=sent[0];self.assertEqual(args[0],self.f.png_data)
  self.assertEqual(kwargs['content_type'],'image/png');headers=kwargs['headers']
  self.assertTrue(headers['Content-Disposition'].startswith('inline; filename="'))
  for header,key in [('Run-Id','run_id'),('Request-Id','request_id'),('Input-SHA256','input_sha256'),('Parameter-SHA256','parameter_sha256'),('Binding-SHA256','binding_sha256'),('Bytes','bytes_read')]:
   self.assertEqual(headers['X-Hub-Read-'+header],str(result.receipt[key]))
  self.assertEqual(headers['X-Hub-Read-Durable-Receipt'],'false')
