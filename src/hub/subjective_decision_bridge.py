"""Optional PCH subjective decisions, persisted ONLY as existing TaskStore events.

Root supplies exact original-node anchors, an existing task binding, and live
backend authorization/authentication callbacks. Dataclasses, JSON, prompts and
hashes do not authenticate anyone or prove an OS boundary. This module never
creates tasks, changes task/owner/human state, calls a service, or grants effects.

Public flow: request(frozen_request), receive(decision_id, receipt, context=...),
resolve(decision_id, request_sha256=..., online=...). Reconstructing the bridge
against the same store recovers requests and results. An exact already-verified
receipt may finish recovery after its deadline; a new late receipt cannot.
Defaults are pure, explicitly provisional views, never proxy/owner acceptance.
"""
from __future__ import annotations
import copy
import math
import re
import time
from dataclasses import dataclass
from .service_contract import ServiceError
from .workbench_store import canonical, digest

PROJECT = 'personal-control-hub'
NODES = {'prior-business-result': 'subjective_business_result', 'prior-page-visual': 'visual_preference'}
VERSION = 'subjective-decision/1'
PREFIX = 'subjective-v1-'


def need(value, code='SUBJECTIVE_DECISION_INVALID'):
    if not value:
        raise ServiceError(code, status=409)


def text(value, limit=512):
    return type(value) is str and 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def sha(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def strings(value, maximum=8, limit=256, *, empty=False):
    return type(value) is list and (empty or bool(value)) and len(value) <= maximum and all(text(v, limit) for v in value)


@dataclass(frozen=True)
class TaskBinding:
    """Internal frozen binding, checked against actual persisted task reads."""
    task_id: str
    lineage_id: str
    payload_sha256: str
    intent_sha256: str
    input_sha256: str
    policy_sha256: str
    rules_sha256: str
    requester_id: str
    owner_id: str
    synthetic: bool = False


@dataclass(frozen=True)
class NodeAnchor:
    original_node_ref: str
    evidence_sha256: str


@dataclass(frozen=True)
class VerifiedProxyIdentity:
    """Return type of a trusted verifier, NOT a self-authenticating token.

    verify_proxy(context) must resolve authenticated backend context independently
    of the receipt. The caller owns its real authentication integration. Synthetic
    verifier contexts remain visibly synthetic in every saved result.
    """
    principal: str
    issuer: str
    authentication_sha256: str
    synthetic: bool


class SubjectiveDecisionBridge:
    def __init__(self, store, binding, nodes, *, authorize_task, verify_proxy, clock=time.time):
        need(type(binding) is TaskBinding and callable(authorize_task) and callable(verify_proxy) and callable(clock))
        need(all(text(getattr(binding, k), 128) for k in ('task_id','lineage_id','requester_id','owner_id')))
        need(all(sha(getattr(binding, k)) for k in ('payload_sha256','intent_sha256','input_sha256','policy_sha256','rules_sha256')))
        need(type(binding.synthetic) is bool and type(nodes) is dict and set(nodes) == set(NODES))
        need(all(type(n) is NodeAnchor and text(n.original_node_ref) and sha(n.evidence_sha256) for n in nodes.values()))
        self.store, self.binding, self.nodes = store, binding, copy.deepcopy(nodes)
        self.authorize_task, self.verify_proxy, self.clock = authorize_task, verify_proxy, clock
        self.binding_sha256 = digest({'binding':vars(binding), 'nodes':{k:vars(v) for k,v in sorted(nodes.items())}})

    def _now(self):
        now = self.clock(); need(number(now), 'SUBJECTIVE_CLOCK_INVALID'); return now

    def _task(self, request):
        task = self.store.task(self.binding.task_id)
        need(task is not None and task['id'] == self.binding.task_id and task['project'] == PROJECT,
             'SUBJECTIVE_TASK_BINDING_REJECTED')
        need(digest(task['payload']) == self.binding.payload_sha256 and digest(task['intent']) == self.binding.intent_sha256,
             'SUBJECTIVE_TASK_BINDING_REJECTED')
        marker = task['intent'].get('normal_dispatch_budget')
        if type(marker) is dict:
            lineage = marker.get('lineage_id')
        else:
            package = self.store.rebuild_package(task['id'])
            need(package is not None and digest(package) == task['intent'].get('package_sha256'), 'SUBJECTIVE_LINEAGE_REQUIRED')
            lineage = package['lineage']['goal_id']
        need(lineage == self.binding.lineage_id, 'SUBJECTIVE_LINEAGE_REQUIRED')
        need(not task['cancel'] and task['status'] not in {'cancelled','lost','requires_reconcile','failed'}, 'SUBJECTIVE_TASK_UNAVAILABLE')
        try:
            allowed = self.authorize_task(copy.deepcopy(task), copy.deepcopy(request))
        except Exception:
            raise ServiceError('SUBJECTIVE_AUTH_UNAVAILABLE', status=403) from None
        need(allowed is True, 'SUBJECTIVE_TASK_NOT_AUTHORIZED')
        return task

    def _request(self, value):
        keys = {'schema_version','decision_id','original_node_id','original_node_ref','original_evidence_sha256',
                'dimension','project_id','task_id','lineage_id','requester_id','question','candidates','constraints',
                'input_sha256','policy_sha256','rules_sha256','requested_at','deadline','facts_complete','default','reconsider_if'}
        need(type(value) is dict and set(value) == keys and value['schema_version'] == VERSION)
        need(type(value['original_node_id']) is str and value['original_node_id'] in NODES and value['dimension'] == NODES[value['original_node_id']], 'SUBJECTIVE_NODE_REQUIRED')
        node = self.nodes[value['original_node_id']]
        need(value['original_node_ref'] == node.original_node_ref and value['original_evidence_sha256'] == node.evidence_sha256,
             'SUBJECTIVE_NODE_BINDING_REJECTED')
        need(text(value['decision_id'], 128) and text(value['question']))
        need(value['project_id'] == PROJECT and value['task_id'] == self.binding.task_id
             and value['lineage_id'] == self.binding.lineage_id and value['requester_id'] == self.binding.requester_id,
             'SUBJECTIVE_TASK_BINDING_REJECTED')
        need(all(value[k] == getattr(self.binding, k) for k in ('input_sha256','policy_sha256','rules_sha256')), 'SUBJECTIVE_VERSION_CHANGED')
        need(number(value['requested_at']) and number(value['deadline']) and 0 < value['deadline']-value['requested_at'] <= 120)
        need(type(value['facts_complete']) is bool and strings(value['constraints']) and strings(value['reconsider_if']))
        candidates = value['candidates']
        need(type(candidates) is list and 1 <= len(candidates) <= 8)
        for c in candidates:
            need(type(c) is dict and set(c) == {'id','revision','artifact_sha256','input_sha256'})
            need(text(c['id'],128) and text(c['revision'],128) and sha(c['artifact_sha256']) and c['input_sha256'] == value['input_sha256'])
        need(len({c['id'] for c in candidates}) == len(candidates))
        default = value['default']
        if default is not None:
            need(type(default) is dict and set(default) == {'choice','reversible','scope','reason'})
            need(default['choice'] in {c['id'] for c in candidates} and default['reversible'] is True
                 and default['scope'] == 'presentation_only' and text(default['reason']), 'SUBJECTIVE_DEFAULT_REJECTED')
        # Leave room for the event envelope within the existing 8KiB event limit.
        need(len(canonical(value)) <= 7000, 'SUBJECTIVE_DECISION_CAPACITY')
        return copy.deepcopy(value)

    @staticmethod
    def _key(kind, decision_id):
        need(text(decision_id,128)); return PREFIX + kind + '-' + digest(decision_id)

    def _events(self):
        return {r['key']:r['data'] for r in self.store.events(self.binding.task_id) if r['key'].startswith(PREFIX)}

    def _load(self, decision_id):
        events = self._events(); saved = events.get(self._key('request', decision_id))
        need(saved is not None, 'SUBJECTIVE_REQUEST_NOT_FOUND')
        need(saved.get('binding_sha256') == self.binding_sha256, 'SUBJECTIVE_VERSION_CHANGED')
        request = self._request(saved['request'])
        need(saved['request_sha256'] == digest(request), 'SUBJECTIVE_HISTORY_INVALID')
        self._task(request)
        return request, saved, events

    def _problem(self, request):
        # Conservative original-problem scope: rewording, new decision IDs,
        # candidate revisions, input or policy changes never reset two reworks.
        return digest([PROJECT,self.binding.task_id,self.binding.lineage_id,request['original_node_id']])

    def request(self, value):
        request = self._request(value); self._task(request)
        events = self._events(); key = self._key('request', request['decision_id'])
        saved = {'schema_version':VERSION,'kind':'decision_requested','request':request,
                 'request_sha256':digest(request),'binding_sha256':self.binding_sha256}
        if key in events:
            need(events[key] == saved, 'SUBJECTIVE_REQUEST_CONFLICT'); return copy.deepcopy(saved)
        now = self._now(); need(request['requested_at'] <= now < request['deadline'], 'SUBJECTIVE_REQUEST_EXPIRED')
        count = sum(PREFIX+'rework-'+self._problem(request)+'-'+str(n) in events for n in (1,2))
        need(count < 2, 'SUBJECTIVE_REWORK_LIMIT')
        self.store.event(self.binding.task_id,key,saved)
        return saved

    def _identity(self, context, receipt, request):
        try:
            identity = self.verify_proxy(context)
        except Exception:
            raise ServiceError('SUBJECTIVE_PROXY_UNVERIFIED', status=403) from None
        need(type(identity) is VerifiedProxyIdentity and text(identity.principal,128) and text(identity.issuer,128)
             and sha(identity.authentication_sha256) and type(identity.synthetic) is bool
             and identity.synthetic is self.binding.synthetic, 'SUBJECTIVE_PROXY_UNVERIFIED')
        need(identity.principal == receipt['proxy_id'] and identity.principal not in {request['requester_id'],self.binding.owner_id},
             'SUBJECTIVE_PROXY_IDENTITY_REJECTED')
        return vars(identity).copy()

    def _receipt(self, value, request):
        keys = {'schema_version','decision_id','request_sha256','input_sha256','policy_sha256','rules_sha256','proxy_id',
                'status','choice','rationale','rule_refs','uncertainty','rework','reconsider_if','issued_at'}
        need(type(value) is dict and set(value) == keys and value['schema_version'] == VERSION)
        need(value['decision_id'] == request['decision_id'] and value['request_sha256'] == digest(request), 'SUBJECTIVE_RECEIPT_BINDING_REJECTED')
        need(all(value[k] == request[k] for k in ('input_sha256','policy_sha256','rules_sha256')), 'SUBJECTIVE_VERSION_CHANGED')
        need(type(value['status']) is str and value['status'] in {'proxy_accepted','proxy_rejected','proxy_rework','proxy_advice'})
        need(type(value['choice']) is str and value['choice'] in {c['id'] for c in request['candidates']} or value['choice'] is None and value['status'] == 'proxy_rejected')
        need(text(value['proxy_id'],128) and text(value['rationale']) and strings(value['rule_refs']) and strings(value['reconsider_if']))
        need(number(value['uncertainty']) and 0 <= value['uncertainty'] <= 1 and number(value['issued_at']))
        need(request['requested_at'] <= value['issued_at'] <= request['deadline'], 'SUBJECTIVE_RECEIPT_EXPIRED')
        need(type(value['rework']) is list and len(value['rework']) <= 4)
        for change in value['rework']:
            need(type(change) is dict and set(change) == {'issue','change'} and all(text(v,256) for v in change.values()))
        need(bool(value['rework']) == (value['status'] == 'proxy_rework'), 'SUBJECTIVE_REWORK_REQUIRED')
        need(len(canonical(value)) <= 6000, 'SUBJECTIVE_DECISION_CAPACITY')
        return copy.deepcopy(value)

    def _rework_claim(self, request, receipt):
        return {'decision_id':request['decision_id'],'request_sha256':digest(request),'receipt_sha256':digest(receipt)}

    def _rework_limit(self, request, receipt, events):
        claim = self._rework_claim(request, receipt)
        slots = [events.get(PREFIX+'rework-'+self._problem(request)+'-'+str(n)) for n in (1,2)]
        return all(value is not None for value in slots) and claim not in slots

    def _verified_history(self, request, saved, events):
        choice = events.get(self._key('choice',request['decision_id']))
        result = events.get(self._key('result',request['decision_id']))
        if choice is not None:
            need(type(choice) is dict and set(choice) == {'schema_version','request_sha256','receipt','identity','verified_at'}
                 and choice['schema_version'] == VERSION and choice['request_sha256'] == saved['request_sha256'], 'SUBJECTIVE_HISTORY_INVALID')
            self._receipt(choice['receipt'], request)
            identity = choice['identity']
            need(type(identity) is dict and set(identity) == {'principal','issuer','authentication_sha256','synthetic'}
                 and text(identity['principal'],128) and text(identity['issuer'],128) and sha(identity['authentication_sha256'])
                 and identity['synthetic'] is self.binding.synthetic
                 and identity['principal'] == choice['receipt']['proxy_id']
                 and identity['principal'] not in {request['requester_id'],self.binding.owner_id}
                 and number(choice['verified_at']) and request['requested_at'] <= choice['receipt']['issued_at'] <= choice['verified_at'] < request['deadline'], 'SUBJECTIVE_HISTORY_INVALID')
        if result is not None:
            expected = {'schema_version':VERSION,'kind':'proxy_decided','decision_id':request['decision_id'],
                        'request_sha256':saved['request_sha256'],'receipt':choice['receipt'] if choice else None,
                        'proxy_identity':choice['identity'] if choice else None,
                        'owner_acceptance':False,'execution_authority':False,'task_state_changed':False}
            if result.get('kind') == 'rework_limited':
                expected.update(kind='rework_limited', reason='SUBJECTIVE_REWORK_LIMIT')
                need(choice is not None and choice['receipt']['status'] == 'proxy_rework'
                     and self._rework_limit(request, choice['receipt'], events), 'SUBJECTIVE_HISTORY_INVALID')
            need(choice is not None and result == expected, 'SUBJECTIVE_HISTORY_INVALID')
        return choice, result

    def receive(self, decision_id, value, *, context):
        request, saved, events = self._load(decision_id)
        self._verified_history(request, saved, events)
        receipt = self._receipt(value, request); identity = self._identity(context, receipt, request)
        choice_key, result_key = self._key('choice',decision_id), self._key('result',decision_id)
        verified = events.get(choice_key)
        if verified:
            need(verified['receipt'] == receipt and all(verified['identity'][k] == identity[k] for k in ('principal','issuer','synthetic')),
                 'SUBJECTIVE_RECEIPT_CONFLICT')
        else:
            now = self._now(); need(request['requested_at'] <= receipt['issued_at'] <= now < request['deadline'], 'SUBJECTIVE_RECEIPT_EXPIRED')
            verified = {'schema_version':VERSION,'request_sha256':saved['request_sha256'],'receipt':receipt,'identity':identity,'verified_at':now}
            try:
                self.store.event(self.binding.task_id,choice_key,verified)
            except ServiceError as error:
                if error.code != 'TASK_EVENT_CONFLICT': raise
                prior = self._events().get(choice_key)
                need(prior is not None and prior['receipt'] == receipt and all(prior['identity'][k] == identity[k] for k in ('principal','issuer','synthetic')), 'SUBJECTIVE_RECEIPT_CONFLICT')
                verified = prior
        if result_key in events:
            need(events[result_key]['kind'] != 'rework_limited', 'SUBJECTIVE_REWORK_LIMIT')
            return copy.deepcopy(events[result_key])
        limited = False
        if receipt['status'] == 'proxy_rework':
            claim = self._rework_claim(request, receipt)
            for slot in (1,2):
                key = PREFIX+'rework-'+self._problem(request)+'-'+str(slot)
                try:
                    self.store.event(self.binding.task_id,key,claim)
                    break
                except ServiceError as error:
                    if error.code != 'TASK_EVENT_CONFLICT': raise
            else:
                # Fence the receipt before quota CAS. Exhaustion is a known
                # outcome, not an indefinitely pending verified receipt.
                limited = True
        result = {'schema_version':VERSION,'kind':'proxy_decided','decision_id':decision_id,
                  'request_sha256':saved['request_sha256'],'receipt':verified['receipt'],'proxy_identity':verified['identity'],
                  'owner_acceptance':False,'execution_authority':False,'task_state_changed':False}
        if limited:
            result.update(kind='rework_limited', reason='SUBJECTIVE_REWORK_LIMIT')
        self._task(request)
        self.store.event(self.binding.task_id,result_key,result)
        need(not limited, 'SUBJECTIVE_REWORK_LIMIT')
        return copy.deepcopy(result)

    def resolve(self, decision_id, *, request_sha256, online=True):
        need(type(online) is bool)
        request, saved, events = self._load(decision_id)
        need(request_sha256 == saved['request_sha256'], 'SUBJECTIVE_RECEIPT_BINDING_REJECTED')
        choice, result = self._verified_history(request, saved, events)
        base = {'decision_id':decision_id,'request_sha256':request_sha256,'execution_authority':False,
                'owner_acceptance':False,'task_state_changed':False,'affected_dependency':self._problem(request)}
        # Preferences never establish missing source facts. The frozen gap
        # dominates online waiting, defaults and any recorded proxy result.
        if not request['facts_complete']:
            return {**base,'state':'dependency_isolated','reason':'MISSING_FACTS','blocking':True}
        if (result is not None and result['kind'] == 'rework_limited'
                or choice is not None and choice['receipt']['status'] == 'proxy_rework'
                and self._rework_limit(request, choice['receipt'], events)):
            # Also recognize the cap if its terminal event failed to commit.
            return {**base,'state':'dependency_isolated','reason':'SUBJECTIVE_REWORK_LIMIT','blocking':True}
        if result is not None:
            return {**base,'state':'proxy_decided','result':copy.deepcopy(result),'blocking':False}
        if choice is not None:
            return {**base,'state':'requires_reconcile','reason':'VERIFIED_RECEIPT_PENDING_FINALIZATION','blocking':True}
        if online and self._now() < request['deadline']:
            return {**base,'state':'pending','blocking':True}
        if request['default'] is None:
            return {**base,'state':'dependency_isolated','reason':'NO_REVERSIBLE_DEFAULT','blocking':True}
        return {**base,'state':'provisional','choice':request['default']['choice'],'reason':'OFFLINE_OR_DEADLINE',
                'reconsider_if':copy.deepcopy(request['reconsider_if']),'proxy_accepted':False,'blocking':False}
