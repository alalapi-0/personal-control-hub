"""Source implementation is not effect permission or whole-task acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_plan_admission as fixtures
from hub.plan_admission import PlanAuthority, BOOTSTRAP_IDS, validate_plan, plan_history
from hub.source_admission import source_ready, SOURCE_IDS
from hub.service_contract import ServiceError
from hub.workbench_store import digest, canonical
from hub.task_store import TaskStore

GRAPH = 'P3-R1-PCH-GRAPH'
RELATIONS = 'P3-R1-PCH-RELATIONS'
BASE = fixtures.BASE
ROUTES = 'P2-R1-PCH-ROUTES'


class SourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        f = fixtures.PlanStoreTests(); f.setUp(); self.addCleanup(f.doCleanups)
        self.f = f; self.store = f.store; self.control = f.control
        self.records = {}; self.verified = set(); self.develop = True
        self.p = fixtures.plan([fixtures.task(BASE), fixtures.task(ROUTES),
                               fixtures.task(GRAPH, [ROUTES]), fixtures.task(RELATIONS, [GRAPH])])
        self.p['revision'] = '1.2.0-synthetic'
        self.definition(GRAPH)['source_admission'] = self.policy([])
        self.definition(RELATIONS)['source_admission'] = self.policy([
            {'task_id': GRAPH, 'kind': 'source_candidate', 'candidate_sha256': 'a' * 64}])
        self.configure(); self.store.import_plan(self.p, authority=self.cap)

    def definition(self, ident):
        return next(t for t in self.p['tasks'] if t['id'] == ident)

    def policy(self, deps):
        return {'schema_version': 'source-admission/1', 'dependencies': deps,
                'inputs': [{'path': 'schema.json', 'sha256': 'b' * 64}],
                'owned_paths': ['src/graph.py'],
                'workspace': {'canonical_root': str(self.store.root),
                              'delivery_root': str(self.store.root.parent / 'delivery'),
                              'native_thread_id': 'synthetic-thread'}}

    def configure(self, *, prior=None, development=True, verification=True):
        binding = {'plan_sha256': digest(self.p), 'owner_context': 'synthetic-owner',
                   'verified_evidence': {k: digest(v) for k, v in self.records.items()},
                   'verified_evidence_versions': {k: {v['plan_sha256']: digest(v)} for k, v in self.records.items()}}
        if prior: binding['reconcile_from_sha256'] = digest(prior)
        self.cap = PlanAuthority.configured(binding, authorize=lambda: None,
            controls=lambda: copy.deepcopy(self.control), routes=lambda _: False, package_for=lambda _: None,
            development=(lambda _: self.develop) if development else None,
            source_evidence_verification=(lambda r: digest(r) in self.verified) if verification else None)

    def view(self):
        return self.store.plan_source_ready(self.p['plan_id'], authority=self.cap)

    def candidate(self, ident=GRAPH, *, request='source-one', candidate='a', status='implemented'):
        t = self.definition(ident); p = t['source_admission']
        r = {'schema_version': 'plan-source-record/1', 'request_id': request,
             'plan_sha256': digest(self.p), 'task_id': ident, 'definition_sha256': digest(t),
             'candidate_sha256': candidate * 64, 'status': status,
             'unknowns': ['outcome_unknown'] if status == 'unknown' else [],
             'checks': [{'name': 'targeted', 'status': 'passed', 'evidence_sha256': 'c' * 64}],
             'provenance': {'source': 'root_verified', 'evidence_sha256': 'd' * 64}}
        for k in ('workspace', 'inputs', 'owned_paths'): r[k] = copy.deepcopy(p[k])
        self.verified.add(digest(r)); return r

    def record(self, r, expected=None):
        return self.store.record_plan_source_evidence(self.p['plan_id'], r,
            expected_source_sha256=expected or self.view()['source_sha256'], authority=self.cap)

    def test_source_ready_without_routes_does_not_admit_execution_or_acceptance(self):
        self.assertEqual(self.view()['ready'], [GRAPH])
        self.record(self.candidate())
        self.assertIn(RELATIONS, self.view()['ready'])
        self.assertEqual(self.view()['accepted'], [])
        self.assertNotIn(GRAPH, self.store.plan_ready(self.p['plan_id'], authority=self.cap)['ready'])
        self.assertEqual(self.store.admit_plan_window(self.p['plan_id'], authority=self.cap)['admitted'], [])
        self.assertEqual(self.store.tasks(), [])
        with self.store._connection() as db:
            self.assertEqual(db.execute('SELECT count(*)FROM plan_receipts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*)FROM plan_conflicts').fetchone()[0], 0)
        self.assertNotIn(GRAPH, BOOTSTRAP_IDS)

    def test_default_deny_exact_true_and_live_workspace_scope_callback(self):
        self.configure(development=False); self.assertEqual(self.view()['ready'], [])
        self.configure(); self.develop = 1; self.assertEqual(self.view()['ready'], [])
        self.develop = False
        with self.assertRaises(ServiceError): self.record(self.candidate())
        self.develop = True; self.configure(verification=False)
        with self.assertRaises(ServiceError): self.record(self.candidate())
        self.assertEqual(self.view()['ready'], [])
        self.configure(); r = self.candidate(); r['owned_paths'] = ['outside.py']; self.verified.add(digest(r))
        with self.assertRaises(ServiceError): self.record(r)
        r = self.candidate(); r['workspace']['canonical_root'] += '-foreign'; self.verified.add(digest(r))
        with self.assertRaises(ServiceError): self.record(r)

    def test_candidate_exact_hash_unknown_freeze_and_callback_revocation(self):
        self.record(self.candidate(candidate='e'))
        self.assertNotIn(RELATIONS, self.view()['ready'])
        r = self.candidate(request='exact'); self.record(r)
        self.assertIn(RELATIONS, self.view()['ready'])
        self.verified.remove(digest(r)); self.assertNotIn(RELATIONS, self.view()['ready'])
        self.verified.add(digest(r))
        self.record(self.candidate(request='unknown', status='unknown'))
        self.assertNotIn(GRAPH, self.view()['ready']); self.assertNotIn(RELATIONS, self.view()['ready'])

    def test_cas_replay_conflict_and_stale_preserve_original_records(self):
        r = self.candidate(); expected = self.view()['source_sha256']; self.record(r, expected)
        before = self.store.path.read_bytes()
        self.assertTrue(self.record(r, expected)['replayed'])
        self.assertEqual(self.store.path.read_bytes(), before)
        changed = self.candidate(candidate='e')
        with self.assertRaises(ServiceError) as err: self.record(changed, expected)
        self.assertEqual(err.exception.code, 'PLAN_SOURCE_REQUEST_CONFLICT')
        with self.assertRaises(ServiceError) as err: self.record(self.candidate(request='stale'), expected)
        self.assertEqual(err.exception.code, 'PLAN_SOURCE_CAS_STALE')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.view()['source_records'][0]['record'], r)

    def test_pause_cancel_control_drift_and_atomic_fault(self):
        r = self.candidate(); expected = self.view()['source_sha256']
        for state in ('paused', 'cancelled'):
            self.control['state'] = state
            self.assertEqual(self.view()['ready'], [])
            with self.assertRaises(ServiceError): self.record(r, expected)
        self.control['state'] = 'active'; before = self.store.path.read_bytes()
        def fault(phase):
            if phase == 'before_plan_source_commit': self.control['revision'] += 1
        self.store.fault = fault
        with self.assertRaises(ServiceError) as err: self.record(r, expected)
        self.assertEqual(err.exception.code, 'PLAN_CONTROL_CHANGED')
        self.assertEqual(self.store.path.read_bytes(), before)
        def rollback(phase):
            if phase == 'before_plan_source_commit': raise OSError('synthetic failure')
        self.store.fault = rollback
        with self.assertRaises(OSError): self.record(r, expected)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_service_owner_forwarding_and_no_implicit_authority(self):
        from hub.task_service import TaskService
        service = TaskService(None, None, self.store, plan_authority=self.cap)
        with self.assertRaises(ServiceError): service.plan_source_ready(self.p['plan_id'], owner_context='foreign')
        self.assertEqual(service.plan_source_ready(self.p['plan_id'], owner_context='synthetic-owner')['ready'], [GRAPH])
        r = self.candidate()
        service.record_plan_source_evidence(self.p['plan_id'], r,
            expected_source_sha256=digest([]), owner_context='synthetic-owner')
        self.assertEqual(self.store.tasks(), [])

    def test_old_no_policy_and_strict_policy_rejection(self):
        old = fixtures.plan([fixtures.task(BASE), fixtures.task(GRAPH)])
        self.assertEqual(source_ready(old, {}, set(), [], lambda _: True, lambda _: True), [])
        for mutate in ('bootstrap', 'unknown', 'cycle', 'traversal', 'candidate_missing', 'workspace'):
            p = copy.deepcopy(self.p)
            t = next(t for t in p['tasks'] if t['id'] == GRAPH)
            if mutate == 'bootstrap': t['id'] = 'P1-R1-PCH-OFFLINE'; t['phase_id'] = 'P1'; t['admission_class'] = 'bootstrap'
            if mutate == 'unknown': t['source_admission']['unexpected'] = True
            if mutate == 'cycle': t['source_admission']['dependencies'] = [{'kind': 'accepted', 'task_id': RELATIONS}]
            if mutate == 'traversal': t['source_admission']['owned_paths'] = ['../private']
            if mutate == 'candidate_missing': p['tasks'][-1]['source_admission']['dependencies'][0].pop('candidate_sha256')
            if mutate == 'workspace': t['source_admission']['workspace']['delivery_root'] = '/tmp/../private'
            with self.subTest(mutate=mutate), self.assertRaises(ServiceError): validate_plan(p)

    def test_priority_twenty_12_preserves_other_rules(self):
        p = copy.deepcopy(self.p); t = fixtures.task('P2-R2-PCH-DAG'); t['dispatch_priority'] = 20; p['tasks'].append(t)
        validate_plan(p)
        t['dispatch_priority'] = 100
        with self.assertRaises(ServiceError): validate_plan(p)

    def test_reconcile_rollback_return_preserve_budget_and_source_applicability(self):
        import test_hub_dispatch_budget as budgets
        b = budgets.DispatchBudgetTests(); b.setUp(); self.addCleanup(b.doCleanups)
        # Existing budget APIs operate on our plan; no second DB or ledger.
        b.store = self.store; b.p = self.p; b.plan_cap = self.cap; b.control = self.control
        b.policy['lineage_id'] = self.p['plan_id']; b.initialize(); request = b.req(); b.reserve(request)
        self.record(self.candidate()); old = copy.deepcopy(self.p); original_records = self.view()['source_records']
        target = copy.deepcopy(old); target['revision'] = '1.2.0-next'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['objective'] = 'changed source definition'
        for value, prior, name, rollback in ((target, old, 'change', False), (old, target, 'back', True), (target, old, 'return', True)):
            self.p = value; self.configure(prior=prior)
            self.store.reconcile_plan(value, expected_sha256=digest(prior), request_id=name, authority=self.cap, rollback=rollback)
            self.assertEqual(self.view()['source_records'], original_records)
            self.assertEqual(RELATIONS in self.view()['ready'], value == old)
            b.plan_cap = self.cap; self.assertEqual(b.status()['charged_total'], 2)
        from hub.task_storage_restore import _snapshot
        _snapshot(self.store, self.store.path.read_bytes())

    def test_twelve_accepted_definitions_unchanged_after_source_policy_cas(self):
        rows = [fixtures.task(i) for i in sorted(BOOTSTRAP_IDS - {ROUTES})[:12]]
        old = fixtures.plan(rows + [fixtures.task(GRAPH, [ROUTES]), fixtures.task(ROUTES)])
        old['revision'] = '1.1.0-original'
        for t in old['tasks']:
            if t['id'] == 'P2-R2-PCH-DAG': t['dispatch_priority'] = 20
        self.p = old; self.configure(); other = TaskStore(self.store.root)
        # Separate plan identity in the same existing SQLite.
        self.p['plan_id'] = 'TWELVE-PLAN'; old = copy.deepcopy(self.p); self.configure()
        other.import_plan(old, authority=self.cap)
        for t in rows:
            r = self.f.evidence(t['id']); r['plan_sha256'] = digest(old)
            self.records[t['id']] = r; self.configure(); other.record_plan_evidence(old['plan_id'], r, authority=self.cap)
        before = {t['id']: copy.deepcopy(t) for t in rows}
        target = copy.deepcopy(old); target['revision'] = '1.2.0-upgrade'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission'] = self.policy([])
        self.p = target; self.configure(prior=old)
        other.reconcile_plan(target, expected_sha256=digest(old), request_id='adopt-source', authority=self.cap)
        self.assertEqual(len(other.plan_ready(target['plan_id'], authority=self.cap)['accepted']), 12)
        self.assertEqual({t['id']: t for t in target['tasks'] if t['id'] in before}, before)

    def test_offline_restore_retains_source_without_renewing_callbacks(self):
        self.record(self.candidate()); original = self.view()['source_records']
        from hub.task_storage_restore import _snapshot
        raw = self.store.path.read_bytes(); _snapshot(self.store, raw)
        cold = self.store.root.parent / 'cold-source'; cold.mkdir(mode=0o700)
        dest = TaskStore(cold); dest.path.parent.mkdir(mode=0o700, parents=True)
        shutil.copy2(self.store.path, dest.path); shutil.copy2(self.store.registration_path, dest.registration_path)
        h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        dest.restore_storage(database_sha256=h(dest.path), registration_sha256=h(dest.registration_path), confirm_offline=True)
        with dest._connection() as db:
            value = dest._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
        self.assertEqual(value['records'], original)
        from hub.source_admission import source_ready
        self.assertEqual(source_ready(self.p, {}, set(), original, lambda _: False, lambda _: False), [])
        with self.assertRaises(ServiceError): dest.plan_source_ready(self.p['plan_id'], authority=self.cap)
        # Content hash recomputation cannot legalize a forged source reference.
        with sqlite3.connect(':memory:') as db:
            db.deserialize(raw); value['records'][0]['record']['candidate_sha256'] = 'not-a-hash'
            db.execute('UPDATE plan_inputs SET body=?,hash=?', (canonical(value).decode(), digest(value))); db.commit()
            with self.assertRaises(ServiceError): _snapshot(self.store, db.serialize())

    def test_source_record_does_not_occupy_real_package_singleton(self):
        import test_hub_rebuild_contracts as contracts
        f = contracts.RebuildContractsTests(); f.setUp(); self.addCleanup(f.doCleanups)
        self.store = f.store
        for t in self.p['tasks']:
            if 'source_admission' in t:
                t['source_admission']['workspace']['canonical_root'] = str(self.store.root)
        self.configure(); self.store.import_plan(self.p, authority=self.cap)
        self.record(self.candidate()); self.assertEqual(self.store.tasks(), [])
        p = copy.deepcopy(f.package); p['task_id'] = GRAPH; p['lineage']['original_task_id'] = GRAPH
        p['phase_id'] = 'P3'; p['round_id'] = 'P3-R1'
        f.package = p; f.configure(); self.store.prepare_rebuild(p, authority=f.authority)
        self.assertEqual(self.store.rebuild_package(GRAPH), p)
        self.assertNotIn(GRAPH, self.view()['ready'])

    def test_completed_record_cannot_lend_through_unknown_producer(self):
        self.record(self.candidate())
        self.record(self.candidate(RELATIONS, request='relations', candidate='e'))
        self.record(self.candidate(request='unknown-producer', status='unknown'))
        self.assertNotIn(RELATIONS, self.view()['ready'])
        self.assertEqual(self.view()['accepted'], [])

    def test_accepted_dependencies_require_real_applicable_whole_evidence(self):
        old = copy.deepcopy(self.p)
        target = copy.deepcopy(old); target['revision'] = '1.2.0-accepted-input'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission']['dependencies'] = [{'task_id': BASE, 'kind': 'accepted'}]
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='accepted-input', authority=self.cap)
        self.assertNotIn(GRAPH, self.view()['ready'])
        r = self.f.evidence(BASE); r['plan_sha256'] = digest(self.p)
        self.records[BASE] = r; self.configure(); self.store.record_plan_evidence(self.p['plan_id'], r, authority=self.cap)
        self.assertIn(GRAPH, self.view()['ready'])
        self.assertEqual(self.view()['accepted'], [BASE])

    def test_cancelled_source_and_input_drift_cannot_lend_candidate(self):
        self.record(self.candidate(status='cancelled'))
        self.assertEqual(self.view()['ready'], [])
        old = copy.deepcopy(self.p); target = copy.deepcopy(old); target['revision'] = '1.2.0-input-change'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission']['inputs'][0]['sha256'] = 'f' * 64
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='input-change', authority=self.cap)
        self.assertNotIn(RELATIONS, self.view()['ready'])

    def test_unknown_commit_is_queryable_without_duplicate_record(self):
        r = self.candidate(); expected = self.view()['source_sha256']
        def fault(phase):
            if phase == 'after_commit': raise OSError('synthetic lost acknowledgement')
        self.store.fault = fault
        with self.assertRaises(ServiceError) as err: self.record(r, expected)
        self.assertEqual(err.exception.outcome, 'UNKNOWN')
        self.store.fault = lambda _: None
        self.assertEqual(self.view()['source_records'][0]['record'], r)
        self.assertTrue(self.record(r, expected)['replayed'])
        self.assertEqual(len(self.view()['source_records']), 1)

    def test_dependency_verification_revoked_before_commit_rolls_back(self):
        producer = self.candidate(); self.record(producer)
        consumer = self.candidate(RELATIONS, request='consumer', candidate='e')
        before = self.store.path.read_bytes()
        def fault(phase):
            if phase == 'before_plan_source_commit': self.verified.remove(digest(producer))
        self.store.fault = fault
        with self.assertRaises(ServiceError): self.record(consumer)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unknown_survives_source_definition_and_revision_change(self):
        old_unknown = self.candidate(status='unknown'); self.record(old_unknown)
        self.assertEqual(self.view()['ready'], [])
        old = copy.deepcopy(self.p); target = copy.deepcopy(old); target['revision'] = '1.2.0-new-input'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission']['inputs'][0]['sha256'] = 'f' * 64
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='new-input', authority=self.cap)
        self.assertEqual(self.view()['ready'], [])
        self.assertEqual(self.view()['source_records'][0]['record'], old_unknown)

    def resolution(self, unknown_ids, *, ident=GRAPH, request='resolved'):
        r = self.candidate(ident, request=request)
        r['reconciles'] = list(unknown_ids)
        self.verified.add(digest(r))
        return r

    def test_explicit_verified_resolution_after_input_change_retains_unknown(self):
        original = self.candidate(status='unknown'); self.record(original)
        old = copy.deepcopy(self.p); target = copy.deepcopy(old); target['revision'] = '1.2.0-reviewed-input'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission']['inputs'][0]['sha256'] = 'f' * 64
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='reviewed-input', authority=self.cap)
        with self.assertRaises(ServiceError): self.record(self.candidate(request='not-reconciled'))
        r = self.resolution([original['request_id']]); expected = self.view()['source_sha256']
        self.record(r, expected)
        self.assertIn(GRAPH, self.view()['ready']); self.assertIn(RELATIONS, self.view()['ready'])
        self.assertTrue(self.record(r, expected)['replayed'])
        self.assertEqual(self.view()['source_records'][0]['record'], original)
        self.assertEqual(self.view()['source_records'][1]['record'], r)
        self.assertEqual(self.store.tasks(), []); self.assertEqual(self.view()['accepted'], [])
        from hub.task_storage_restore import _snapshot
        _snapshot(self.store, self.store.path.read_bytes())

    def test_missing_wrong_foreign_duplicate_and_unverified_reconciliation_reject(self):
        self.record(self.candidate())
        foreign = self.candidate(RELATIONS, request='foreign-unknown', status='unknown'); self.record(foreign)
        own = self.candidate(request='own-unknown', status='unknown'); self.record(own)
        before = self.store.path.read_bytes()
        for refs in ([], ['missing'], [foreign['request_id']], [own['request_id'], own['request_id']],
                     [own['request_id'], foreign['request_id']]):
            with self.subTest(refs=refs), self.assertRaises(ServiceError):
                self.record(self.resolution(refs))
        r = self.resolution([own['request_id']]); self.verified.remove(digest(r))
        with self.assertRaises(ServiceError): self.record(r)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.verified.add(digest(r)); self.record(r)
        self.assertIn(GRAPH, self.view()['ready'])
        self.assertNotIn(RELATIONS, self.view()['ready'])  # Other task's UNKNOWN remains.

    def test_historical_multiple_unknowns_need_complete_exact_set(self):
        # Old readers could record more than one UNKNOWN across definition changes.
        # Preserve that valid historical content and require all same-task witnesses.
        from hub.source_admission import split, wrap
        first = self.candidate(request='first-unknown', status='unknown'); self.record(first)
        second = self.candidate(request='second-unknown', status='unknown')
        with self.store._connection(True) as db:
            raw = self.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
            history, rows = split(raw)
            rows.append({'expected_source_sha256': digest(rows), 'record': second})
            body = wrap(history, rows); plan_history(body)
            db.execute('UPDATE plan_inputs SET body=?,hash=?', (canonical(body).decode(), digest(body)))
        self.assertEqual(self.view()['ready'], [])
        for refs in ([first['request_id']], [second['request_id']]):
            with self.subTest(refs=refs), self.assertRaises(ServiceError): self.record(self.resolution(refs))
        self.record(self.resolution([second['request_id'], first['request_id']]))
        self.assertIn(RELATIONS, self.view()['ready'])
        self.assertEqual([r['record'] for r in self.view()['source_records'][:2]], [first, second])

    def test_resolution_revocation_refreezes_across_rollback(self):
        unknown = self.candidate(status='unknown'); self.record(unknown)
        r = self.resolution([unknown['request_id']]); self.record(r)
        self.assertIn(RELATIONS, self.view()['ready'])
        for witness in (unknown, r):
            self.verified.remove(digest(witness))
            self.assertEqual(self.view()['ready'], [])
            self.verified.add(digest(witness))
        old = copy.deepcopy(self.p); target = copy.deepcopy(old); target['revision'] = '1.2.0-after-resolution'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission']['inputs'][0]['sha256'] = 'e' * 64
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='resolved-new-definition', authority=self.cap)
        self.verified.remove(digest(r)); self.assertEqual(self.view()['ready'], [])
        self.p = old; self.configure(prior=target)
        self.store.reconcile_plan(old, expected_sha256=digest(target), request_id='resolved-back', authority=self.cap, rollback=True)
        self.assertEqual(self.view()['ready'], [])

    def test_resolution_commit_rechecks_unknown_witness_control_and_dependencies(self):
        unknown = self.candidate(status='unknown'); self.record(unknown)
        r = self.resolution([unknown['request_id']]); before = self.store.path.read_bytes()
        def revoke(phase):
            if phase == 'before_plan_source_commit': self.verified.remove(digest(unknown))
        self.store.fault = revoke
        with self.assertRaises(ServiceError): self.record(r)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.verified.add(digest(unknown))
        for state in ('paused', 'cancelled'):
            self.control['state'] = state
            with self.assertRaises(ServiceError): self.record(r)
        self.control['state'] = 'active'
        def changed(phase):
            if phase == 'before_plan_source_commit': self.control['revision'] += 1
        self.store.fault = changed
        with self.assertRaises(ServiceError): self.record(r)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.fault = lambda _: None
        old = copy.deepcopy(self.p); target = copy.deepcopy(old); target['revision'] = '1.2.0-unmet-dependency'
        next(t for t in target['tasks'] if t['id'] == GRAPH)['source_admission']['dependencies'] = [{'task_id': BASE, 'kind': 'accepted'}]
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='new-required-input', authority=self.cap)
        with self.assertRaises(ServiceError): self.record(self.resolution([unknown['request_id']]))
        self.assertEqual(len(self.view()['source_records']), 1)

    def test_changed_strategy_cannot_skip_real_unknown_dependency_or_cancel(self):
        self.record(self.candidate())
        self.record(self.candidate(RELATIONS, request='consumer', candidate='e'))
        self.record(self.candidate(request='unknown-producer', status='unknown'))
        old = copy.deepcopy(self.p); target = copy.deepcopy(old); target['revision'] = '1.2.0-skip-source-edge'
        next(t for t in target['tasks'] if t['id'] == RELATIONS)['source_admission']['dependencies'] = []
        self.p = target; self.configure(prior=old)
        self.store.reconcile_plan(target, expected_sha256=digest(old), request_id='skip-source-edge', authority=self.cap)
        self.assertEqual(self.view()['ready'], [])
        # Resolving an unknown cannot override a distinct owner cancellation.
        from hub.source_admission import source_ready
        cancel = self.candidate(status='cancelled', request='owner-cancel')
        rows = self.view()['source_records'] + [{'record': cancel}]
        resolution = self.resolution(['unknown-producer'])
        self.assertEqual(source_ready(self.p, {}, set(), rows, lambda _: True,
                                     lambda _: True, reconciliation=resolution), [])


class PersonalStyleSourceAdmissionTests(unittest.TestCase):
    """Only the selected original ID; trusted callbacks are synthetic, not a grant."""
    ident = 'P5-personal-style-hub-01'
    onboard = 'P1-R1-PCH-ONBOARD'
    dag = 'P2-R2-PCH-DAG'
    definition = SourceAdmissionTests.definition
    policy = SourceAdmissionTests.policy
    candidate = SourceAdmissionTests.candidate
    view = SourceAdmissionTests.view
    record = SourceAdmissionTests.record

    def setUp(self):
        f = fixtures.PlanStoreTests(); f.setUp(); self.addCleanup(f.doCleanups)
        self.f = f; self.store = f.store; self.control = f.control
        self.records = {}; self.verified = set(); self.develop = True
        foreign = self.store.root.parent / 'synthetic-personal-style-hub'
        delivery = self.store.root.parent / 'synthetic-style-delivery'
        foreign.mkdir(); delivery.mkdir()
        self.workspace = {'canonical_root': str(foreign), 'delivery_root': str(delivery),
                          'native_thread_id': 'synthetic-psh-native-thread'}
        dag = fixtures.task(self.dag); dag['dispatch_priority'] = 20
        self.p = fixtures.plan([fixtures.task(BASE), dag, fixtures.task(ROUTES),
                               fixtures.task(self.onboard, [BASE]),
                               fixtures.task(self.ident, [self.onboard])])
        self.p['revision'] = '1.2.0-synthetic-psh'
        self.definition(self.ident)['source_admission'] = self.policy([
            {'task_id': BASE, 'kind': 'accepted'}, {'task_id': self.dag, 'kind': 'accepted'}])
        selected = self.definition(self.ident)
        selected['project_id'] = 'personal-style-hub'
        selected['owner_route'] = 'route:personal-style-hub'
        selected['source_admission']['workspace'] = copy.deepcopy(self.workspace)
        self.registrations = {self.ident: {'project_id': 'personal-style-hub',
                                        'workspace': copy.deepcopy(self.workspace)}}
        if getattr(self, 'include_pch', False):
            self.p['tasks'].extend([fixtures.task(GRAPH, [ROUTES]), fixtures.task(RELATIONS, [self.ident])])
            self.definition(GRAPH)['source_admission'] = self.policy([])
            self.definition(RELATIONS)['source_admission'] = self.policy([])
        if getattr(self, 'include_graph_source', False):
            graph_source = fixtures.task('P5-personal-style-hub-02', [self.ident, GRAPH])
            graph_source.update(project_id='personal-style-hub', owner_route='route:personal-style-hub')
            graph_source['source_admission'] = self.policy([
                {'task_id': self.ident, 'kind': 'source_candidate', 'candidate_sha256': 'a' * 64},
                {'task_id': GRAPH, 'kind': 'source_candidate', 'candidate_sha256': 'b' * 64}])
            graph_source['source_admission']['workspace'] = copy.deepcopy(self.workspace)
            self.p['tasks'].append(graph_source)
            self.registrations[graph_source['id']] = copy.deepcopy(self.registrations[self.ident])
        if getattr(self, 'include_actions_source', False):
            actions = fixtures.task('P3-R3-PCH-ACTIONS', [ROUTES])
            actions['source_admission'] = self.policy([])
            self.p['tasks'].extend([fixtures.task('P2-R1-PCH-ADAPTER'), actions])
            selected = fixtures.task('P5-personal-style-hub-03',
                                     [self.ident, 'P2-R1-PCH-ADAPTER', actions['id']])
            selected.update(project_id='personal-style-hub', owner_route='route:personal-style-hub')
            selected['source_admission'] = self.policy([
                {'task_id': self.ident, 'kind': 'source_candidate', 'candidate_sha256': 'a' * 64},
                {'task_id': actions['id'], 'kind': 'source_candidate', 'candidate_sha256': 'c' * 64}])
            selected['source_admission']['workspace'] = copy.deepcopy(self.workspace)
            declared = []
            (foreign / 'inputs').mkdir()
            for n in range(35):
                path = foreign / 'inputs' / ('synthetic-' + str(n) + '.txt')
                path.write_bytes(('Synthetic source03 input ' + str(n)).encode())
                declared.append({'path': path.relative_to(foreign).as_posix(),
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            selected['source_admission']['inputs'] = declared
            selected['source_admission']['owned_paths'] = ['src/synthetic-action-' + str(n) + '.py' for n in range(6)]
            self.actions_policy = copy.deepcopy(selected['source_admission'])
            self.actions_commit = '1' * 40; self.actions_observed_commit = self.actions_commit
            self.actions_enabled = True
            self.p['tasks'].append(selected)
            self.registrations[selected['id']] = copy.deepcopy(self.registrations[self.ident])
        self.configure(); self.store.import_plan(self.p, authority=self.cap)

    def actions_development(self, definition):
        # A synthetic internal authority callback, not a public permission path
        # or the real private verifier. Exercise actual input/alias drift here.
        if (not self.actions_enabled or self.actions_observed_commit != self.actions_commit
                or definition['source_admission'] != self.actions_policy): return False
        root = Path(self.workspace['canonical_root'])
        for item in self.actions_policy['inputs']:
            path = root
            for part in Path(item['path']).parts:
                path = path / part
                if path.is_symlink(): return False
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']: return False
        return True

    def configure(self, *, prior=None):
        binding = {'plan_sha256': digest(self.p), 'owner_context': 'synthetic-owner',
                   'verified_evidence': {k: digest(v) for k, v in self.records.items()},
                   'verified_evidence_versions': {k: {v['plan_sha256']: digest(v)} for k, v in self.records.items()},
                   'source_workspaces': self.registrations}
        if prior: binding['reconcile_from_sha256'] = digest(prior)
        self.cap = PlanAuthority.configured(binding, authorize=lambda: None,
            controls=lambda: copy.deepcopy(self.control), routes=lambda _: False, package_for=lambda _: None,
            development=lambda t: self.develop if t['id'] == self.ident else
                getattr(self, 'graph_develop', True) if t['id'] == 'P5-personal-style-hub-02' else
                self.actions_development(t) if t['id'] == 'P5-personal-style-hub-03' else True,
            source_evidence_verification=lambda r: digest(r) in self.verified)

    def shared(self):
        f = PersonalStyleSourceAdmissionTests(); f.include_pch = True
        f.setUp(); self.addCleanup(f.doCleanups); f.accept_dependencies()
        f.record(f.candidate(GRAPH, request='pch-existing'))
        f.record(f.candidate(f.ident, request='psh-existing'))
        return f

    def accept_dependencies(self):
        for ident in (BASE, self.dag):
            r = self.f.evidence(ident); r['plan_sha256'] = digest(self.p)
            self.records[ident] = r; self.configure()
            self.store.record_plan_evidence(self.p['plan_id'], r, authority=self.cap)

    def assert_execution_still_closed(self):
        view = self.store.plan_ready(self.p['plan_id'], authority=self.cap)
        self.assertNotIn(self.ident, view['ready'])
        self.assertNotIn(self.onboard, view['accepted'])
        self.assertNotIn(ROUTES, view['accepted'])
        self.assertEqual(self.store.admit_plan_window(self.p['plan_id'], authority=self.cap)['admitted'], [])
        self.assertEqual(self.store.tasks(), [])
        self.assertEqual(set(view['accepted']), {BASE, self.dag})

    def test_exact_id_source_ready_requires_accepted_inputs_not_normal_onboarding(self):
        original_ids = frozenset('''P3-R1-PCH-GRAPH P3-R1-PCH-RELATIONS P3-R2-PCH-OVERVIEW P3-R2-PCH-FOCUS P3-R3-PCH-ACTIONS P3-R3-PCH-IMPACT P3-R3-PCH-PORTS P6-R1-PCH-PRECISE-SAVE P6-R1-PCH-SEMANTIC-UI P6-R2-PCH-ARTIFACTS P6-R2-PCH-VERSION'''.split())
        self.assertEqual(SOURCE_IDS, original_ids | {self.ident, 'P5-personal-style-hub-02', 'P5-personal-style-hub-03'})
        self.assertNotIn(self.ident, BOOTSTRAP_IDS)
        for foreign in ('P5-personal-style-hub-04', 'P5-other-project-02'):
            p = copy.deepcopy(self.p); self_task = next(t for t in p['tasks'] if t['id'] == self.ident)
            self_task['id'] = foreign
            with self.subTest(foreign=foreign), self.assertRaises(ServiceError): validate_plan(p)
        self.assertEqual(self.view()['ready'], [])
        # One accepted input is insufficient: all explicit source dependencies hold.
        r = self.f.evidence(BASE); r['plan_sha256'] = digest(self.p)
        self.records[BASE] = r; self.configure()
        self.store.record_plan_evidence(self.p['plan_id'], r, authority=self.cap)
        self.assertEqual(self.view()['ready'], [])
        self.accept_dependencies()
        self.assertEqual(self.view()['ready'], [self.ident])
        r = self.candidate(self.ident, request='psh-source')
        self.record(r)
        self.assert_execution_still_closed()
        with self.store._connection() as db:
            self.assertEqual(db.execute('SELECT count(*)FROM plan_receipts').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*)FROM plan_conflicts').fetchone()[0], 0)
        self.assertEqual(self.definition(self.ident)['depends_on'], [self.onboard])
        self.assertEqual(self.view()['source_records'][0]['record'], r)

    def test_wrong_workspace_inputs_owned_and_request_cas_refuse_without_effects(self):
        self.accept_dependencies(); before = self.store.path.read_bytes()
        mutations = [lambda r: r['workspace'].update(canonical_root='/synthetic/foreign'),
                     lambda r: r['workspace'].update(delivery_root='/synthetic/foreign-delivery'),
                     lambda r: r['workspace'].update(native_thread_id='foreign-thread'),
                     lambda r: r['inputs'][0].update(sha256='f' * 64),
                     lambda r: r['inputs'][0].update(path='other.json'),
                     lambda r: r.update(owned_paths=['src/unowned.py'])]
        for n, mutate in enumerate(mutations):
            r = self.candidate(self.ident, request='psh-invalid-' + str(n)); mutate(r)
            self.verified.add(digest(r)) # Serialized evidence cannot override exact policy binding.
            with self.subTest(n=n), self.assertRaises(ServiceError): self.record(r)
            self.assertEqual(self.store.path.read_bytes(), before)
        r = self.candidate(self.ident, request='psh-valid'); expected = self.view()['source_sha256']
        self.record(r, expected); committed = self.store.path.read_bytes()
        self.assertTrue(self.record(r, expected)['replayed'])
        with self.assertRaises(ServiceError) as error:
            self.record(self.candidate(self.ident, request='psh-valid', candidate='e'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_REQUEST_CONFLICT')
        with self.assertRaises(ServiceError) as error:
            self.record(self.candidate(self.ident, request='psh-stale'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_CAS_STALE')
        self.assertEqual(self.store.path.read_bytes(), committed)
        self.assert_execution_still_closed()

    def test_live_permission_revocation_and_unknown_never_open_execution_or_acceptance(self):
        self.accept_dependencies(); self.develop = False
        self.assertEqual(self.view()['ready'], [])
        with self.assertRaises(ServiceError): self.record(self.candidate(self.ident))
        self.develop = True; r = self.candidate(self.ident, request='psh-observed'); self.record(r)
        self.verified.remove(digest(r)); self.assertEqual(self.view()['ready'], [])
        self.assert_execution_still_closed()
        self.verified.add(digest(r))
        unknown = self.candidate(self.ident, request='psh-unknown', status='unknown'); self.record(unknown)
        self.assertEqual(self.view()['ready'], [])
        before = self.store.path.read_bytes()
        with self.assertRaises(ServiceError): self.record(self.candidate(self.ident, request='psh-no-reconcile'))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual([entry['record'] for entry in self.view()['source_records']], [r, unknown])
        self.assert_execution_still_closed()

    def test_pause_control_drift_and_cancel_remain_authoritative_for_exact_id(self):
        self.accept_dependencies(); r = self.candidate(self.ident, request='psh-control')
        before = self.store.path.read_bytes(); self.control['state'] = 'paused'
        self.assertEqual(self.view()['ready'], [])
        with self.assertRaises(ServiceError): self.record(r)
        self.control['state'] = 'active'
        def drift(phase):
            if phase == 'before_plan_source_commit': self.control['revision'] += 1
        self.store.fault = drift
        with self.assertRaises(ServiceError) as error: self.record(r)
        self.assertEqual(error.exception.code, 'PLAN_CONTROL_CHANGED')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.fault = lambda _: None
        cancel = self.candidate(self.ident, request='psh-cancelled', status='cancelled'); self.record(cancel)
        self.assertEqual(self.view()['ready'], [])
        with self.assertRaises(ServiceError): self.record(r)
        self.assertEqual(self.view()['source_records'][0]['record'], cancel)
        self.assert_execution_still_closed()

    def test_foreign_workspace_registration_is_exact_and_missing_binding_is_local(self):
        f = self.shared(); before = f.store.path.read_bytes(); original = copy.deepcopy(f.registrations)
        self.assertNotEqual(f.workspace['canonical_root'], str(f.store.root))
        self.assertEqual(f.definition(f.ident)['project_id'], 'personal-style-hub')
        self.assertTrue(f.cap.registered_source_workspace(f.definition(f.ident)))
        kept = f.view()['source_records']
        mutations = [lambda m: m.clear(),
                     lambda m: m[f.ident].update(project_id='synthetic'),
                     lambda m: m[f.ident]['workspace'].update(canonical_root='/synthetic/wrong-root'),
                     lambda m: m[f.ident]['workspace'].update(delivery_root='/synthetic/wrong-delivery'),
                     lambda m: m[f.ident]['workspace'].update(native_thread_id='wrong-native-thread'),
                     lambda m: m[f.ident]['workspace'].update(canonical_root='/tmp/../alias'),
                     lambda m: m[f.ident].update(extra=True)]
        for n, mutate in enumerate(mutations):
            f.registrations = copy.deepcopy(original); mutate(f.registrations); f.configure()
            with self.subTest(n=n):
                self.assertFalse(f.cap.registered_source_workspace(f.definition(f.ident)))
                self.assertEqual(f.view()['ready'], [GRAPH])
                with self.assertRaises(ServiceError): f.record(f.candidate(f.ident, request='psh-unbound-' + str(n)))
                self.assertEqual(f.store.path.read_bytes(), before)
                self.assertEqual(f.view()['source_records'], kept)
                f.assert_execution_still_closed()
        f.registrations = original; f.configure(); snapshot = f.cap.snapshot()
        snapshot['source_workspaces'][f.ident]['workspace']['canonical_root'] = '/synthetic/changed-copy'
        self.assertTrue(f.cap.registered_source_workspace(f.definition(f.ident)))
        for ident in (f.ident, GRAPH, RELATIONS): self.assertIn(ident, f.view()['ready'])

    def test_scoped_development_or_evidence_revocation_does_not_poison_pch(self):
        f = self.shared(); kept = f.view()['source_records']
        f.develop = False
        self.assertTrue(f.cap.registered_source_workspace(f.definition(f.ident)))
        self.assertEqual(f.view()['ready'], [GRAPH])
        f.record(f.candidate(GRAPH, request='pch-while-psh-disabled', candidate='e'))
        f.develop = True
        record = next(e['record'] for e in kept if e['record']['task_id'] == f.ident)
        f.verified.remove(digest(record))
        self.assertEqual(f.view()['ready'], [GRAPH])
        self.assertEqual(f.view()['source_records'][:2], kept)
        f.assert_execution_still_closed()
        f.verified.add(digest(record))
        self.assertIn(f.ident, f.view()['ready'])
        f.registrations = {}; f.configure()
        f.record(f.candidate(GRAPH, request='pch-while-psh-unregistered', candidate='f'))
        self.assertEqual(f.view()['ready'], [GRAPH])

    def test_wrong_project_or_same_root_guess_cannot_register_foreign_policy(self):
        for mutation in ('project', 'same_root'):
            f = self.shared(); old = copy.deepcopy(f.p)
            f.p = copy.deepcopy(old); f.p['revision'] += '-' + mutation
            selected = f.definition(f.ident)
            if mutation == 'project': selected['project_id'] = 'synthetic'
            else:
                selected['source_admission']['workspace']['canonical_root'] = str(f.store.root)
                f.registrations[f.ident]['workspace']['canonical_root'] = str(f.store.root)
            f.configure(prior=old)
            f.store.reconcile_plan(f.p, expected_sha256=digest(old), request_id='synthetic-' + mutation, authority=f.cap)
            with self.subTest(mutation=mutation):
                self.assertEqual(f.view()['ready'], [GRAPH])
                before = f.store.path.read_bytes()
                with self.assertRaises(ServiceError): f.record(f.candidate(f.ident, request='psh-refused'))
                self.assertEqual(f.store.path.read_bytes(), before)
                f.assert_execution_still_closed()
        f = self.shared(); old = copy.deepcopy(f.p); f.p = copy.deepcopy(old); f.p['revision'] += '-foreign-pch'
        f.definition(GRAPH)['source_admission']['workspace'] = copy.deepcopy(f.workspace)
        f.registrations[GRAPH] = {'project_id': 'synthetic', 'workspace': copy.deepcopy(f.workspace)}
        f.configure(prior=old)
        f.store.reconcile_plan(f.p, expected_sha256=digest(old), request_id='synthetic-foreign-pch', authority=f.cap)
        self.assertFalse(f.cap.registered_source_workspace(f.definition(GRAPH)))
        with self.assertRaises(ServiceError) as error: f.view()
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_WORKSPACE_MISMATCH')

    def test_foreign_unknown_cancel_and_commit_revocation_keep_pch_and_history(self):
        for status in ('unknown', 'cancelled'):
            f = self.shared(); before = f.view()['source_records']
            observed = f.candidate(f.ident, request='psh-' + status, status=status); f.record(observed)
            with self.subTest(status=status):
                self.assertEqual(f.view()['ready'], [GRAPH])
                self.assertEqual(f.view()['source_records'][:2], before)
                self.assertEqual(f.view()['source_records'][-1]['record'], observed)
                f.record(f.candidate(GRAPH, request='pch-after-' + status, candidate='e'))
                f.assert_execution_still_closed()
        f = self.shared(); proposed = f.candidate(f.ident, request='psh-commit-revoked')
        before = f.store.path.read_bytes()
        def revoke(phase):
            if phase == 'before_plan_source_commit': f.develop = False
        f.store.fault = revoke
        with self.assertRaises(ServiceError): f.record(proposed)
        self.assertEqual(f.store.path.read_bytes(), before)
        self.assertEqual(f.view()['ready'], [GRAPH])
        f.assert_execution_still_closed()


class PersonalStyleGraphSourceAdmissionTests(unittest.TestCase):
    """Task02 reuses a workspace, never task01's registration or source receipt."""
    ident = 'P5-personal-style-hub-02'

    def fixture(self):
        f = PersonalStyleSourceAdmissionTests()
        f.include_pch = True; f.include_graph_source = True
        f.setUp(); self.addCleanup(f.doCleanups); f.accept_dependencies()
        self.assertNotEqual(f.workspace['canonical_root'], str(f.store.root))
        return f

    def seed(self, f):
        first = f.candidate(f.ident, request='psh01-implemented', candidate='a'); f.record(first)
        graph = f.candidate(GRAPH, request='pch-graph-implemented', candidate='b'); f.record(graph)
        return first, graph

    def assert_execution_closed(self, f):
        view = f.store.plan_ready(f.p['plan_id'], authority=f.cap)
        self.assertEqual(set(view['accepted']), {BASE, f.dag})
        for ident in (f.ident, self.ident, GRAPH, f.onboard, ROUTES): self.assertNotIn(ident, view['accepted'])
        self.assertNotIn(self.ident, view['ready'])
        self.assertEqual(f.store.admit_plan_window(f.p['plan_id'], authority=f.cap)['admitted'], [])
        self.assertEqual(f.store.tasks(), [])

    def test_02_needs_both_exact_applicable_source_candidates_not_registration_alone(self):
        f = self.fixture(); definition = f.definition(self.ident)
        self.assertEqual(definition['project_id'], 'personal-style-hub')
        self.assertEqual(definition['depends_on'], [f.ident, GRAPH])
        self.assertNotIn(self.ident, BOOTSTRAP_IDS)
        self.assertTrue(f.cap.registered_source_workspace(definition))
        self.assertNotIn(self.ident, f.view()['ready'])
        first = f.candidate(f.ident, request='source01'); f.record(first)
        self.assertNotIn(self.ident, f.view()['ready'])
        wrong_graph = f.candidate(GRAPH, request='wrong-graph-version', candidate='c'); f.record(wrong_graph)
        self.assertNotIn(self.ident, f.view()['ready'])
        graph = f.candidate(GRAPH, request='exact-graph-version', candidate='b'); f.record(graph)
        self.assertIn(self.ident, f.view()['ready'])
        kept = f.view()['source_records']; before_accepted = f.view()['accepted']
        own = f.candidate(self.ident, request='psh02-source', candidate='e'); expected = f.view()['source_sha256']
        f.record(own, expected); committed = f.store.path.read_bytes()
        self.assertTrue(f.record(own, expected)['replayed'])
        self.assertEqual(f.store.path.read_bytes(), committed)
        self.assertEqual(f.view()['source_records'][:-1], kept)
        self.assertEqual(f.view()['source_records'][-1]['record'], own)
        self.assertEqual(f.view()['accepted'], before_accepted)
        self.assert_execution_closed(f)
        for invalid in ('P5-personal-style-hub-04', 'P5-other-project-02'):
            p = copy.deepcopy(f.p); next(t for t in p['tasks'] if t['id'] == self.ident)['id'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ServiceError): validate_plan(p)

    def test_02_explicit_registration_and_wrong_identity_cannot_borrow_01(self):
        f = self.fixture(); self.seed(f); kept = f.view()['source_records']; before = f.store.path.read_bytes()
        valid = copy.deepcopy(f.registrations)
        mutations = [lambda r: r.pop(self.ident),
                     lambda r: r[self.ident].update(project_id='other-project'),
                     lambda r: r[self.ident]['workspace'].update(canonical_root='/synthetic/other-root'),
                     lambda r: r[self.ident]['workspace'].update(delivery_root='/synthetic/other-delivery'),
                     lambda r: r[self.ident]['workspace'].update(native_thread_id='other-native-thread')]
        for n, mutate in enumerate(mutations):
            f.registrations = copy.deepcopy(valid); mutate(f.registrations); f.configure()
            with self.subTest(n=n):
                self.assertFalse(f.cap.registered_source_workspace(f.definition(self.ident)))
                self.assertTrue(f.cap.registered_source_workspace(f.definition(f.ident)))
                view = f.view()
                self.assertNotIn(self.ident, view['ready']); self.assertIn(f.ident, view['ready']); self.assertIn(GRAPH, view['ready'])
                with self.assertRaises(ServiceError): f.record(f.candidate(self.ident, request='psh02-invalid-' + str(n)))
                self.assertEqual(f.store.path.read_bytes(), before)
                self.assertEqual(f.view()['source_records'], kept)
                self.assert_execution_closed(f)
        f.registrations = valid; f.configure(); f.graph_develop = False
        self.assertTrue(f.cap.registered_source_workspace(f.definition(self.ident)))
        self.assertNotIn(self.ident, f.view()['ready']); self.assertIn(f.ident, f.view()['ready']); self.assertIn(GRAPH, f.view()['ready'])
        with self.assertRaises(ServiceError): f.record(f.candidate(self.ident, request='psh02-disabled'))

    def test_02_producer_revocation_unknown_and_cancel_block_only_actual_dependents(self):
        for producer in ('P5-personal-style-hub-01', GRAPH):
            for status in ('unknown', 'cancelled'):
                f = self.fixture(); self.seed(f); kept = f.view()['source_records']
                f.record(f.candidate(self.ident, request='psh02-existing', candidate='e'))
                observed = f.candidate(producer, request='producer-' + status, status=status); f.record(observed)
                with self.subTest(producer=producer, status=status):
                    self.assertNotIn(self.ident, f.view()['ready'])
                    unrelated = GRAPH if producer == f.ident else f.ident
                    self.assertIn(unrelated, f.view()['ready'])
                    self.assertEqual(f.view()['source_records'][:2], kept)
                    self.assertEqual(f.view()['source_records'][-1]['record'], observed)
                    before = f.store.path.read_bytes()
                    with self.assertRaises(ServiceError): f.record(f.candidate(self.ident, request='psh02-no-proof'))
                    self.assertEqual(f.store.path.read_bytes(), before)
                    self.assert_execution_closed(f)
        f = self.fixture(); first, graph = self.seed(f)
        f.verified.remove(digest(first))
        self.assertNotIn(self.ident, f.view()['ready']); self.assertIn(GRAPH, f.view()['ready'])
        f.verified.add(digest(first)); f.verified.remove(digest(graph))
        self.assertNotIn(self.ident, f.view()['ready']); self.assertIn(f.ident, f.view()['ready'])

    def test_02_own_unknown_cancel_cas_and_commit_guards_preserve_pch_history(self):
        for status in ('unknown', 'cancelled'):
            f = self.fixture(); self.seed(f); kept = f.view()['source_records']
            f.record(f.candidate(self.ident, request='psh02-' + status, status=status))
            with self.subTest(status=status):
                self.assertNotIn(self.ident, f.view()['ready']); self.assertIn(f.ident, f.view()['ready']); self.assertIn(GRAPH, f.view()['ready'])
                self.assertEqual(f.view()['source_records'][:2], kept)
                self.assert_execution_closed(f)
        for revoked in ('capability', 'dependency', 'control'):
            f = self.fixture(); _, graph = self.seed(f)
            proposed = f.candidate(self.ident, request='psh02-precommit'); before = f.store.path.read_bytes()
            def fault(phase):
                if phase != 'before_plan_source_commit': return
                if revoked == 'capability': f.graph_develop = False
                elif revoked == 'dependency': f.verified.remove(digest(graph))
                else: f.control['revision'] += 1
            f.store.fault = fault
            with self.subTest(revoked=revoked), self.assertRaises(ServiceError): f.record(proposed)
            self.assertEqual(f.store.path.read_bytes(), before)
            self.assert_execution_closed(f)
        f = self.fixture(); self.seed(f); expected = f.view()['source_sha256']
        first = f.candidate(self.ident, request='psh02-cas', candidate='e'); f.record(first, expected)
        before = f.store.path.read_bytes()
        with self.assertRaises(ServiceError) as error: f.record(f.candidate(self.ident, request='psh02-cas', candidate='f'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_REQUEST_CONFLICT')
        with self.assertRaises(ServiceError) as error: f.record(f.candidate(self.ident, request='psh02-stale'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_CAS_STALE')
        self.assertEqual(f.store.path.read_bytes(), before)
        self.assert_execution_closed(f)


class PersonalStyleActionsSourceAdmissionTests(unittest.TestCase):
    """Source03 registration and evidence do not authorize a business action."""
    ident = 'P5-personal-style-hub-03'
    actions = 'P3-R3-PCH-ACTIONS'
    assert_execution_closed = PersonalStyleGraphSourceAdmissionTests.assert_execution_closed

    def fixture(self):
        f = PersonalStyleSourceAdmissionTests()
        f.include_pch = True; f.include_graph_source = True; f.include_actions_source = True
        f.setUp(); self.addCleanup(f.doCleanups); f.accept_dependencies()
        self.assertNotEqual(f.workspace['canonical_root'], str(f.store.root))
        return f

    def seed(self, f):
        records = []
        for ident, candidate in ((f.ident, 'a'), (GRAPH, 'b'), (self.actions, 'c'),
                                 ('P5-personal-style-hub-02', 'd')):
            r = f.candidate(ident, request='existing-' + ident.lower(), candidate=candidate); f.record(r); records.append(r)
        return records

    def assert_unrelated_ready(self, f):
        ready = f.view()['ready']
        self.assertNotIn(self.ident, ready)
        for ident in (f.ident, 'P5-personal-style-hub-02', GRAPH, self.actions): self.assertIn(ident, ready)

    def test_03_exact_candidates_and_registered_policy_do_not_create_normal_execution(self):
        f = self.fixture(); definition = f.definition(self.ident); policy = definition['source_admission']
        self.assertEqual(definition['project_id'], 'personal-style-hub')
        self.assertEqual(definition['depends_on'], [f.ident, 'P2-R1-PCH-ADAPTER', self.actions])
        self.assertEqual(len(policy['inputs']), 35); self.assertEqual(len(policy['owned_paths']), 6)
        self.assertNotIn(self.ident, BOOTSTRAP_IDS)
        self.assertTrue(f.cap.registered_source_workspace(definition))
        self.assertNotIn(self.ident, f.view()['ready'])
        first = f.candidate(f.ident, request='source01-exact', candidate='a'); f.record(first)
        self.assertNotIn(self.ident, f.view()['ready'])
        f.record(f.candidate(self.actions, request='actions-wrong-candidate', candidate='b'))
        self.assertNotIn(self.ident, f.view()['ready'])
        exact = f.candidate(self.actions, request='actions-exact-candidate', candidate='c'); f.record(exact)
        self.assertIn(self.ident, f.view()['ready'])
        kept = f.view()['source_records']; expected = f.view()['source_sha256']
        own = f.candidate(self.ident, request='source03', candidate='e'); f.record(own, expected)
        before = f.store.path.read_bytes()
        self.assertTrue(f.record(own, expected)['replayed']); self.assertEqual(f.store.path.read_bytes(), before)
        self.assertEqual(f.view()['source_records'][:-1], kept)
        self.assertEqual(f.view()['source_records'][-1]['record'], own)
        self.assert_execution_closed(f)
        for invalid in ('P5-personal-style-hub-04', 'P5-other-project-03'):
            p = copy.deepcopy(f.p); next(t for t in p['tasks'] if t['id'] == self.ident)['id'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ServiceError): validate_plan(p)
        wrong = copy.deepcopy(definition); wrong['project_id'] = 'other-project'
        self.assertFalse(f.cap.registered_source_workspace(wrong))

    def test_03_registration_and_record_scope_cannot_borrow_01_or_02(self):
        f = self.fixture(); self.seed(f); kept = f.view()['source_records']; before = f.store.path.read_bytes()
        original = copy.deepcopy(f.registrations)
        mutations = [lambda m: m.pop(self.ident),
                     lambda m: m[self.ident].update(project_id='other-project'),
                     lambda m: m[self.ident]['workspace'].update(canonical_root='/synthetic/foreign-root'),
                     lambda m: m[self.ident]['workspace'].update(delivery_root='/synthetic/foreign-delivery'),
                     lambda m: m[self.ident]['workspace'].update(native_thread_id='foreign-native-thread')]
        for n, mutate in enumerate(mutations):
            f.registrations = copy.deepcopy(original); mutate(f.registrations); f.configure()
            with self.subTest(n=n):
                self.assertFalse(f.cap.registered_source_workspace(f.definition(self.ident)))
                for ident in (f.ident, 'P5-personal-style-hub-02'):
                    self.assertTrue(f.cap.registered_source_workspace(f.definition(ident)))
                self.assert_unrelated_ready(f)
                with self.assertRaises(ServiceError): f.record(f.candidate(self.ident, request='source03-unregistered-' + str(n)))
                self.assertEqual(f.store.path.read_bytes(), before); self.assertEqual(f.view()['source_records'], kept)
        f.registrations = original; f.configure()
        for field in ('input', 'owned', 'workspace'):
            r = f.candidate(self.ident, request='source03-wrong-' + field)
            if field == 'input': r['inputs'][0]['sha256'] = 'f' * 64
            elif field == 'owned': r['owned_paths'][0] = 'src/unowned.py'
            else: r['workspace']['native_thread_id'] = 'foreign-thread'
            f.verified.add(digest(r))
            with self.subTest(field=field), self.assertRaises(ServiceError): f.record(r)
            self.assertEqual(f.store.path.read_bytes(), before)
        self.assert_execution_closed(f)

    def test_03_live_input_commit_alias_and_capability_refusals_are_local(self):
        for reason in ('input', 'commit', 'symlink', 'revocation'):
            f = self.fixture(); self.seed(f); kept = f.view()['source_records']; before = f.store.path.read_bytes()
            path = Path(f.workspace['canonical_root']) / f.actions_policy['inputs'][0]['path']
            if reason == 'input': path.write_bytes(b'Changed synthetic declared input')
            elif reason == 'commit': f.actions_observed_commit = '2' * 40
            elif reason == 'symlink':
                target = path.with_name('same-bytes-alias-target.txt'); target.write_bytes(path.read_bytes())
                path.unlink(); path.symlink_to(target)
            else: f.actions_enabled = False
            with self.subTest(reason=reason):
                self.assertTrue(f.cap.registered_source_workspace(f.definition(self.ident)))
                self.assertFalse(f.cap.development(f.definition(self.ident)))
                self.assert_unrelated_ready(f)
                with self.assertRaises(ServiceError): f.record(f.candidate(self.ident, request='source03-refused-' + reason))
                self.assertEqual(f.store.path.read_bytes(), before); self.assertEqual(f.view()['source_records'], kept)
                self.assert_execution_closed(f)

    def test_03_unknown_cancel_cas_and_precommit_guards_preserve_existing_records(self):
        for ident, status in ((self.ident, 'unknown'), (self.ident, 'cancelled'),
                              ('P5-personal-style-hub-01', 'unknown'), (self.actions, 'cancelled')):
            f = self.fixture(); self.seed(f); kept = f.view()['source_records']
            f.record(f.candidate(self.ident, request='source03-existing', candidate='e'))
            r = f.candidate(ident, request='source-observed-' + status, status=status); f.record(r)
            with self.subTest(ident=ident, status=status):
                self.assertNotIn(self.ident, f.view()['ready'])
                self.assertEqual(f.view()['source_records'][:4], kept)
                if ident == self.ident: self.assert_unrelated_ready(f)
                elif ident == f.ident: self.assertIn(self.actions, f.view()['ready'])
                else: self.assertIn(f.ident, f.view()['ready']); self.assertIn('P5-personal-style-hub-02', f.view()['ready'])
                self.assert_execution_closed(f)
        for reason in ('capability', 'dependency', 'control'):
            f = self.fixture(); records = self.seed(f)
            proposed = f.candidate(self.ident, request='source03-precommit'); before = f.store.path.read_bytes()
            def fault(phase):
                if phase != 'before_plan_source_commit': return
                if reason == 'capability': f.actions_enabled = False
                elif reason == 'dependency': f.verified.remove(digest(records[2]))
                else: f.control['revision'] += 1
            f.store.fault = fault
            with self.subTest(reason=reason), self.assertRaises(ServiceError): f.record(proposed)
            self.assertEqual(f.store.path.read_bytes(), before)
            self.assert_execution_closed(f)
        f = self.fixture(); self.seed(f); expected = f.view()['source_sha256']
        r = f.candidate(self.ident, request='source03-cas', candidate='e'); f.record(r, expected); before = f.store.path.read_bytes()
        with self.assertRaises(ServiceError) as error: f.record(f.candidate(self.ident, request='source03-cas', candidate='f'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_REQUEST_CONFLICT')
        with self.assertRaises(ServiceError) as error: f.record(f.candidate(self.ident, request='source03-stale'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_CAS_STALE'); self.assertEqual(f.store.path.read_bytes(), before)
        self.assert_execution_closed(f)
