"""Configured projection adoption binds exact bytes, row, registry and owner CAS."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_management_api as fixture
from hub.connection_records import content_hash
from hub.management_store import ManagementStore, _request_hash, validate_management_state
from hub.service_contract import ServiceError

class ManagementProposalTests(unittest.TestCase):
    call=fixture.ManagementAPITests.call
    stop=fixture.ManagementAPITests.stop
    session=fixture.ManagementAPITests.session
    command=fixture.ManagementAPITests.command
    post=fixture.ManagementAPITests.post

    def setUp(self):
        fixture.ManagementAPITests.setUp(self)
        self.projection=self.fixture.hub/'data/connections/projection.json'
        self.proposal={'id':'a-b','project_ids':['a','b'],'kind':'pipeline','status':'proposed',
            'shared_tasks':['Shared handoff'],'differences':['Separate task ownership'],
            'not_shared':['Source authority'],'inventory_index':0,'evidence_refs':['fixture://evidence']}
        self.write_projection()
        self.service.relations_path=self.projection
        self.session(owner=True)

    def write_projection(self):
        self.projection.write_text(json.dumps({'projects':[{'project_id':ident,'status':'proposed',
            'reason':None,'relations':[self.proposal],'program_link_authority':False,
            'design_selection_authority':False} for ident in ('a','b')]}))

    def reference(self):
        return self.service.get_management('a')['proposals'][0]['proposal_ref']

    def adoption(self,ident='adopt'):
        view=self.service.get_management('a')
        if view['overlay'] is None:
            # Dates/defaults are a separate normal save, never collateral to a
            # proposal. Do not seed unrelated history/date-only test cases.
            baseline=self.command(request='proposal-baseline-'+ident,value=fixture.overlay())
            status,_,body=self.post(baseline);self.assertEqual(status,200,body)
            view=self.service.get_management('a')
        candidate=view['proposals'][0]
        value=copy.deepcopy(view['overlay'])
        value['relations']=(copy.deepcopy(value['relations']) if value['relations'] is not None
                            else fixture.relations())
        value['relations']['related_to']=list(dict.fromkeys(value['relations']['related_to']+
            [member for member in candidate['project_ids'] if member!='a']))
        command=self.command(request=ident,value=value)
        command['proposal_ref']=candidate['proposal_ref']
        return command

    def assert_no_write(self,command,code):
        before=self.config.read_bytes()
        status,_,body=self.post(command)
        self.assertEqual(status,409,body);self.assertEqual(body['error']['code'],code)
        self.assertEqual(self.config.read_bytes(),before)

    def test_real_api_adopt_replay_recent_history_and_explicit_restore_preserve_dates(self):
        source_before=self.projection.read_bytes()
        command=self.adoption()
        ref=command['proposal_ref']
        self.assertEqual(ref['source_sha256'],hashlib.sha256(source_before).hexdigest())
        self.assertEqual(ref['proposal_hash'],content_hash(self.proposal))
        status,_,body=self.post(command);self.assertEqual(status,200,body)
        self.assertFalse(body['data']['receipt']['replayed'])
        view=body['data']['view'];self.assertEqual(view['overlay']['start_date'],'2026-10-14')
        self.assertEqual(view['history_recent'][-1]['proposal_ref'],ref)
        self.assertEqual(view['history_recent'][-1]['after'],command['value'])
        self.assertEqual(self.projection.read_bytes(),source_before)
        before=self.config.read_bytes()
        status,_,body=self.post(command);self.assertEqual(status,200,body)
        self.assertTrue(body['data']['receipt']['replayed']);self.assertEqual(before,self.config.read_bytes())
        self.projection.write_text(self.projection.read_text()+'\n')
        status,_,replayed=self.post(command)
        self.assertEqual(status,200,replayed);self.assertTrue(replayed['data']['receipt']['replayed'])
        self.assertEqual(before,self.config.read_bytes())
        new_request=copy.deepcopy(command);new_request['request_id']='new-after-projection-change'
        self.assert_no_write(new_request,'MANAGEMENT_PROPOSAL_STALE')
        import yaml
        config=yaml.safe_load(before);self.assertEqual(config['schema_version'],'1.4');validate_management_state(config)
        changed=copy.deepcopy(command);changed['proposal_ref']['proposal_hash']='0'*64
        self.assert_no_write(changed,'MANAGEMENT_REQUEST_CONFLICT')
        self.projection.write_text(self.projection.read_text()+'\n')
        rebound=copy.deepcopy(command);rebound['proposal_ref']=self.reference()
        self.assert_no_write(rebound,'MANAGEMENT_REQUEST_CONFLICT')
        clear=self.command(request='clear',value=fixture.overlay(relations=fixture.relations()))
        self.assertEqual(self.post(clear)[0],200)
        restore=self.command(request='restore',value=command['value'])
        self.assertNotIn('proposal_ref',restore)
        status,_,body=self.post(restore);self.assertEqual(status,200,body)
        self.assertEqual(body['data']['view']['overlay'],command['value'])
        self.assertNotIn('proposal_ref',body['data']['view']['history_recent'][-1])

    def test_stale_projection_registry_excluded_and_wrong_mapping_do_not_write(self):
        command=self.adoption()
        self.projection.write_text(self.projection.read_text()+'\n')
        self.assert_no_write(command,'MANAGEMENT_PROPOSAL_STALE')
        command=self.adoption('registry-drift')
        self.fixture.projects[1]['name']='B changed registry';self.fixture.save_registry()
        self.assert_no_write(command,'MANAGEMENT_PROPOSAL_STALE')
        command=self.adoption('excluded')
        self.fixture.projects[1]['connection_read_allowed']=False;self.fixture.save_registry()
        self.assert_no_write(command,'MANAGEMENT_PROPOSAL_STALE')
        self.assertEqual(self.service.get_management('a')['proposals'],[])
        self.fixture.projects[1]['connection_read_allowed']=True;self.fixture.save_registry()
        command=self.adoption('bad-mapping');command['value']['relations']=fixture.relations(depends=['b'])
        self.assert_no_write(command,'MANAGEMENT_PROPOSAL_MAPPING_REJECTED')

    def test_commit_authorization_rechecks_proposal_identity(self):
        command=self.adoption();before=self.config.read_bytes()
        original_apply=ManagementStore.apply
        def apply(store,*args,**kwargs):
            store.fault=lambda stage:self.projection.write_text(self.projection.read_text()+'\n') if stage=='before_replace' else None
            return original_apply(store,*args,**kwargs)
        with mock.patch.object(ManagementStore,'apply',autospec=True,side_effect=apply):
            status,_,body=self.post(command)
        self.assertEqual(status,409,body);self.assertEqual(body['error']['code'],'MANAGEMENT_PROPOSAL_STALE')
        self.assertEqual(before,self.config.read_bytes())
        self.assertEqual(list(self.config.parent.glob('.management-*')),[])

    def test_invalid_projection_has_no_refs_and_default_none_accepts_nothing(self):
        command=self.adoption()
        self.service.relations_path=None
        self.assertEqual(self.service.get_management('a')['proposals'],[])
        self.assert_no_write(command,'MANAGEMENT_PROPOSAL_STALE')
        self.service.relations_path=self.projection
        for patch in ({'status':'confirmed'},{'project_ids':['a','a']},{'project_ids':['a','absent']},
                      {'inventory_index':True},{'evidence_refs':[]},{'extra':'unrecognized'}):
            saved=copy.deepcopy(self.proposal);self.proposal.update(patch);self.write_projection()
            self.assertEqual(self.service.get_management('a')['proposals'],[],patch)
            self.proposal=saved

    def test_old_request_hash_and_history_unchanged_without_ref(self):
        command=self.command(request='legacy',value=fixture.overlay())
        expected=content_hash({key:command[key] for key in ('request_id','project_id','operation','value')})
        self.assertEqual(_request_hash(command['request_id'],'a','set',command['value']),expected)
        self.assertEqual(self.post(command)[0],200)
        state=self.service.get_management('a');oldrow=copy.deepcopy(ManagementStore(self.fixture.hub).snapshot()['history'][0])
        self.assertNotIn('proposal_ref',oldrow)
        self.assertEqual(self.post(self.adoption())[0],200)
        self.assertEqual(ManagementStore(self.fixture.hub).snapshot()['history'][0],oldrow)

    def test_date_diagnostics_are_specific_fixed_and_zero_write(self):
        cases=[({'start_date':'2026-02-30'},'start_date'),({'end_date':'not-a-date'},'end_date'),
            ({'end_date':'2026-10-01'},'end_date'),
            ({'timezone':'Does/NotExist','milestones':None},'timezone'),
            ({'timezone':None,'milestones':[{'id':'m','label':'  ','date':'2026-10-20'}]},'milestones.0.label'),
            ({'timezone':None,'milestones':[{'id':'m','label':'Milestone','date':'2026-99-99'}]},'milestones.0.date')]
        before=self.config.read_bytes()
        for index,(patch,field) in enumerate(cases):
            with self.subTest(field=field,index=index):
                command=self.command(request='date-error-'+str(index),value=fixture.overlay(**patch))
                status,_,body=self.post(command)
                self.assertEqual(status,400,body)
                self.assertEqual(body['error']['code'],'MANAGEMENT_INVALID_COMMAND')
                errors=body['error']['details']['field_errors']
                self.assertIn(field,[error['field'] for error in errors])
                self.assertTrue(all(set(error)=={'field','message'} for error in errors))
                self.assertEqual(before,self.config.read_bytes())

    def test_recent_history_is_bounded_and_scope_specific(self):
        for number in range(22):
            self.assertEqual(self.post(self.command(request='entry-'+str(number)))[0],200)
        history=self.service.get_management('a')['history_recent']
        self.assertEqual(len(history),20);self.assertEqual(history[0]['revision'],3)
        self.assertEqual(self.service.get_management('b')['history_recent'],[])
        self.assertTrue(all(set(row)=={'before','after','revision','request_id','operation'} for row in history))

    def test_initial_full_overlay_proposal_rejects_before_separate_baseline_save(self):
        self.assertIsNone(self.service.get_management('a')['overlay'])
        collateral=self.command(request='initial-collateral',
            value=fixture.overlay(relations=fixture.relations(related=['b'])))
        collateral['proposal_ref']=self.reference()
        self.assert_no_write(collateral,'MANAGEMENT_PROPOSAL_SCOPE_REJECTED')
        self.assertEqual(ManagementStore(self.fixture.hub).snapshot()['history'],[])
        self.assertEqual(self.post(self.command(request='explicit-baseline',value=fixture.overlay()))[0],200)
        before=self.config.read_bytes()
        precise=self.adoption('after-baseline')
        self.assertEqual(self.config.read_bytes(),before)  # Existing raw baseline is not saved again.
        self.assertEqual(precise['expected_revision'],1)
        status,_,body=self.post(precise);self.assertEqual(status,200,body)
        self.assertEqual(body['data']['view']['overlay'],precise['value'])
        self.assertEqual(len(ManagementStore(self.fixture.hub).snapshot()['history']),2)

if __name__=='__main__':unittest.main()
