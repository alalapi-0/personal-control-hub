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
