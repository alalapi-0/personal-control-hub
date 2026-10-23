"""Real isolated HTTP/config/ledger acceptance for owner management overlays."""
import copy
import http.client
import json
import os
import secrets
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from connection_fixtures import Fixture
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_records import validate_result
from hub.connection_sources import SourceResolver
from hub.local_service import HubHTTPServer
from hub.management_store import ManagementStore
from hub.owner_auth import OwnerAuth
from hub.project_service import ProjectService
from hub.service_contract import ServiceError


def overlay(**changes):
    value = {'priority': 2, 'start_date': '2026-10-14', 'end_date': '2026-10-20',
             'watch': True, 'order': 1, 'layout': {'x': 32, 'y': 16}, 'relations': None}
    value.update(changes)
    return value


def relations(parent=None, depends=(), related=(), independent=None):
    return {'belongs_to': parent, 'depends_on': list(depends),
            'related_to': list(related), 'independent': independent}


class ManagementAPITests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.a, _ = self.fixture.add('a')
        self.fixture.add('b')
        self.addCleanup(self.fixture.close)
        self.config = self.fixture.hub / 'data/connections/management_config.yaml'
        self.config.parent.mkdir(parents=True)
        self.base_config = yaml.safe_load((Path(__file__).resolve().parents[1] /
            'data/connections/management_config.yaml').read_text())
        self.config.write_text(yaml.safe_dump(self.base_config, allow_unicode=True, sort_keys=False))
        self.service = ProjectService(self.fixture.hub)
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        refresh(self.ledger, SourceResolver(self.fixture.hub), 'seed-source', ['a', 'b'])
        self.proof = secrets.token_urlsafe(40)
        self.server = HubHTTPServer(self.service, None,
            owner_auth=OwnerAuth(provider=lambda: self.proof))
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.cookie = self.csrf = None

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def call(self, method='GET', path='/api/management?project_id=a', body=None, headers=None):
        supplied = {'Cookie': self.cookie} if self.cookie else {}
        if method == 'POST':
            supplied.update({'Origin': self.server.origin, 'X-Hub-CSRF': self.csrf,
                             'Content-Type': 'application/json'})
        supplied.update(headers or {})
        supplied = {k: v for k, v in supplied.items() if v is not None}
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None, supplied)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def session(self, owner=False):
        status, headers, result = self.call(path='/api/session')
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = result['data']['csrf_token']
        if owner:
            status, headers, result = self.call('POST', '/api/owner/login', {'token': self.proof})
            self.assertEqual(status, 200)
            self.cookie = headers['Set-Cookie'].split(';')[0]
            self.csrf = result['data']['csrf_token']

    def command(self, request='manage-one', project='a', value=None, operation='set'):
        state = self.service.get_management(project)
        return {'request_id': request, 'expected_revision': state['revision'],
                'expected_hash': state['config_hash'], 'project_id': project,
                'operation': operation, 'value': overlay() if value is None and operation == 'set' else value}

    def post(self, command):
        return self.call('POST', '/api/management/project', command)

    def test_guest_read_and_owner_csrf_guards_preserve_file(self):
        before = self.config.read_bytes()
        self.assertEqual(self.call()[0], 401)
        self.session()
        status, _, response = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(response['data']['relation_state'], 'unconfigured')
        status, _, guest = self.post(self.command())
        self.assertEqual((status, guest['error']['code']), (401, 'OWNER_AUTH_REQUIRED'))
        self.session(owner=True)
        for attack in ({'X-Hub-CSRF': None}, {'X-Hub-CSRF': 'wrong'}, {'Origin': 'https://evil.invalid'}):
            with self.subTest(attack=attack):
                status, _, result = self.call('POST', '/api/management/project', self.command(), attack)
                self.assertEqual(status, 403)
                self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
        self.assertEqual(self.config.read_bytes(), before)

    def test_unconfigured_owner_cannot_save(self):
        self.proof = None
        self.session()
        before = self.config.read_bytes()
        status, _, result = self.post(self.command())
        self.assertEqual((status, result['error']['code']), (503, 'OWNER_AUTH_UNAVAILABLE'))
        self.assertEqual(self.config.read_bytes(), before)

    def test_success_restart_upgrade_preserves_base_and_business(self):
        self.session(owner=True)
        before = self.service.get_project('a')['business']
        self.assertEqual(before['fields']['progress'], self.fixture.business['progress'])
        head = self.service.ledger_head()
        source = (self.a / 'STATE.yaml').read_bytes()
        value = overlay(relations=relations(depends=['b'], independent=True))
        status, _, result = self.post(self.command(value=value))
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['receipt']['committed_revision'], 1)
        restarted = ProjectService(self.fixture.hub)
        self.assertEqual(restarted.get_management('a')['overlay'], value)
        self.assertEqual(restarted.get_management('a')['relation_state'], 'owner_confirmed')
        stored = yaml.safe_load(self.config.read_text())
        self.assertEqual(stored['schema_version'], '1.1')
        for key, original in self.base_config.items():
            if key not in {'schema_version', 'projects'}:
                self.assertEqual(stored[key], original, key)
        self.assertEqual(restarted.get_project('a')['business'], before)
        self.assertEqual(self.service.ledger_head(), head)
        self.assertEqual((self.a / 'STATE.yaml').read_bytes(), source)

    def test_expired_cas_and_logical_retry_do_not_append(self):
        self.session(owner=True)
        first = self.command()
        stale = self.command(request='stale-two')
        self.assertEqual(self.post(first)[0], 200)
        status, _, result = self.post(stale)
        self.assertEqual((status, result['error']['code']), (409, 'MANAGEMENT_CONFLICT'))
        self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
        second = self.command('manage-two', 'b')
        self.assertEqual(self.post(second)[0], 200)
        before = self.config.read_bytes()
        retry = dict(first, expected_revision=2, expected_hash=self.service.get_management('a')['config_hash'])
        status, _, result = self.post(retry)
        self.assertEqual(status, 200)
        receipt = result['data']['receipt']
        self.assertTrue(receipt['replayed'])
        self.assertEqual((receipt['committed_revision'], receipt['revision']), (1, 2))
        self.assertEqual(self.config.read_bytes(), before)
        conflict = copy.deepcopy(retry)
        conflict['value']['priority'] = 9
        self.assertEqual(self.post(conflict)[0], 409)
        self.assertEqual(self.config.read_bytes(), before)

    def test_revocation_and_rotation_immediately_before_commit_abort(self):
        for mode in ('revoke', 'rotate'):
            with self.subTest(mode=mode):
                self.proof = secrets.token_urlsafe(40)
                self.cookie = self.csrf = None
                self.session(owner=True)
                before = self.config.read_bytes()
                def fault(store, stage):
                    if stage == 'before_replace':
                        if mode == 'revoke':
                            self.server.sessions.revoke(self.cookie)
                        else:
                            self.proof = secrets.token_urlsafe(40)
                # Inject at the real store's last precommit boundary; disk IO and
                # HTTP/session authorization remain real.
                original_init = ManagementStore.__init__
                def initialize(store, root, **kwargs):
                    original_init(store, root, fault=lambda stage: fault(store, stage))
                with mock.patch.object(ManagementStore, '__init__', initialize):
                    status, _, result = self.post(self.command(request='revoked-' + mode))
                self.assertEqual(status, 401)
                self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(list(self.config.parent.glob('.management-*')), [])

    def test_source_import_preserves_owner_overlay(self):
        self.session(owner=True)
        value = overlay(priority=7)
        self.assertEqual(self.post(self.command(value=value))[0], 200)
        config = self.config.read_bytes()
        business = copy.deepcopy(self.fixture.business)
        business['current_work']['next_action'] = 'New source action'
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(business))
        refresh(self.ledger, SourceResolver(self.fixture.hub), 'changed-source', ['a'])
        current = self.service.get_project('a')['business']['fields']
        self.assertEqual(current['progress'], business['progress'])
        self.assertEqual(current['current_work']['next_action'], 'New source action')
        self.assertEqual(self.service.get_management('a')['overlay'], value)
        self.assertEqual(self.config.read_bytes(), config)

    def test_rejected_management_config_does_not_hide_source_project(self):
        self.server.designs = mock.Mock()
        self.server.designs.snapshot.return_value = {'available': True, 'reason': None,
            'store_revision': 0, 'store_classification': 'synthetic_fixture',
            'facts': [], 'history': [], 'effective': {}, 'queues': {}}
        self.session()
        self.assertEqual(self.call(path='/api/projects/a')[0], 200)
        before = self.service.get_project('a')['business']
        self.config.write_text('schema_version: invalid\n')
        status, _, result = self.call(path='/api/projects/a')
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['business'], before)
        self.assertFalse(result['data']['management']['available'])
        self.assertEqual(result['data']['management']['reason'], 'MANAGEMENT_CONFIG_REJECTED')
        self.assertEqual(self.call()[0], 503)

    def test_belonging_cycle_rejected_without_committing(self):
        self.session(owner=True)
        self.assertEqual(self.post(self.command(value=overlay(relations=relations(parent='b'))))[0], 200)
        before = self.config.read_bytes()
        status, _, result = self.post(self.command('cycle-two', 'b', overlay(relations=relations(parent='a'))))
        self.assertEqual(status, 409)
        self.assertEqual(result['error']['code'], 'MANAGEMENT_RELATION_CONFLICT')
        self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
        self.assertEqual(self.config.read_bytes(), before)

    def test_dependency_cycle_and_independence_are_explicit_warnings(self):
        self.session(owner=True)
        a = overlay(relations=relations(depends=['b'], related=['b'], independent=True))
        b = overlay(relations=relations(depends=['a'], related=['a']))
        self.assertEqual(self.post(self.command(value=a))[0], 200)
        self.assertEqual(self.post(self.command('dependency-two', 'b', b))[0], 200)
        view = self.service.get_management('a')
        self.assertIn('PROJECT_DEPENDENCY_CYCLE', view['warnings'])
        self.assertTrue(view['overlay']['relations']['independent'])
        self.assertEqual(view['overlay']['relations']['depends_on'], ['b'])

    def test_removed_and_cloud_metadata_never_probe_external_sources(self):
        self.session(owner=True)
        for index, mode in enumerate(('removed', 'cloud')):
            with self.subTest(mode=mode):
                row = self.fixture.projects[index]
                if mode == 'removed':
                    row['local_presence'] = {'status': 'removed_local'}
                else:
                    row['project_type'] = 'cloud'
        self.fixture.save_registry()
        before = self.config.read_bytes()
        external = [Path(row['root_path']) for row in self.fixture.projects]
        real_open, real_stat, real_read = os.open, Path.stat, Path.read_bytes
        def reject_external(path):
            if isinstance(path, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(path)).absolute()
                if any(candidate == root or root in candidate.parents for root in external):
                    raise AssertionError('external source root probe')
        def guarded_open(path, *args, **kwargs):
            reject_external(path)
            return real_open(path, *args, **kwargs)
        def guarded_stat(path, *args, **kwargs):
            reject_external(path)
            return real_stat(path, *args, **kwargs)
        def guarded_read(path):
            reject_external(path)
            return real_read(path)
        with mock.patch('hub.connection_sources.SourceResolver.refresh', side_effect=AssertionError('external source probe')), \
             mock.patch('os.open', guarded_open), mock.patch.object(Path, 'stat', guarded_stat), \
             mock.patch.object(Path, 'read_bytes', guarded_read):
            for project in ('a', 'b'):
                status, _, result = self.call(path='/api/management?project_id=' + project)
                self.assertEqual(status, 200)
                self.assertFalse(result['data']['editable'])
                self.assertEqual(result['data']['project_options'], [])
                self.assertEqual(self.post(self.command(request='deny-' + project, project=project))[0], 400)
        self.assertEqual(self.config.read_bytes(), before)

    def test_postcommit_view_failure_returns_successful_receipt(self):
        self.session(owner=True)
        command = self.command()
        with mock.patch.object(self.service, 'get_management', side_effect=ServiceError('MANAGEMENT_CONFIG_REJECTED', status=503)):
            status, _, result = self.post(command)
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['receipt']['committed_revision'], 1)
        self.assertIsNone(result['data']['view'])
        self.assertEqual(result['data']['view_error']['code'], 'MANAGEMENT_CONFIG_REJECTED')
        self.assertEqual(self.service.get_management('a')['revision'], 1)

    def test_retract_preserves_history_and_restores_unconfigured(self):
        self.session(owner=True)
        self.assertEqual(self.post(self.command())[0], 200)
        self.assertEqual(self.post(self.command('retract-two', operation='retract'))[0], 200)
        state = ManagementStore(self.fixture.hub).snapshot()
        self.assertEqual(state['revision'], 2)
        self.assertEqual(len(state['history']), 2)
        self.assertEqual(state['projects'], {})
        self.assertEqual(self.service.get_management('a')['relation_state'], 'unconfigured')

    def test_missing_config_read_and_save_never_initialize_it(self):
        self.session(owner=True)
        command = self.command()
        self.config.unlink()
        contents = sorted(path.name for path in self.config.parent.iterdir())
        status, _, result = self.call()
        self.assertEqual(status, 200)
        self.assertFalse(result['data']['available'])
        self.assertFalse(result['data']['editable'])
        self.assertIsNone(result['data']['revision'])
        status, _, result = self.post(command)
        self.assertEqual((status, result['error']['code']), (503, 'MANAGEMENT_NOT_INITIALIZED'))
        self.assertFalse(self.config.exists())
        self.assertEqual(sorted(path.name for path in self.config.parent.iterdir()), contents)

    def test_invalid_dates_layout_and_self_relations_rejected_without_effect(self):
        self.session(owner=True)
        before = self.config.read_bytes()
        for value in (overlay(start_date='2026-02-30'), overlay(end_date='2026-10-01'),
                      overlay(layout={'x': 321, 'y': 0}),
                      overlay(relations=relations(depends=['a'])),
                      overlay(relations=relations(depends=['b', 'b']))):
            with self.subTest(value=value):
                status, _, result = self.post(self.command(value=value))
                self.assertEqual((status, result['error']['code']), (400, 'MANAGEMENT_INVALID_COMMAND'))
                self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
                self.assertEqual(self.config.read_bytes(), before)

    def test_registry_scope_change_before_commit_rejects_stale_authority(self):
        self.session(owner=True)
        before = self.config.read_bytes()
        command = self.command()
        original_init = ManagementStore.__init__
        def fault(stage):
            if stage == 'before_replace':
                self.fixture.projects[0]['connection_read_allowed'] = False
                self.fixture.save_registry()
        def initialize(store, root, **kwargs):
            original_init(store, root, fault=fault)
        with mock.patch.object(ManagementStore, '__init__', initialize):
            status, _, result = self.post(command)
        self.assertEqual((status, result['error']['code']), (409, 'MANAGEMENT_SCOPE_CHANGED'))
        self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
        self.assertEqual(self.config.read_bytes(), before)



    def proposal_fixture(self, members=('a', 'b')):
        self.fixture.add('c')
        self.projection = self.config.with_name('proposal-projection.json')
        proposal = dict(id='selected', project_ids=list(members), kind='pipeline', status='proposed',
            shared_tasks=['Synthetic handoff'], differences=['Separate ownership'],
            not_shared=['Permissions'], inventory_index=0, evidence_refs=['fixture:evidence'])
        self.projection.write_text(json.dumps({'projects': [dict(project_id=i, status='proposed',
            reason=None, relations=[proposal], program_link_authority=False,
            design_selection_authority=False) for i in members]}))
        self.service.relations_path = self.projection

    def proposal_command(self, request='confirm-selected'):
        view = self.service.get_management('a')
        value = copy.deepcopy(view['overlay'])
        value['relations'] = copy.deepcopy(value['relations']) if value['relations'] is not None else relations()
        members = view['proposals'][0]['project_ids']
        value['relations']['related_to'] = list(dict.fromkeys(value['relations']['related_to'] +
            [ident for ident in members if ident != 'a']))
        command = self.command(request=request, value=value)
        command['proposal_ref'] = view['proposals'][0]['proposal_ref']
        return command

    def test_proposal_exact_targets_preserve_existing_and_typed_content(self):
        from test_hub_graph_relations import relation
        self.proposal_fixture(); self.session(owner=True)
        typed = relation(); typed['from']['project_id'] = typed['to']['project_id'] = 'a'
        value = overlay(priority=7, relations={**relations(related=['c']),
            'typed': {'schema_version': '1.0', 'items': [typed]}})
        self.assertEqual(self.post(self.command(value=value))[0], 200)
        command = self.proposal_command(); before = self.config.read_bytes()
        for targets in (['c','b','unregistered'], ['b'], ['b','c']):
            with self.subTest(targets=targets):
                bad = copy.deepcopy(command); bad['value']['relations']['related_to'] = targets
                self.assertIn(self.post(bad)[0], (400,409)); self.assertEqual(self.config.read_bytes(), before)
        status, _, response = self.post(command)
        self.assertEqual(status, 200)
        expected = copy.deepcopy(value); expected['relations']['related_to'] = ['c','b']
        self.assertEqual(response['data']['view']['overlay'], expected)

    def test_proposal_unknown_replay_keeps_receipt_after_later_source_and_value(self):
        self.proposal_fixture(); self.session(owner=True)
        self.assertEqual(self.post(self.command())[0], 200)
        command = self.proposal_command(); original_init = ManagementStore.__init__
        def initialize(store, root, **kwargs):
            original_init(store, root, fault=lambda stage: (_ for _ in ()).throw(OSError('lost receipt'))
                if stage == 'after_replace' else None)
        with mock.patch.object(ManagementStore, '__init__', initialize):
            status, _, result = self.post(command)
        self.assertEqual((status,result['error']['outcome']), (503,'UNKNOWN'))
        committed = ManagementStore(self.fixture.hub).snapshot()['history'][-1]['revision']
        self.assertEqual(self.post(self.command(request='later',value=overlay(priority=8)))[0], 200)
        self.projection.write_text(json.dumps({'projects': []})); before = self.config.read_bytes()
        status, _, result = self.post(command)
        self.assertEqual(status, 200); self.assertTrue(result['data']['receipt']['replayed'])
        self.assertEqual(result['data']['receipt']['committed_revision'], committed)
        changed = copy.deepcopy(command); changed['value']['priority'] = 9
        self.assertEqual(self.post(changed)[0], 409); self.assertEqual(self.config.read_bytes(), before)

    def test_proposal_source_and_owner_rechecked_at_commit(self):
        self.proposal_fixture(); self.session(owner=True)
        self.assertEqual(self.post(self.command())[0], 200)
        for mode in ('source','owner'):
            with self.subTest(mode=mode):
                self.cookie = self.csrf = None; self.session(owner=True)
                command = self.proposal_command('boundary-'+mode); before = self.config.read_bytes()
                original_init = ManagementStore.__init__
                def fault(stage):
                    if stage == 'before_replace':
                        if mode == 'source': self.projection.write_text(json.dumps({'projects': []}))
                        else: self.server.sessions.revoke(self.cookie)
                def initialize(store, root, **kwargs): original_init(store, root, fault=fault)
                with mock.patch.object(ManagementStore, '__init__', initialize):
                    status, _, result = self.post(command)
                self.assertEqual(status, 409 if mode == 'source' else 401)
                self.assertEqual(result['error']['outcome'], 'NOT_COMMITTED')
                self.assertEqual(self.config.read_bytes(), before)
                # Restore only this disposable projection for the next boundary.
                if mode == 'source':
                    proposal = dict(id='selected',project_ids=['a','b'],kind='pipeline',status='proposed',
                        shared_tasks=['Synthetic handoff'],differences=['Separate ownership'],not_shared=['Permissions'],
                        inventory_index=0,evidence_refs=['fixture:evidence'])
                    self.projection.write_text(json.dumps({'projects':[dict(project_id=i,status='proposed',reason=None,
                        relations=[proposal],program_link_authority=False,design_selection_authority=False)for i in ('a','b')]}))


if __name__ == '__main__':
    unittest.main()
