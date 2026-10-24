#!/usr/bin/env python3
"""Socket/service-free checks; every identity, artifact and lineage is synthetic.

Creates disposable tasks only through TaskStore's existing trusted local APIs.
These checks prove the protocol, not proxy authentication, real task adoption,
OS isolation, historic owner acceptance or any native execution capability.
"""
from __future__ import annotations
import copy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from hub.subjective_decision_bridge import (SubjectiveDecisionBridge, TaskBinding, NodeAnchor,
                                            VerifiedProxyIdentity, VERSION, PROJECT)
from hub.task_store import TaskStore
from hub.service_contract import ServiceError
from hub.workbench_store import digest, canonical


class BridgeChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='subjective-bridge-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'control'; self.root.mkdir()
        self.store = TaskStore(self.root); self.store.register_storage()
        self.store.register_grant({'grant_id':'synthetic-grant','version':1,'source':'synthetic-backend-test'})
        self.payload = {'request_id':'synthetic-task','project_id':PROJECT,'parent_task_id':None,'mode':'new','kind':'synthetic_readonly_test'}
        self.intent = {'grant':self.store.grant('synthetic-grant'),
                       'normal_dispatch_budget':{'lineage_id':'synthetic-lineage'},
                       'owner_context':'synthetic-owner-context','legacy_human':'HUMAN_ONLY'}
        # Internal synthetic admission, not an execution grant or historical row.
        self.store.admit(self.payload,self.intent)
        self.store.event('synthetic-task','synthetic-legacy-evidence',{'human':'HUMAN_ONLY','verified':107,'total':111,'owner_accepted':None})
        self.original_task = self.store.task('synthetic-task')
        self.original_history = self.store.events('synthetic-task')
        self.binding = TaskBinding('synthetic-task','synthetic-lineage',digest(self.payload),digest(self.intent),
                                   'a'*64,'b'*64,'c'*64,'synthetic-requester','synthetic-owner',True)
        self.nodes = {'prior-business-result':NodeAnchor('synthetic:legacy/business-result','d'*64),
                      'prior-page-visual':NodeAnchor('synthetic:legacy/visual','e'*64)}
        self.time = 1000.0; self.allowed = True; self.proxy_calls = 0; self.token = object()
        self.identity = VerifiedProxyIdentity('synthetic-proxy','synthetic-authenticator','f'*64,True)
        self.bridge = self.reopen()

    def verify(self, context):
        self.proxy_calls += 1
        return self.identity if context is self.token else None

    def authorize(self, task, request):
        return self.allowed is True and task['intent']['grant'] == self.store.grant('synthetic-grant')

    def reopen(self, **changes):
        return SubjectiveDecisionBridge(TaskStore(self.root), changes.get('binding',self.binding), self.nodes,
                                        authorize_task=self.authorize,verify_proxy=self.verify,clock=lambda:self.time)

    def request(self, ident='decision-one', node='prior-business-result', **changes):
        value = {'schema_version':VERSION,'decision_id':ident,'original_node_id':node,
                 'original_node_ref':self.nodes[node].original_node_ref,'original_evidence_sha256':self.nodes[node].evidence_sha256,
                 'dimension':'subjective_business_result' if node=='prior-business-result' else 'visual_preference',
                 'project_id':PROJECT,'task_id':'synthetic-task','lineage_id':'synthetic-lineage','requester_id':'synthetic-requester',
                 'question':'Choose the clearer synthetic presentation.',
                 'candidates':[{'id':'synthetic-option','revision':'synthetic-r1','artifact_sha256':'1'*64,'input_sha256':'a'*64}],
                 'constraints':['Only reversible presentation; no execution or permissions.'],
                 'input_sha256':'a'*64,'policy_sha256':'b'*64,'rules_sha256':'c'*64,
                 'requested_at':self.time,'deadline':self.time+120,'facts_complete':True,
                 'default':{'choice':'synthetic-option','reversible':True,'scope':'presentation_only','reason':'Use the smaller reversible preview.'},
                 'reconsider_if':['New source facts or explicit constraints change.']}
        value.update(changes); return value

    def receipt(self, request, **changes):
        value = {'schema_version':VERSION,'decision_id':request['decision_id'],'request_sha256':digest(request),
                 'input_sha256':request['input_sha256'],'policy_sha256':request['policy_sha256'],'rules_sha256':request['rules_sha256'],
                 'proxy_id':'synthetic-proxy','status':'proxy_accepted','choice':'synthetic-option',
                 'rationale':'Smaller reversible synthetic presentation.','rule_refs':['synthetic-rule:one'],
                 'uncertainty':0.25,'rework':[],'reconsider_if':['New verified input.'],'issued_at':self.time}
        value.update(changes); return value

    def rejected(self, code, call):
        with self.assertRaises(ServiceError) as error:call()
        self.assertEqual(error.exception.code,code)

    def unchanged(self):
        self.assertEqual(self.store.task('synthetic-task'),self.original_task)
        self.assertEqual(self.store.events('synthetic-task')[:len(self.original_history)],self.original_history)
        self.assertEqual(len(self.store.tasks()),1)

    def test_both_original_nodes_use_independent_synthetic_identity_and_unique_results(self):
        for node in self.nodes:
            request=self.request(node,node);saved=self.bridge.request(request)
            result=self.bridge.receive(node,self.receipt(request),context=self.token)
            self.assertEqual(result['receipt']['status'],'proxy_accepted')
            self.assertTrue(result['proxy_identity']['synthetic'])
            self.assertNotEqual(result['proxy_identity']['principal'],request['requester_id'])
            self.assertFalse(result['owner_acceptance']);self.assertFalse(result['execution_authority'])
            resolved=self.bridge.resolve(node,request_sha256=saved['request_sha256'])
            self.assertEqual(resolved['state'],'proxy_decided')
        self.unchanged()

    def test_non_subjective_dimensions_and_wrong_original_anchors_are_rejected(self):
        for dimension in ['data','interaction','auth','device','permission','user_pause','engineering','build','truth']:
            with self.subTest(dimension=dimension):
                self.rejected('SUBJECTIVE_NODE_REQUIRED',lambda:self.bridge.request(self.request(dimension=dimension)))
        for fields,code in [({'original_node_id':'unknown-node'},'SUBJECTIVE_NODE_REQUIRED'),
                            ({'original_node_ref':'synthetic:wrong'},'SUBJECTIVE_NODE_BINDING_REJECTED'),
                            ({'original_evidence_sha256':'0'*64},'SUBJECTIVE_NODE_BINDING_REJECTED')]:
            self.rejected(code,lambda:self.bridge.request(self.request(**fields)))
        self.assertEqual(self.proxy_calls,0);self.unchanged()

    def test_reopen_same_store_idempotency_and_exact_conflicts(self):
        request=self.request();self.bridge.request(request);receipt=self.receipt(request)
        result=self.bridge.receive(request['decision_id'],receipt,context=self.token)
        events=self.store.events('synthetic-task');raw=self.store.path.read_bytes()
        bridge=self.reopen();self.assertEqual(bridge.request(request)['request'],request)
        self.assertEqual(bridge.receive(request['decision_id'],receipt,context=self.token),result)
        self.assertEqual(bridge.resolve(request['decision_id'],request_sha256=digest(request))['result'],result)
        self.assertEqual(self.store.events('synthetic-task'),events);self.assertEqual(self.store.path.read_bytes(),raw)
        for fields in ({'question':'Different question.'},{'candidates':[{**request['candidates'][0],'revision':'synthetic-r2'}]}):
            self.rejected('SUBJECTIVE_REQUEST_CONFLICT',lambda:bridge.request({**request,**fields}))
        self.rejected('SUBJECTIVE_RECEIPT_CONFLICT',lambda:bridge.receive(request['decision_id'],{**receipt,'rationale':'Different rationale.'},context=self.token))
        self.unchanged()

    def test_unverified_self_reported_or_requester_owner_identity_cannot_accept(self):
        request=self.request();self.bridge.request(request);receipt=self.receipt(request)
        for context in (None, {'verified':True,'proxy_id':'synthetic-proxy'}, self.identity):
            self.rejected('SUBJECTIVE_PROXY_UNVERIFIED',lambda:self.bridge.receive(request['decision_id'],receipt,context=context))
        for principal in ('synthetic-owner','synthetic-requester','different-proxy'):
            self.identity=replace(self.identity,principal=principal)
            self.rejected('SUBJECTIVE_PROXY_IDENTITY_REJECTED',lambda:self.bridge.receive(request['decision_id'],receipt,context=self.token))
        self.identity=replace(self.identity,principal='synthetic-proxy',synthetic=False)
        self.rejected('SUBJECTIVE_PROXY_UNVERIFIED',lambda:self.bridge.receive(request['decision_id'],receipt,context=self.token))
        self.assertNotEqual(self.bridge.resolve(request['decision_id'],request_sha256=digest(request))['state'],'proxy_decided')

    def test_current_input_policy_rules_task_lineage_and_authorization_are_bound(self):
        request=self.request();self.bridge.request(request)
        for field in ('input_sha256','policy_sha256','rules_sha256'):
            self.rejected('SUBJECTIVE_VERSION_CHANGED',lambda:self.bridge.receive(request['decision_id'],self.receipt(request,**{field:'0'*64}),context=self.token))
            bridge=self.reopen(binding=replace(self.binding,**{field:'0'*64}))
            self.rejected('SUBJECTIVE_VERSION_CHANGED',lambda:bridge.resolve(request['decision_id'],request_sha256=digest(request)))
        for field,value in [('task_id','missing-task'),('intent_sha256','0'*64),('payload_sha256','0'*64),('lineage_id','wrong-lineage')]:
            bridge=self.reopen(binding=replace(self.binding,**{field:value}))
            with self.assertRaises(ServiceError):bridge.request({**request,'task_id':getattr(bridge.binding,'task_id'),'lineage_id':getattr(bridge.binding,'lineage_id')})
        self.allowed=False
        self.rejected('SUBJECTIVE_TASK_NOT_AUTHORIZED',lambda:self.bridge.receive(request['decision_id'],self.receipt(request),context=self.token))
        self.unchanged()

    def test_deadline_120_offline_provisional_no_default_and_missing_facts(self):
        request=self.request();self.bridge.request(request)
        view=self.bridge.resolve(request['decision_id'],request_sha256=digest(request),online=False)
        self.assertEqual(view['state'],'provisional');self.assertFalse(view['proxy_accepted']);self.assertFalse(view['owner_acceptance'])
        self.time+=120
        self.assertEqual(self.bridge.resolve(request['decision_id'],request_sha256=digest(request))['state'],'provisional')
        self.rejected('SUBJECTIVE_RECEIPT_EXPIRED',lambda:self.bridge.receive(request['decision_id'],self.receipt(request,issued_at=self.time-1),context=self.token))
        self.rejected('SUBJECTIVE_DECISION_INVALID',lambda:self.bridge.request(self.request('overlong',deadline=self.time+121)))
        self.rejected('SUBJECTIVE_DEFAULT_REJECTED',lambda:self.bridge.request(self.request(default={'choice':'synthetic-option','reversible':False,'scope':'presentation_only','reason':'Not reversible.'})))
        for ident,fields,reason in [('no-default',{'default':None},'NO_REVERSIBLE_DEFAULT'),('no-facts',{'facts_complete':False},'MISSING_FACTS')]:
            req=self.request(ident,**fields);self.bridge.request(req)
            value=self.bridge.resolve(ident,request_sha256=digest(req),online=False)
            self.assertEqual(value['state'],'dependency_isolated');self.assertEqual(value['reason'],reason)
        self.unchanged()

    def test_rework_limit_survives_new_ids_rewording_input_policy_and_reopening(self):
        for i in (1,2):
            request=self.request('rework-'+str(i),question='Presentation iteration '+str(i))
            self.bridge.request(request);receipt=self.receipt(request,status='proxy_rework',rework=[{'issue':'Dense label','change':'Shorten only the synthetic caption.'}])
            self.reopen().receive(request['decision_id'],receipt,context=self.token)
        self.rejected('SUBJECTIVE_REWORK_LIMIT',lambda:self.reopen().request(self.request('new-id',question='Reword the same original problem.')))
        newer=replace(self.binding,input_sha256='2'*64,policy_sha256='3'*64)
        bridge=self.reopen(binding=newer);req=self.request('new-input',input_sha256='2'*64,policy_sha256='3'*64)
        req['candidates'][0]['input_sha256']='2'*64
        self.rejected('SUBJECTIVE_REWORK_LIMIT',lambda:bridge.request(req))
        self.unchanged()

    def test_unknown_commit_recovers_verified_receipt_after_deadline_without_second_result(self):
        request=self.request();self.bridge.request(request);receipt=self.receipt(request)
        self.store.fault=lambda phase:(_ for _ in ()).throw(OSError('synthetic durability uncertainty')) if phase=='after_commit' else None
        # Use this exact store instance for injected existing transaction failure.
        self.bridge.store=self.store
        self.rejected('TASK_COMMIT_UNCONFIRMED',lambda:self.bridge.receive(request['decision_id'],receipt,context=self.token))
        self.store.fault=lambda _:None;self.time+=121
        recovered=self.reopen();self.assertEqual(recovered.resolve(request['decision_id'],request_sha256=digest(request))['state'],'requires_reconcile')
        result=recovered.receive(request['decision_id'],receipt,context=self.token)
        self.assertEqual(result['receipt'],receipt)
        self.assertEqual(len([e for e in self.store.events('synthetic-task') if e['data'].get('kind')=='proxy_decided']),1)
        self.unchanged()

    def test_nonblocking_advice_and_request_hash_guard(self):
        request=self.request();self.bridge.request(request)
        self.bridge.receive(request['decision_id'],self.receipt(request,status='proxy_advice'),context=self.token)
        self.rejected('SUBJECTIVE_RECEIPT_BINDING_REJECTED',lambda:self.bridge.resolve(request['decision_id'],request_sha256='0'*64))
        self.assertFalse(self.bridge.resolve(request['decision_id'],request_sha256=digest(request))['blocking'])
        self.unchanged()

    def test_engineering_completes_via_existing_store_without_proxy_or_human_gate(self):
        # Deterministic synthetic engineering, not a native model/task executor.
        for i in (1,2):
            ident='engineering-'+str(i)
            self.store.admit({'request_id':ident,'project_id':'synthetic-engineering','parent_task_id':None,'mode':'new','kind':'synthetic_engineering'}, {'grant':{'root':str(self.root)}})
            task=self.store.claim('synthetic-worker',only_task_id=ident)
            self.assertIsNotNone(task)
            self.assertEqual(digest({'synthetic_check':i}),digest({'synthetic_check':i}))
            self.store.update(ident,'synthetic-worker',status='validating')
            self.store.update(ident,'synthetic-worker',status='checks_complete',result={'checks':[{'exit':0,'scope':'synthetic deterministic engineering'}]})
            self.assertEqual(self.store.task(ident)['status'],'checks_complete')
            self.assertEqual(self.store.task(ident)['human_acceptance'],'pending')
        self.assertEqual(self.proxy_calls,0)
        self.assertEqual(self.store.events('synthetic-task'),self.original_history)


    def isolated(self, bridge, request, reason):
        for online in (True,False):
            view=bridge.resolve(request['decision_id'],request_sha256=digest(request),online=online)
            self.assertEqual((view['state'],view['reason'],view['blocking']),('dependency_isolated',reason,True))
            self.assertFalse(view['execution_authority']);self.assertFalse(view['owner_acceptance'])

    def rework_receipt(self, request):
        return self.receipt(request,status='proxy_rework',rework=[{'issue':'Dense synthetic caption','change':'Shorten only the caption.'}])

    def preissued(self):
        requests=[self.request('preissued-'+str(i)) for i in range(3)]
        for request in requests:self.bridge.request(request)
        return requests

    def test_missing_facts_isolates_before_and_after_verified_receipts_and_reopen(self):
        for node in self.nodes:
            request=self.request('missing-'+node,node,facts_complete=False)
            self.bridge.request(request);self.isolated(self.bridge,request,'MISSING_FACTS')
            self.bridge.receive(request['decision_id'],self.receipt(request),context=self.token)
            self.isolated(self.reopen(),request,'MISSING_FACTS')
        self.time+=121
        for node in self.nodes:
            request=self.request('unused',node)
            saved=self.reopen()._load('missing-'+node)[0]
            self.isolated(self.reopen(),saved,'MISSING_FACTS')
        self.unchanged()

    def test_missing_facts_also_dominates_verified_choice_unknown_commit(self):
        request=self.request(facts_complete=False);self.bridge.request(request);receipt=self.receipt(request)
        self.bridge.store=self.store
        self.store.fault=lambda phase:(_ for _ in ()).throw(OSError('synthetic unknown choice commit')) if phase=='after_commit' else None
        self.rejected('TASK_COMMIT_UNCONFIRMED',lambda:self.bridge.receive(request['decision_id'],receipt,context=self.token))
        self.store.fault=lambda _:None;reopened=self.reopen()
        self.isolated(reopened,request,'MISSING_FACTS')
        reopened.receive(request['decision_id'],receipt,context=self.token)
        self.isolated(self.reopen(),request,'MISSING_FACTS');self.unchanged()

    def test_preissued_third_rework_has_known_limited_outcome_and_no_retry_writes(self):
        requests=self.preissued()
        for request in requests[:2]:self.bridge.receive(request['decision_id'],self.rework_receipt(request),context=self.token)
        prior_claims=[e for e in self.store.events('synthetic-task') if e['key'].startswith('subjective-v1-rework-')]
        third=requests[2];receipt=self.rework_receipt(third)
        self.rejected('SUBJECTIVE_REWORK_LIMIT',lambda:self.bridge.receive(third['decision_id'],receipt,context=self.token))
        self.isolated(self.reopen(),third,'SUBJECTIVE_REWORK_LIMIT')
        before=self.store.events('synthetic-task');raw=self.store.path.read_bytes()
        self.rejected('SUBJECTIVE_REWORK_LIMIT',lambda:self.reopen().receive(third['decision_id'],receipt,context=self.token))
        self.assertEqual(before,self.store.events('synthetic-task'));self.assertEqual(raw,self.store.path.read_bytes())
        self.assertEqual(prior_claims,[e for e in before if e['key'].startswith('subjective-v1-rework-')])
        self.assertEqual(len([e for e in before if e['data'].get('kind')=='rework_limited']),1)
        # Only the same original problem is isolated; the other node remains usable.
        other=self.request('other-subjective-node','prior-page-visual');self.bridge.request(other)
        self.bridge.receive(other['decision_id'],self.receipt(other),context=self.token)
        self.assertEqual(self.bridge.resolve(other['decision_id'],request_sha256=digest(other))['state'],'proxy_decided')
        self.unchanged()

    def test_interleaved_claims_exhaust_slots_after_third_choice_without_stranding_it(self):
        requests=self.preissued();third=requests[2];bridge=self.reopen();real_event=bridge.store.event;interleaved=[]
        def event(ident,key,value):
            result=real_event(ident,key,value)
            if key==bridge._key('choice',third['decision_id']) and not interleaved:
                interleaved.append(True)
                for request in requests[:2]:self.reopen().receive(request['decision_id'],self.rework_receipt(request),context=self.token)
            return result
        bridge.store.event=event
        self.rejected('SUBJECTIVE_REWORK_LIMIT',lambda:bridge.receive(third['decision_id'],self.rework_receipt(third),context=self.token))
        self.isolated(self.reopen(),third,'SUBJECTIVE_REWORK_LIMIT')
        self.assertEqual(len([e for e in self.store.events('synthetic-task') if e['key'].startswith('subjective-v1-rework-')]),2)
        self.unchanged()

    def limited_result_failure(self, phase):
        requests=self.preissued()
        for request in requests[:2]:self.bridge.receive(request['decision_id'],self.rework_receipt(request),context=self.token)
        third=requests[2];receipt=self.rework_receipt(third);self.bridge.store=self.store;commits=[]
        def fault(current):
            if current==phase:
                commits.append(current)
                if len(commits)==2:raise OSError('synthetic limited result write failure')
        self.store.fault=fault
        self.rejected('TASK_NOT_COMMITTED' if phase=='before_commit' else 'TASK_COMMIT_UNCONFIRMED',lambda:self.bridge.receive(third['decision_id'],receipt,context=self.token))
        self.store.fault=lambda _:None
        reopened=self.reopen();self.isolated(reopened,third,'SUBJECTIVE_REWORK_LIMIT')
        self.rejected('SUBJECTIVE_REWORK_LIMIT',lambda:reopened.receive(third['decision_id'],receipt,context=self.token))
        self.isolated(self.reopen(),third,'SUBJECTIVE_REWORK_LIMIT')
        self.assertEqual(len([e for e in self.store.events('synthetic-task') if e['key'].startswith('subjective-v1-rework-')]),2)
        self.unchanged()

    def test_limited_result_precommit_failure_remains_known_limit_on_reopen(self):
        self.limited_result_failure('before_commit')

    def test_limited_result_unknown_commit_remains_known_limit_on_reopen(self):
        self.limited_result_failure('after_commit')

    def test_own_rework_slot_unknown_commit_recovers_even_when_other_slot_is_full(self):
        requests=self.preissued();first,second=requests[:2];receipt=self.rework_receipt(first);self.bridge.store=self.store;commits=[]
        def fault(phase):
            if phase=='after_commit':
                commits.append(phase)
                if len(commits)==2:raise OSError('synthetic quota commit unknown')
        self.store.fault=fault
        self.rejected('TASK_COMMIT_UNCONFIRMED',lambda:self.bridge.receive(first['decision_id'],receipt,context=self.token))
        self.store.fault=lambda _:None
        self.reopen().receive(second['decision_id'],self.rework_receipt(second),context=self.token)
        self.time+=121;reopened=self.reopen()
        self.assertEqual(reopened.resolve(first['decision_id'],request_sha256=digest(first))['state'],'requires_reconcile')
        result=reopened.receive(first['decision_id'],receipt,context=self.token)
        self.assertEqual(result['kind'],'proxy_decided')
        self.assertEqual(len([e for e in self.store.events('synthetic-task') if e['key'].startswith('subjective-v1-rework-')]),2)
        self.unchanged()

    def test_proxy_result_unknown_commit_stays_idempotent_after_reopen(self):
        request=self.request();self.bridge.request(request);receipt=self.receipt(request);self.bridge.store=self.store;commits=[]
        def fault(phase):
            if phase=='after_commit':
                commits.append(phase)
                if len(commits)==2:raise OSError('synthetic result commit unknown')
        self.store.fault=fault
        self.rejected('TASK_COMMIT_UNCONFIRMED',lambda:self.bridge.receive(request['decision_id'],receipt,context=self.token))
        self.store.fault=lambda _:None;before=self.store.events('synthetic-task');self.time+=121
        bridge=self.reopen();self.assertEqual(bridge.resolve(request['decision_id'],request_sha256=digest(request))['state'],'proxy_decided')
        bridge.receive(request['decision_id'],receipt,context=self.token)
        self.assertEqual(before,self.store.events('synthetic-task'));self.unchanged()


if __name__ == '__main__':
    unittest.main(verbosity=2)
