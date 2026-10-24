"""Synthetic original-project registrations confer source permission only."""
import copy
import hashlib
from pathlib import Path
import unittest

import test_hub_plan_admission as fixtures
import test_hub_source_admission as sources
from hub.plan_admission import PlanAuthority, validate_plan
from hub.service_contract import ServiceError
from hub.source_admission import is_registered_project_source_task
from hub.workbench_store import digest


class RegisteredProjectSourceTests(unittest.TestCase):
    ident = 'P5-synthetic-studio-01'
    child = 'P5-synthetic-studio-02'
    peer = 'P5-synthetic-peer-01'
    onboard = 'P1-R1-PCH-ONBOARD'
    definition = sources.SourceAdmissionTests.definition
    candidate = sources.SourceAdmissionTests.candidate
    view = sources.SourceAdmissionTests.view
    record = sources.SourceAdmissionTests.record

    def setUp(self):
        f = fixtures.PlanStoreTests(); f.setUp(); self.addCleanup(f.doCleanups)
        self.f = f; self.store = f.store; self.control = f.control
        self.records = {}; self.verified = set(); self.disabled = set(); self.calls = []
        self.authorized = True; self.source_verified = True
        self.input_bytes = b'Synthetic declared implementation input\n'
        self.input_sha = hashlib.sha256(self.input_bytes).hexdigest()
        self.workspaces = {}
        for project in ('synthetic-studio', 'synthetic-peer'):
            root = self.store.root.parent / project
            delivery = self.store.root.parent / (project + '-delivery')
            root.mkdir(); delivery.mkdir()
            (root / 'schema.json').write_bytes(self.input_bytes)
            self.workspaces[project] = {'canonical_root': str(root), 'delivery_root': str(delivery),
                                        'native_thread_id': project + '-native-thread'}
        (self.store.root / 'schema.json').write_bytes(self.input_bytes)
        self.p = fixtures.plan([fixtures.task(sources.BASE), fixtures.task(sources.ROUTES),
            fixtures.task(self.onboard, [sources.BASE]),
            fixtures.task(sources.GRAPH, [sources.ROUTES]),
            fixtures.task(self.ident, [self.onboard]), fixtures.task(self.child, [self.ident]),
            fixtures.task(sources.RELATIONS, [self.ident]), fixtures.task(self.peer, [self.onboard])])
        self.p['revision'] = '1.2.0-synthetic-registered'
        for ident in (sources.GRAPH, sources.RELATIONS):
            self.definition(ident)['source_admission'] = self.policy([])
        self.registrations = {}
        for ident, project, deps in (
                (self.ident, 'synthetic-studio', [
                    {'kind': 'accepted', 'task_id': sources.BASE},
                    {'kind': 'source_candidate', 'task_id': sources.GRAPH, 'candidate_sha256': 'a' * 64}]),
                (self.child, 'synthetic-studio', [
                    {'kind': 'source_candidate', 'task_id': self.ident, 'candidate_sha256': 'b' * 64}]),
                (self.peer, 'synthetic-peer', [])):
            t = self.definition(ident)
            t.update(project_id=project, owner_route='route:' + project,
                     admission_class='verified_project_execution')
            t['source_admission'] = self.policy(deps)
            t['source_admission']['workspace'] = copy.deepcopy(self.workspaces[project])
            self.registrations[ident] = {
                'project_id': project, 'workspace': copy.deepcopy(self.workspaces[project]),
                'original_identity': {k: t[k] for k in
                    ('id', 'project_id', 'phase_id', 'admission_class', 'owner_route')}}
        self.expected_policies = {t['id']: copy.deepcopy(t['source_admission'])
                                  for t in self.p['tasks'] if 'source_admission' in t}
        self.configure(); self.store.import_plan(self.p, authority=self.cap)

    def policy(self, deps):
        p = sources.SourceAdmissionTests.policy(self, deps)
        p['inputs'][0]['sha256'] = self.input_sha
        return p

    def authorize(self):
        if not self.authorized:
            raise ServiceError('SYNTHETIC_AUTHORITY_REVOKED', status=409)

    def development(self, definition):
        # Synthetic trusted host callback: the public registration must precede
        # this check, and it cannot replace the live declared-input checks.
        ident = definition['id']; self.calls.append(ident)
        p = definition.get('source_admission')
        if ident in self.disabled or p != self.expected_policies.get(ident):
            return False
        for root in (p['workspace']['canonical_root'], p['workspace']['delivery_root']):
            if Path(root).is_symlink(): return False
        for item in p['inputs']:
            path = Path(p['workspace']['canonical_root'])
            for part in Path(item['path']).parts:
                path /= part
                if path.is_symlink(): return False
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                return False
        return True

    def configure(self, *, prior=None, callback=None):
        binding = {'plan_sha256': digest(self.p), 'owner_context': 'synthetic-owner',
            'source_workspaces': self.registrations,
            'verified_evidence': {k: digest(v) for k, v in self.records.items()},
            'verified_evidence_versions': {k: {v['plan_sha256']: digest(v)} for k, v in self.records.items()}}
        if prior is not None: binding['reconcile_from_sha256'] = digest(prior)
        self.cap = PlanAuthority.configured(binding, authorize=self.authorize,
            controls=lambda: copy.deepcopy(self.control), routes=lambda _: False, package_for=lambda _: None,
            development=self.development if callback is None else callback,
            source_evidence_verification=lambda r: self.source_verified and digest(r) in self.verified)

    def prime(self):
        r = self.f.evidence(sources.BASE); r['plan_sha256'] = digest(self.p)
        self.records[sources.BASE] = r; self.configure()
        self.store.record_plan_evidence(self.p['plan_id'], r, authority=self.cap)
        self.record(self.candidate(sources.GRAPH, request='graph-source'))

    def assert_closed(self):
        view = self.store.plan_ready(self.p['plan_id'], authority=self.cap)
        self.assertNotIn(self.ident, view['ready'])
        self.assertNotIn(sources.ROUTES, view['accepted'])
        self.assertEqual(self.store.admit_plan_window(self.p['plan_id'], authority=self.cap)['admitted'], [])
        self.assertEqual(self.store.tasks(), [])
        self.assertFalse(self.view()['execution_authority'])
        self.assertFalse(self.view()['whole_task_acceptance'])

    def test_structure_is_exact_not_project_or_route_inference(self):
        t = self.definition(self.ident)
        self.assertTrue(is_registered_project_source_task(t)); validate_plan(self.p)
        mutations = ({'phase_id': 'P1'}, {'admission_class': 'routed'},
            {'project_id': 'different-project'}, {'owner_route': 'route:other'},
            {'id': 'P5-synthetic-studio-00'}, {'id': 'P5-synthetic-studio-1'},
            {'id': 'P5-synthetic-studio-010'}, {'id': 'P5-synthetic-studio-01-extra'})
        for change in mutations:
            changed = copy.deepcopy(t); changed.update(change)
            with self.subTest(change=change):
                self.assertFalse(is_registered_project_source_task(changed))
                p = copy.deepcopy(self.p); p['tasks'][p['tasks'].index(t)] = changed
                with self.assertRaises(ServiceError): validate_plan(p)
        # Project characters are literal, never interpolated as a regex wildcard.
        changed = copy.deepcopy(t); changed.update(project_id='synthetic.studio',
            owner_route='route:synthetic.studio', id='P5-syntheticXstudio-01')
        self.assertFalse(is_registered_project_source_task(changed))
        changed['id'] = 'P5-synthetic.studio-99'
        self.assertTrue(is_registered_project_source_task(changed))

    def test_registered_original_ready_only_after_real_source_dependencies(self):
        self.assertTrue(self.cap.registered_source_workspace(self.definition(self.ident)))
        self.assertNotIn(self.ident, self.view()['ready'])
        self.prime(); self.assertIn(self.ident, self.view()['ready'])
        self.assertNotIn(self.child, self.view()['ready'])
        r = self.candidate(self.ident, request='studio-source', candidate='b'); self.record(r)
        self.assertIn(self.child, self.view()['ready'])
        self.assertEqual(self.view()['accepted'], [sources.BASE])
        self.assertEqual(self.view()['source_records'][-1]['record'], r)
        self.assert_closed()

    def test_missing_malformed_or_copied_original_registration_is_local(self):
        self.prime(); original = copy.deepcopy(self.registrations)
        before = self.store.path.read_bytes(); calls = []
        mutations = [lambda m: m.pop(self.ident),
            lambda m: m[self.ident].pop('original_identity'),
            lambda m: m[self.ident].update(original_identity=None),
            lambda m: m[self.ident]['original_identity'].update(extra='not-identity'),
            lambda m: m[self.ident].update(original_identity=copy.deepcopy(m[self.peer]['original_identity'])),
            lambda m: m[self.ident].update(project_id='synthetic-peer'),
            lambda m: m[self.ident].update(extra=True)]
        for key, value in (('id', self.child), ('project_id', 'other'), ('phase_id', 'P1'),
                           ('admission_class', 'routed'), ('owner_route', 'route:other')):
            mutations.append(lambda m, k=key, v=value: m[self.ident]['original_identity'].update({k: v}))
        for n, mutate in enumerate(mutations):
            self.registrations = copy.deepcopy(original); mutate(self.registrations)
            self.configure(callback=lambda t: calls.append(t['id']) or True)
            calls.clear()
            with self.subTest(n=n):
                self.assertFalse(self.cap.development(self.definition(self.ident)))
                self.assertNotIn(self.ident, calls)
                ready = self.view()['ready']
                for ident in (self.ident, self.child, sources.RELATIONS): self.assertNotIn(ident, ready)
                self.assertIn(sources.GRAPH, ready); self.assertIn(self.peer, ready)
                with self.assertRaises(ServiceError):
                    self.record(self.candidate(self.ident, request='unregistered-' + str(n)))
                self.assertEqual(self.store.path.read_bytes(), before)

    def test_workspace_root_delivery_thread_and_sibling_scope_are_exact(self):
        self.prime(); original = copy.deepcopy(self.registrations)
        before = self.store.path.read_bytes()
        for field, value in (('canonical_root', '/synthetic/wrong-root'),
                ('delivery_root', '/synthetic/wrong-delivery'), ('native_thread_id', 'wrong-thread'),
                ('canonical_root', '/synthetic/../alias'), ('native_thread_id', None)):
            self.registrations = copy.deepcopy(original)
            self.registrations[self.ident]['workspace'][field] = value; self.configure()
            with self.subTest(field=field, value=value):
                self.assertFalse(self.cap.registered_source_workspace(self.definition(self.ident)))
                self.assertNotIn(self.ident, self.view()['ready'])
                with self.assertRaises(ServiceError): self.record(self.candidate(self.ident))
                self.assertEqual(self.store.path.read_bytes(), before)
        self.registrations = {self.peer: original[self.peer], self.child: original[self.ident]}
        self.configure()
        self.assertFalse(self.cap.registered_source_workspace(self.definition(self.child)))
        self.assertNotIn(self.ident, self.view()['ready'])
        self.assertIn(self.peer, self.view()['ready'])

    def test_registration_cannot_borrow_pch_root_or_occupy_unadopted_id(self):
        self.prime(); original = copy.deepcopy(self.p)
        r = self.candidate(self.ident); r['task_id'] = 'P5-synthetic-studio-03'
        self.verified.add(digest(r)); before = self.store.path.read_bytes()
        with self.assertRaises(ServiceError): self.record(r)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.p['revision'] = '1.2.0-borrowed-root'
        p = self.definition(self.ident)['source_admission']
        p['workspace']['canonical_root'] = str(self.store.root)
        self.registrations[self.ident]['workspace'] = copy.deepcopy(p['workspace'])
        self.expected_policies[self.ident] = copy.deepcopy(p)
        self.configure(prior=original)
        self.store.reconcile_plan(self.p, expected_sha256=digest(original), request_id='borrow-root', authority=self.cap)
        self.assertTrue(self.cap.registered_source_workspace(self.definition(self.ident)))
        self.assertNotIn(self.ident, self.view()['ready'])
        self.assertIn(self.peer, self.view()['ready']); self.assert_closed()

    def test_live_callback_input_symlink_and_evidence_revocation_freeze_dependents(self):
        self.prime(); r = self.candidate(self.ident, candidate='b'); self.record(r)
        kept = copy.deepcopy(self.view()['source_records']); before = self.store.path.read_bytes()
        self.disabled.add(self.ident)
        self.assertNotIn(self.child, self.view()['ready']); self.assertIn(self.peer, self.view()['ready'])
        self.disabled.clear(); self.verified.remove(digest(r))
        self.assertNotIn(self.child, self.view()['ready'])
        self.verified.add(digest(r))
        path = Path(self.workspaces['synthetic-studio']['canonical_root']) / 'schema.json'
        path.write_bytes(b'Changed synthetic input')
        self.assertNotIn(self.ident, self.view()['ready']); self.assertNotIn(self.child, self.view()['ready'])
        path.unlink(); path.symlink_to(Path(self.workspaces['synthetic-peer']['canonical_root']) / 'schema.json')
        self.assertNotIn(self.ident, self.view()['ready'])
        path.unlink(); path.write_bytes(self.input_bytes)
        self.assertIn(self.child, self.view()['ready'])
        self.assertEqual(self.view()['source_records'], kept)
        self.assertEqual(self.store.path.read_bytes(), before); self.assert_closed()

    def test_record_exact_scope_cas_replay_conflict_and_stale_policy(self):
        self.prime(); expected = self.view()['source_sha256']; before = self.store.path.read_bytes()
        for n, field in enumerate(('workspace', 'inputs', 'owned_paths')):
            r = self.candidate(self.ident, request='bad-scope-' + str(n))
            if field == 'workspace': r[field]['native_thread_id'] = 'wrong-thread'
            elif field == 'inputs': r[field][0]['sha256'] = 'f' * 64
            else: r[field] = ['src/unowned.py']
            self.verified.add(digest(r))
            with self.subTest(field=field), self.assertRaises(ServiceError): self.record(r, expected)
            self.assertEqual(self.store.path.read_bytes(), before)
        r = self.candidate(self.ident, request='exact-record', candidate='b'); self.record(r, expected)
        committed = self.store.path.read_bytes()
        self.assertTrue(self.record(r, expected)['replayed'])
        with self.assertRaises(ServiceError) as error:
            self.record(self.candidate(self.ident, request='exact-record', candidate='e'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_REQUEST_CONFLICT')
        with self.assertRaises(ServiceError) as error:
            self.record(self.candidate(self.ident, request='stale-record'), expected)
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_CAS_STALE')
        self.assertEqual(self.store.path.read_bytes(), committed)
        old = copy.deepcopy(self.p); self.p['revision'] = '1.2.0-changed-input'
        self.definition(self.ident)['source_admission']['inputs'][0]['sha256'] = 'f' * 64
        self.configure(prior=old)
        self.store.reconcile_plan(self.p, expected_sha256=digest(old), request_id='new-input', authority=self.cap)
        self.assertNotIn(self.ident, self.view()['ready']); self.assertNotIn(self.child, self.view()['ready'])
        with self.assertRaises(ServiceError): self.record(r)
        self.assertEqual(self.view()['source_records'][-1]['record'], r)

    def test_unknown_and_cancelled_records_cannot_lend_source_or_acceptance(self):
        self.prime(); unknown = self.candidate(self.ident, request='uncertain', status='unknown')
        self.record(unknown); before = self.store.path.read_bytes()
        self.assertNotIn(self.ident, self.view()['ready']); self.assertNotIn(self.child, self.view()['ready'])
        self.assertIn(self.peer, self.view()['ready'])
        with self.assertRaises(ServiceError): self.record(self.candidate(self.ident, request='no-reconcile'))
        self.assertEqual(self.store.path.read_bytes(), before)
        cancel = self.candidate(self.peer, request='peer-cancel', status='cancelled'); self.record(cancel)
        self.assertNotIn(self.peer, self.view()['ready'])
        self.assertEqual([e['record'] for e in self.view()['source_records']][-2:], [unknown, cancel])
        self.assert_closed()

    def test_commit_rechecks_registration_authority_dependencies_and_control(self):
        self.prime(); r = self.candidate(self.ident, request='guarded'); before = self.store.path.read_bytes()
        registration = copy.deepcopy(self.registrations[self.ident])
        for change in ('registration', 'development', 'dependency', 'authority', 'control'):
            self.registrations[self.ident] = copy.deepcopy(registration)
            self.disabled.clear(); self.source_verified = True; self.authorized = True
            def fault(phase):
                if phase != 'before_plan_source_commit': return
                if change == 'registration':
                    # Internal registration is immutable within this authority;
                    # trusted live callback revocation still fences commit.
                    self.disabled.add(self.ident)
                if change == 'development': self.disabled.add(self.ident)
                if change == 'dependency': self.source_verified = False
                if change == 'authority': self.authorized = False
                if change == 'control': self.control['revision'] += 1
            self.store.fault = fault
            with self.subTest(change=change), self.assertRaises(ServiceError): self.record(r)
            self.authorized = True
            self.assertEqual(self.store.path.read_bytes(), before)
        self.store.fault = lambda _: None
        self.disabled.clear(); self.source_verified = True; self.configure()
        for state in ('paused', 'cancelled'):
            self.control['state'] = state
            self.assertEqual(self.view()['ready'], [])
            with self.assertRaises(ServiceError): self.record(r)
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_existing_pch_foreign_root_still_rejects(self):
        old = copy.deepcopy(self.p); self.p['revision'] = '1.2.0-wrong-pch-root'
        self.definition(sources.GRAPH)['source_admission']['workspace'] = copy.deepcopy(self.workspaces['synthetic-studio'])
        self.configure(prior=old)
        self.store.reconcile_plan(self.p, expected_sha256=digest(old), request_id='wrong-pch-root', authority=self.cap)
        with self.assertRaises(ServiceError) as error: self.view()
        self.assertEqual(error.exception.code, 'PLAN_SOURCE_WORKSPACE_MISMATCH')


if __name__ == '__main__':
    unittest.main()
