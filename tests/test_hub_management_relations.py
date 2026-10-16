"""Owner-confirmed relations derive from one versioned management snapshot."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.project_service import ProjectService

class ManagementRelationsTests(unittest.TestCase):
    def test_cloud_root_kind_is_not_management_eligible_and_no_probe(self):
        with tempfile.TemporaryDirectory() as root:
            service=ProjectService(root)
            resolver=SimpleNamespace(projects={'a':{'name':'A','enabled':True},
                'cloud':{'name':'Cloud','enabled':True,'root_kind':'cloud'},
                'gone':{'name':'Gone','enabled':True,'current_state_status':'removed_local'}})
            eligible,options=service._management_context(resolver)
            self.assertEqual(eligible,{'a'})
            self.assertEqual(options,[{'id':'a','name':'A'}])

class ConfirmedRelationProjectionTests(unittest.TestCase):
    def setUp(self):
        from connection_fixtures import Fixture
        from hub.management_store import ManagementStore
        import yaml
        self.fixture=Fixture();self.addCleanup(self.fixture.close)
        for ident in ('a','b','c'):self.fixture.add(ident)
        self.fixture.projects[1]['name']='Project Bee';self.fixture.save_registry()
        root=self.fixture.hub
        config=root/'data/connections/management_config.yaml';config.parent.mkdir(parents=True)
        base=yaml.safe_load((Path(__file__).parents[1]/'data/connections/management_config.yaml').read_text())
        base.update(schema_version='1.0',projects={});base.pop('revision',None);base.pop('history',None)
        config.write_text(yaml.safe_dump(base,sort_keys=False))
        self.store=ManagementStore(root);self.service=ProjectService(root)
        self.eligible={'a','b','c'}
        self.options=[{'id':row['id'],'name':row['name']} for row in self.fixture.projects]

    def set_relations(self,ident,parent=None,depends=(),related=(),independent=None):
        state=self.store.snapshot()
        value=dict(priority=None,start_date=None,end_date=None,watch=None,order=None,layout=None,
            relations={'belongs_to':parent,'depends_on':list(depends),'related_to':list(related),'independent':independent})
        self.store.apply({'request_id':'edit-'+str(state['revision']+1),'expected_revision':state['revision'],
            'expected_hash':state['config_hash'],'project_id':ident,'operation':'set','value':value},
            project_ids=self.eligible,authorize=lambda:None)

    def view(self,ident,rows=None):
        return self.service._management_view(self.store.snapshot(),ident,self.eligible,self.options,project_rows=rows)['relation_view']

    def test_empty_unconfigured_and_owner_confirmed_are_distinct_with_directions(self):
        self.assertEqual(self.view('a')['state'],'unconfigured')
        self.set_relations('a')
        self.assertEqual(self.view('a')['state'],'explicit_empty')
        self.set_relations('b',parent='a',depends=['a'],related=['c'])
        self.assertEqual(self.view('b')['state'],'owner_confirmed')
        incoming=self.view('a')['incoming']
        self.assertEqual({edge['relation'] for edge in incoming},{'belongs_to','depends_on'})
        self.assertTrue(all(edge['direction']=='incoming' and edge['project_id']=='b' and edge['name']=='Project Bee' for edge in incoming))
        self.assertEqual(self.view('c')['incoming'][0]['relation'],'related_to')
        self.assertEqual(self.view('c')['state'],'unconfigured')
        self.set_relations('c',independent=False)
        self.assertEqual(self.view('c')['state'],'owner_confirmed')
        self.assertIs(self.view('c')['independent'],False)

    def test_transitive_impact_contains_path_and_source_version_preserves_business(self):
        import copy
        self.set_relations('a',depends=['b']);self.set_relations('b',depends=['c'])
        rows={ident:{'business':{'normalized_status':'blocked' if ident=='c' else 'active'},
            'freshness':{'state':'fresh'},'provenance':{'ledger_head':{'sequence':7,'hash':'a'*64},
                'latest_result_hash':'b'*64,'registry_sha256':'c'*64}} for ident in self.eligible}
        before=copy.deepcopy(rows)
        impact=self.view('a',rows)['dependency_impact']
        self.assertEqual(impact['classification'],'derived');self.assertEqual(impact['status'],'affected')
        self.assertEqual(impact['paths'][0]['path'],['a','b','c'])
        self.assertEqual(impact['paths'][0]['source_status'],'blocked')
        self.assertEqual(impact['paths'][0]['source_version']['ledger_head']['sequence'],7)
        self.assertEqual(rows,before)
        rows['c']['freshness']['state']='stale'
        self.assertEqual(self.view('a',rows)['dependency_impact']['status'],'unknown')

    def test_dependency_cycle_warns_and_projection_terminates(self):
        self.set_relations('a',depends=['b']);self.set_relations('b',depends=['a'])
        view=self.view('a',{})
        self.assertIn('PROJECT_DEPENDENCY_CYCLE',view['warnings'])
        self.assertFalse(view['dependency_impact']['truncated'])
        self.assertEqual(len(view['dependency_impact']['paths']),1)
        self.assertEqual(view['dependency_impact']['paths'][0]['path'],['a','b'])

    def test_all_rows_share_one_management_snapshot_without_source_reads_or_proposal_mix(self):
        from hub.management_store import ManagementStore
        from hub.connection_sources import SourceResolver
        self.set_relations('a',depends=['b'])
        state=self.store.snapshot()
        proposal={'project_id':'a','relations':[{'target':'c','source':'source_proposal'}]}
        original_relations=self.service._relations
        def source_relations(ids, *, with_identity=False):
            result=original_relations(ids);result['a']=proposal
            return (result,None) if with_identity else result
        with mock.patch.object(ManagementStore,'snapshot',return_value=state) as read,\
             mock.patch.object(SourceResolver,'refresh',side_effect=AssertionError('GET source scan')),\
             mock.patch.object(self.service,'_relations',side_effect=source_relations):
            rows,snapshot=self.service._all(None)
        read.assert_called_once_with()
        self.assertEqual(next(row for row in rows if row['project_id']=='a')['relations'],proposal)
        for row in rows:
            view=row['management']['relation_view']
            for key in ('revision','config_hash','source_sha256'):self.assertEqual(view[key],state[key])
        self.assertEqual(next(row for row in rows if row['project_id']=='a')['management']['relation_view']['outgoing'][0]['project_id'],'b')

    def test_unavailable_has_no_confirmed_edges_and_unknown_impact(self):
        state={'available':False,'reason':'MANAGEMENT_NOT_INITIALIZED','revision':None,
            'config_hash':None,'source_sha256':None,'projects':{}}
        view=self.service._management_view(state,'a',self.eligible,self.options,project_rows={})['relation_view']
        self.assertEqual(view['state'],'unavailable');self.assertEqual(view['outgoing'],[])
        self.assertEqual(view['dependency_impact']['status'],'unknown')

    def test_dependency_traversal_is_bounded_and_orphans_are_explicit(self):
        self.set_relations('a',depends=['b'])
        state=self.store.snapshot()
        view=self.service._management_view(state,'a',{'a'},self.options,project_rows={})['relation_view']
        self.assertIn('MANAGEMENT_ORPHAN_REFERENCE',view['warnings'])
        self.assertFalse(view['outgoing'][0]['eligible'])
        projects={}
        for number in range(270):
            projects['node'+str(number)]={'relations':{'belongs_to':None,
                'depends_on':['node'+str(number+1)] if number<269 else [],'related_to':[],'independent':None}}
        state={**state,'projects':projects}
        view=self.service._relation_view(state,'node0',set(projects),[],[],project_rows={})
        self.assertTrue(view['dependency_impact']['truncated'])
        self.assertEqual(len(view['dependency_impact']['paths']),255)
        self.assertEqual(view['dependency_impact']['status'],'unknown')

if __name__=='__main__':unittest.main()
