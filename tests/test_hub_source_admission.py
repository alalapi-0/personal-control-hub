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
