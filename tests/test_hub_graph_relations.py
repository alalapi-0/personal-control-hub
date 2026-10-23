"""Real management CAS plus authorized DTO graph projections; synthetic data only."""
import copy
import json
from pathlib import Path
import unittest
from unittest import mock
import yaml
from connection_fixtures import Fixture
from hub.connection_records import content_hash
from hub.management_store import ManagementStore, validate_management_state
from hub.project_service import ProjectService
from hub.graph_service import GraphService
from hub.graph_records import validate_graph, RELATIONS
from hub.service_contract import ServiceError
from test_hub_graph_service import dto
from test_hub_management_store import overlay


def endpoint(native='a', kind='Task', project='business', revision='P1'):
    return dict(project_id=project, type=kind, native_id=native, revision=revision)


def relation(ident='relation-a', kind='related_to', a=None, b=None):
    return dict(id=ident, revision=1, type=kind, state='candidate', origin='inferred',
                **{'from': a or endpoint(), 'to': b or endpoint('b')},
                evidence=[dict(ref='state:explicit-field', sha256='a'*64)],
                created_at='2026-10-23T00:00:00+00:00', run=None)


class GraphRelationsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=Fixture(); self.addCleanup(self.fixture.close)
        for ident in ('business','neighbor'): self.fixture.add(ident)
        self.root=self.fixture.hub
        config=self.root/'data/connections/management_config.yaml'; config.parent.mkdir(parents=True)
        base=yaml.safe_load((Path(__file__).parents[1]/'data/connections/management_config.yaml').read_text())
        base.update(schema_version='1.0', projects={}); base.pop('revision',None); base.pop('history',None)
        config.write_text(yaml.safe_dump(base,sort_keys=False))
        self.path=config; self.store=ManagementStore(self.root); self.service=ProjectService(self.root)

    def command(self, request, value, operation='set'):
        s=self.store.snapshot()
        return dict(request_id=request, project_id='business', expected_revision=s['revision'],
                    expected_hash=s['config_hash'], operation=operation, value=copy.deepcopy(value))

    def save(self, items, request='save', **fields):
        value=overlay(relations=dict(belongs_to=None,depends_on=[],related_to=[],independent=None,
                                    typed=dict(schema_version='1.0',items=items)),**fields)
        return self.service.save_management(self.command(request,value),authorize=lambda:None)

    def accept(self, ident='relation-a', request='accept'):
        row=next(i for i in self.store.snapshot()['projects']['business']['relations']['typed']['items'] if i['id']==ident)
        cmd=self.command(request,dict(id=ident,sha256=content_hash(row)),'accept_relation')
        self.service.save_management(cmd,authorize=lambda:None)
        return cmd

    def graph(self, value=None):
        value=copy.deepcopy(value or dto()); value['management']=self.service.get_management('business')
        before=copy.deepcopy(value)
        with mock.patch.object(self.service,'get_project',return_value=value), mock.patch('hub.connection_sources.SourceResolver.refresh',side_effect=AssertionError('no collection')):
            result=GraphService(self.service).get_project('business')
        self.assertEqual(value,before); validate_graph(result); return result

    def edges(self, graph):
        return [e for e in graph['relations'] if 'management' in e]

    def test_single_accept_uses_saved_candidate_not_other_client_drafts_and_preserves_facts(self):
        self.save([relation(),relation('other')],priority=3)
        before=self.store.snapshot(); source=self.graph(); self.accept()
        after=self.store.snapshot(); items=after['projects']['business']['relations']['typed']['items']
        self.assertEqual(items[0]['state'],'accepted'); self.assertEqual(items[1],before['projects']['business']['relations']['typed']['items'][1])
        self.assertEqual(after['projects']['business']['priority'],3)
        changed=self.graph(); self.assertEqual(changed['source_version'],source['source_version'])
        self.assertNotEqual(changed['projection_version'],source['projection_version'])
        self.assertEqual([e for e in changed['relations'] if e['source']['origin']=='source_fact'],[e for e in source['relations'] if e['source']['origin']=='source_fact'])
        self.assertTrue(all(e['identity']=='candidate' for e in self.edges(changed)))
        self.assertEqual(after['history'][:1],before['history']); self.assertEqual(after['history'][-1]['operation'],'accept_relation')
        bad=self.command('smuggle',dict(id='other',sha256=content_hash(items[1]),priority=99),'accept_relation')
        with self.assertRaises(ServiceError): self.service.save_management(bad,authorize=lambda:None)
        self.assertEqual(self.store.snapshot(),after)

    def test_exact_replay_conflict_and_stale_cas(self):
        self.save([relation()]); cmd=self.accept(); before=self.path.read_bytes()
        self.service.save_management(cmd,authorize=lambda:None); self.assertEqual(self.path.read_bytes(),before)
        bad=copy.deepcopy(cmd);bad['value']['sha256']='f'*64
        with self.assertRaises(ServiceError): self.service.save_management(bad,authorize=lambda:None)
        self.save([relation('next')],'new-draft')
        stale=copy.deepcopy(cmd);stale['request_id']='stale';stale['value']={'id':'next','sha256':content_hash(relation('next'))}
        with self.assertRaises(ServiceError) as error:self.service.save_management(stale,authorize=lambda:None)
        self.assertEqual(error.exception.code,'MANAGEMENT_CONFLICT')

    def test_all_types_project_and_versions_do_not_become_execution_or_source_facts(self):
        items=[relation('rel-'+kind,kind) for kind in sorted(RELATIONS)]
        self.save(items)
        g=self.graph();edges=self.edges(g);self.assertEqual({e['type']for e in edges},RELATIONS)
        self.assertTrue(all(e['management']['state']=='candidate' and e['run_id'] is None for e in edges))
        self.assertEqual(g['rows'],[dict(object_id=o['id'],revision=o['revision'],type=o['type'],label=o['label'],identity=o['identity'],source_version=o['source']['version'],projection_version=g['projection_version'])for o in g['objects']])

    def test_candidate_and_information_cycles_legal_accepted_task_cycles_reject_blocks_direction(self):
        # Existing fact says B depends on A; A blocks B has the same direction.
        self.save([relation('forward','blocks')]);self.accept('forward');self.graph()
        self.save([relation('one',a=endpoint(),b=endpoint('b')),relation('two',a=endpoint('b'),b=endpoint())],'info')
        self.accept('one','accept-one');self.accept('two','accept-two');self.graph()
        self.save([relation('dep-a','depends_on',endpoint(),endpoint('b')),relation('dep-b','depends_on',endpoint('b'),endpoint())],'deps')
        self.graph()  # Candidates do not enter the accepted dependency DAG.
        self.accept('dep-b','accept-b');before=self.path.read_bytes()
        with self.assertRaises(ServiceError):self.accept('dep-a','cycle')
        self.assertEqual(self.path.read_bytes(),before)

    def test_accept_cannot_be_forged_through_full_overlay(self):
        item=relation();item['state']='accepted'
        with self.assertRaises(ServiceError):self.save([item])
        self.assertEqual(self.store.snapshot()['revision'],0)

    def test_cross_project_is_registered_reference_only_or_explicit_gap(self):
        self.save([relation('ref','references',b=endpoint('neighbor','Project','neighbor',None))])
        g=self.graph();other=next(o for o in g['objects']if o['data'].get('reference_project_id')=='neighbor')
        self.assertEqual(other['data']['business_state']['state'],'unknown');self.assertIsNone(other['source']['version'])
        self.assertFalse(any(o['project_id']!='business' for o in g['objects']))
        self.save([relation('task-ref','references',b=endpoint('task','Task','neighbor'))],'unmapped')
        g=self.graph();self.assertEqual(self.edges(g),[]);self.assertIn('unavailable',g['objects'][0]['data']['relation_gaps'][0]['reason'])
        with self.assertRaises(ServiceError):self.save([relation('foreign','references',b=endpoint('unregistered','Project','unregistered',None))],'foreign')

    def test_private_evidence_and_unpublished_run_are_not_projected(self):
        item=relation();item['evidence'][0]['ref']='See /data/private/state.json';self.save([item])
        g=self.graph();self.assertEqual(self.edges(g),[]);self.assertNotIn('/data/private',json.dumps(g))
        item=relation('run');item['run']=endpoint('real-unavailable','Run');self.save([item],'run-ref')
        g=self.graph();self.assertEqual(self.edges(g),[]);self.assertIn('real published run',g['objects'][0]['data']['relation_gaps'][0]['reason'])

    def test_disabled_stale_removed_and_no_access_never_project_management(self):
        self.save([relation()])
        for mode in ('disabled','stale','removed','no_current_goal_access'):
            value=dto()
            if mode=='disabled':value['declared']['connection_read_allowed']=False
            elif mode=='removed':value['freshness']['local_presence']='removed_local'
            elif mode=='stale':value['freshness']['state']='stale'
            else:value['declared']['access_profile']=mode
            with self.subTest(mode=mode):self.assertEqual(self.edges(self.graph(value)),[])

    def test_unrelated_management_and_global_clock_do_not_change_project_source_or_projection(self):
        self.save([relation()]);first=self.graph()
        s=self.store.snapshot();cmd=dict(request_id='neighbor-edit',project_id='neighbor',expected_revision=s['revision'],expected_hash=s['config_hash'],operation='set',value=overlay(priority=9))
        self.service.save_management(cmd,authorize=lambda:None)
        value=dto();value['freshness']['projected_at']='2026-10-24T00:00:00Z';value['provenance']['ledger_head']={'sequence':1000}
        second=self.graph(value);self.assertEqual(first['source_version'],second['source_version']);self.assertEqual(first['projection_version'],second['projection_version'])

    def test_authorization_rechecked_and_unknown_commit_replays_without_second_confirmation(self):
        self.save([relation()]);row=relation();cmd=self.command('auth',dict(id=row['id'],sha256=content_hash(row)),'accept_relation');calls=[];before=self.path.read_bytes()
        def auth():
            calls.append(True)
            if len(calls)>1:raise ServiceError('OWNER_SESSION_EXPIRED',status=403)
        with self.assertRaises(ServiceError):self.store.apply(cmd,project_ids={'business','neighbor'},authorize=auth)
        self.assertEqual(self.path.read_bytes(),before)
        def fault(stage):
            if stage=='after_replace':raise OSError('synthetic interrupted durability check')
        store=ManagementStore(self.root,fault=fault)
        with self.assertRaises(ServiceError) as error:store.apply(cmd,project_ids={'business','neighbor'},authorize=lambda:None)
        self.assertEqual(error.exception.outcome,'UNKNOWN');self.store.apply(cmd,project_ids={'business','neighbor'},authorize=lambda:None)
        self.assertEqual(self.store.snapshot()['revision'],2)

    def test_legacy_relations_readable_with_explicit_project_dependency_gap(self):
        s=self.store.snapshot();value=overlay(relations=dict(belongs_to='neighbor',depends_on=['neighbor'],related_to=['neighbor'],independent=None))
        self.service.save_management(self.command('legacy',value),authorize=lambda:None)
        view=self.service.get_management('business');self.assertEqual({e['relation']for e in view['relation_view']['outgoing']},{'belongs_to','depends_on','related_to'})
        g=self.graph();self.assertEqual({e['type']for e in self.edges(g)},{'managed_by','related_to'});self.assertIn('management only',g['objects'][0]['data']['relation_gaps'][0]['reason'])

    def test_replayed_history_cannot_forge_acceptance_or_downgrade_typed_version(self):
        self.save([relation()]);original=yaml.safe_load(self.path.read_text())
        forged=copy.deepcopy(original)
        forged['projects']['business']['relations']['typed']['items'][0]['state']='accepted'
        forged['history'][0]['after']=copy.deepcopy(forged['projects']['business'])
        from hub.management_store import _request_hash
        row=forged['history'][0];row['request_hash']=_request_hash(row['request_id'],row['project_id'],row['operation'],row['after'])
        with self.assertRaises(ValueError):validate_management_state(forged)
        original['schema_version']='1.3'
        with self.assertRaises(ValueError):validate_management_state(original)

    def test_source_dependency_conflict_is_explicit_without_overwriting_facts(self):
        self.save([relation('reverse','depends_on',endpoint(),endpoint('b'))]);before=self.graph()
        self.accept('reverse');after=self.graph()
        self.assertEqual(self.edges(after),[])
        self.assertIn('conflicts',after['objects'][0]['data']['relation_gaps'][0]['reason'])
        self.assertEqual(before['source_version'],after['source_version'])
        self.assertEqual([e for e in before['relations']if e['identity']=='fact'],[e for e in after['relations']if e['identity']=='fact'])
        self.assertEqual(self.store.snapshot()['projects']['business']['relations']['typed']['items'][0]['state'],'accepted')

    def test_malformed_typed_fields_versions_and_duplicate_ids_refuse_without_write(self):
        bads=[]
        value=relation();value['extra']='not allowed';bads.append([value])
        value=relation();value['to']['extra']='not allowed';bads.append([value])
        value=relation();value['from']['project_id']='neighbor';bads.append([value])
        value=relation();value['evidence'][0]['sha256']='unknown';bads.append([value])
        bads.append([relation(),relation()])
        before=self.path.read_bytes()
        for items in bads:
            with self.subTest(items=items),self.assertRaises(ServiceError):self.save(items)
            self.assertEqual(self.path.read_bytes(),before)

    def test_logical_selector_provenance_survives_real_graph_projection_without_mutation(self):
        from hub.graph_service import explanations, safe
        cell={'state':'known','value':'已核对当前工作','source':{'source_ref':'state','sha256':'a'*64,
              'selector':{'path':['current_work','objective']}}}
        before=copy.deepcopy(cell)
        result=explanations({'current_work':cell})['current_work']
        self.assertEqual(result['state'],'known');self.assertEqual(result['source'],cell['source'])
        value=dto();value['semantics']['explanations']['current_work']=cell
        projected=self.graph(value)['objects'][0]['explanations']['current_work']
        self.assertEqual(projected,result);self.assertEqual(cell,before)
        self.assertEqual(safe({'selector':{'path':['rows',0,'objective']}}),{'selector':{'path':['rows',0,'objective']}})
        # A similarly named ordinary path or raw body never gets the exception.
        hidden=safe({'path':['current_work','objective'],'body':'raw message','messages':['private'],
                     'selector':{'path':['current_work','objective']}})
        self.assertEqual(hidden,{'selector':{'path':['current_work','objective']}})

    def test_private_or_malformed_selector_and_embedded_source_values_remain_unknown(self):
        from hub.graph_service import explanations
        selectors=[{'path':['/data/private/state.json']},{'path':['See /var/private/state.json']},
                   {'path':['docs/private.json']},{'path':['..','secret']},{'path':['current_work','bad\x00key']},
                   {'path':[]},{'path':['key']*21},{'path':['x'*161]},{'path':['key',True]},
                   {'path':['key',-1]},{'path':'/data/private/state.json'},
                   {'path':['current_work'],'extra':'unvalidated'}]
        for selector in selectors:
            cell={'state':'known','value':'verified','source':{'source_ref':'state','sha256':'a'*64,'selector':selector}}
            before=copy.deepcopy(cell)
            with self.subTest(selector=selector):
                result=explanations({'current_work':cell})['current_work']
                self.assertEqual(result['state'],'unknown');self.assertIsNone(result['source']);self.assertEqual(cell,before)
                self.assertNotIn('/data/private',json.dumps(result));self.assertNotIn('/var/private',json.dumps(result))
        for private in ('/data/private/state.json','/var/lib/private/source'):
            for where in ('value','source'):
                cell={'state':'known','value':'verified','source':{'source_ref':'state','sha256':'a'*64,
                      'selector':{'path':['current_work','objective']}}}
                if where=='value':cell['value']='See '+private
                else:cell['source']['note']='Evidence '+private
                result=explanations({'current_work':cell})['current_work']
                self.assertEqual(result['state'],'unknown');self.assertNotIn(private,json.dumps(result))
