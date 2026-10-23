"""Actual disposable loopback API; existing owner authentication is preserved."""
import unittest
from unittest import mock
import test_hub_local_service as http
from test_hub_graph_service import dto
from hub.project_service import ProjectService


class GraphAPITests(unittest.TestCase):
 call=http.LocalHTTPTests.call
 session=http.LocalHTTPTests.session
 stop=http.LocalHTTPTests.stop
 def setUp(self):
  http.LocalHTTPTests.setUp(self)
  self.service=ProjectService('/synthetic/hub');self.service.get_project=mock.Mock(return_value=dto());self.server.projects=self.service
 def test_owner_only_graph_route_same_projection_and_exact_query(self):
  status,_,body=self.call(path='/api/projects/business/graph');self.assertEqual(status,401);self.service.get_project.assert_not_called()
  self.session();status,_,body=self.call(path='/api/projects/business/graph');self.assertEqual(status,200,body)
  self.assertEqual(body['data']['rows'][0]['object_id'],body['data']['objects'][0]['id']);self.assertTrue(body['data']['read_only'])
  self.service.get_project.assert_called_once()
  self.assertEqual(self.call(path='/api/projects/business/graph?profile=all')[0],400)
 def test_legacy_detail_still_returns_identical_dto(self):
  self.session();status,_,body=self.call(path='/api/projects/business');self.assertEqual(status,200);self.assertEqual(body['data'],dto())

 def action_fixture(self):
  from test_hub_graph_actions import GraphActionTests
  f=GraphActionTests();f.setUp();self.addCleanup(f.doCleanups)
  self.server.projects=f.projects;self.server.designs=f.f.service
  from hub.action_registry import ActionRegistry
  self.server.actions=ActionRegistry(f.projects,f.f.service,host=self.server.origin,backend_instance=self.server.backend_instance)
  return f
 def test_registered_action_http_bytes_are_bound_safe_and_session_required(self):
  import json
  from urllib.parse import urlencode
  f=self.action_fixture();path='/api/projects/fixture-project/actions'
  self.assertEqual(self.call(path=path)[0],401);self.session()
  status,_,body=self.call(path=path);self.assertEqual(status,200,body)
  binding=next(a['binding']for e in body['data']['entries']for a in e['actions'])
  status,headers,data=self.call(path=path+'/artifact?'+urlencode({'binding':json.dumps(binding)}))
  self.assertEqual(status,200);self.assertEqual(data,f.f.png_data);self.assertEqual(headers['Content-Type'],'image/png');self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
  from hub.action_registry import request_id,digest
  command=dict(action_id='open_registered_artifact',binding=binding,request_id=request_id('open_registered_artifact',binding))
  self.assertEqual(headers['X-Hub-Read-Schema'],'local-read/1');self.assertRegex(headers['X-Hub-Read-Run-Id'],r'^local-read-[a-f0-9]{32}$')
  self.assertEqual(headers['X-Hub-Read-Request-Id'],command['request_id']);self.assertEqual(headers['X-Hub-Read-Parameter-SHA256'],digest(command))
  self.assertEqual(headers['X-Hub-Read-Input-SHA256'],binding['artifact_sha256']);self.assertEqual(headers['X-Hub-Read-Binding-SHA256'],binding['binding_sha256'])
  self.assertEqual(headers['X-Hub-Read-Bytes'],str(len(data)));self.assertEqual(headers['X-Hub-Read-Durable-Receipt'],'false')
  status,other,_=self.call(path=path+'/artifact?'+urlencode({'binding':json.dumps(binding)}));self.assertEqual(status,200)
  self.assertNotEqual(headers['X-Hub-Read-Run-Id'],other['X-Hub-Read-Run-Id'])

 def test_action_http_owner_csrf_exact_request_and_no_native_command(self):
  from hub.action_registry import request_id
  f=self.action_fixture();self.session();binding=self.server.actions.catalog('fixture-project')['entries']
  binding=next(a['binding']for e in binding for a in e['actions'])
  command=dict(action_id='verify_registered_artifact',binding=binding,request_id=request_id('verify_registered_artifact',binding))
  self.assertEqual(self.call('POST','/api/actions/verify',command,headers={'X-Hub-CSRF':'wrong'})[0],403)
  status,_,body=self.call('POST','/api/actions/verify',command);self.assertEqual(status,200,body);self.assertFalse(body['data']['external_effect'])
  for patch in ({'action_id':'native_program'},{'request_id':'changed'},{'shell':'sh'}):
   status,_,_=self.call('POST','/api/actions/verify',{**command,**patch});self.assertEqual(status,409 if 'request_id'in patch else 400)
 def test_action_http_changed_input_and_denied_project_never_returns_bytes(self):
  import json
  from urllib.parse import urlencode
  f=self.action_fixture();self.session();binding=f.binding();binding['host']=self.server.origin;binding['backend_instance']=self.server.backend_instance
  from hub.action_registry import digest
  binding['binding_sha256']=digest({k:v for k,v in binding.items()if k!='binding_sha256'})
  f.f.png_path.write_bytes(b'changed')
  path='/api/projects/fixture-project/actions/artifact?'+urlencode({'binding':json.dumps(binding)})
  self.assertEqual(self.call(path=path)[0],409)
  f.projects._management_context.return_value=(set(),[])
  self.assertEqual(self.call(path='/api/projects/fixture-project/actions')[0],403)
