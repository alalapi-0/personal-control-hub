"""Declared page counts and representative preview bindings stay separate."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_records import RecordError
from hub.page_inventory import inventory_preview_states, validate_page_inventory

A, B = "a" * 64, "b" * 64


def page(page_id="route-home", kind="route", source=A, build=None):
    return {"id": page_id, "label": "Home", "kind": kind, "source_sha256": source, "build_sha256": build}


def domain(name, pages=None, state="complete"):
    pages = [] if pages is None else pages
    return {"domain": name, "known_page_count": None if state == "unknown" else len(pages),
            "completeness": state, "counting_basis": "Each route, dynamic route pattern, dialog and tab is one declared unit",
            "pages": pages}


def inventory():
    return {"schema_version": "1.0", "project_id": "business", "domains": [
        domain("implementation", [page()]), domain("design", state="unknown")], "previews": []}


def preview(preview_id="home-preview", page_id="route-home", source=A, build=None):
    return {"id": preview_id, "project_id": "business", "domain": "implementation", "page_id": page_id,
            "artifact_ref": "data/previews/business/home.webp", "artifact_sha256": B,
            "captured_at": None, "source_sha256": source, "build_sha256": build}


def with_preview(source=A, build=None, captured_source=A, captured_build=None):
    value = inventory()
    value["domains"][0]["pages"][0].update(source_sha256=source, build_sha256=build)
    value["previews"] = [preview(source=captured_source, build=captured_build)]
    return value


class PageInventoryTests(unittest.TestCase):
    def test_recognizable_secret_values_cannot_hide_in_metadata_text(self):
        for secret in ['sk-' + 'x' * 24, 'password=' + 'synthetic-example-value']:
            for target in ('label', 'counting_basis'):
                value = inventory()
                if target == 'label':
                    value['domains'][0]['pages'][0]['label'] = secret
                else:
                    value['domains'][0]['counting_basis'] = secret
                with self.subTest(target=target), self.assertRaises(RecordError):
                    validate_page_inventory(value)

    def test_unknown_zero_partial_and_complete_are_distinct(self):
        for state, pages in [("unknown", []), ("complete", []), ("partial", []),
                             ("partial", [page()]), ("complete", [page()])]:
            value = inventory()
            value["domains"][0] = domain("implementation", pages, state)
            result = validate_page_inventory(value)
            self.assertEqual(value, result)
            self.assertEqual(state, result["domains"][0]["completeness"])

    def test_unknown_cannot_publish_zero_or_known_pages(self):
        for count, pages in [(0, []), (1, [page()]), (None, [page()])]:
            value = inventory()
            value["domains"][0].update(completeness="unknown", known_page_count=count, pages=pages)
            with self.assertRaises(RecordError):
                validate_page_inventory(value)

    def test_known_counts_match_declared_dynamic_route_dialog_tab_units(self):
        value = inventory()
        value["domains"][0] = domain("implementation", [page("dynamic-user", "route"),
            page("confirm", "dialog"), page("settings", "tab")], "partial")
        result = validate_page_inventory(value)
        self.assertEqual(3, result["domains"][0]["known_page_count"])
        self.assertEqual(["route", "dialog", "tab"], [row["kind"] for row in result["domains"][0]["pages"]])
        for bad_count in (2, 4, None, True, 3.0, -1):
            value["domains"][0]["known_page_count"] = bad_count
            with self.assertRaises(RecordError):
                validate_page_inventory(value)

    def test_design_and_implementation_ids_are_separate(self):
        value = with_preview()
        value["domains"][1] = domain("design", [page(source=B)])
        self.assertEqual("current", inventory_preview_states(value)[0]["version_state"])
        value["previews"][0]["domain"] = "design"
        self.assertEqual("historical", inventory_preview_states(value)[0]["version_state"])

    def test_both_unique_domains_required_without_merging(self):
        for domains in ([], [domain("implementation")], [domain("design"), domain("design")],
                        [domain("other"), domain("implementation")], [domain("design"), domain("implementation"), domain("design")]):
            with self.assertRaises(RecordError):
                validate_page_inventory({**inventory(), "domains": domains})

    def test_representative_limit_does_not_limit_page_count(self):
        value = inventory()
        value["domains"][0] = domain("implementation", [page("page-" + str(n)) for n in range(100)])
        for count in (0, 1, 2, 3):
            value["previews"] = [preview("preview-" + str(n), "page-" + str(n)) for n in range(count)]
            self.assertEqual(100, validate_page_inventory(value)["domains"][0]["known_page_count"])
        value["previews"].append(preview("preview-3", "page-3"))
        with self.assertRaises(RecordError):
            validate_page_inventory(value)

    def test_preview_project_page_and_domain_references_are_exact(self):
        for change in ({"project_id": "other"}, {"page_id": "absent"}, {"domain": "design"}, {"domain": "other"}):
            value = with_preview()
            value["previews"][0].update(change)
            with self.assertRaises(RecordError):
                validate_page_inventory(value)
        with self.assertRaises(RecordError):
            validate_page_inventory(inventory(), project_id="other")

    def test_preview_ids_and_domain_page_ids_unique(self):
        value = with_preview()
        value["previews"].append(copy.deepcopy(value["previews"][0]))
        with self.assertRaises(RecordError):
            validate_page_inventory(value)
        value = inventory()
        value["domains"][0]["pages"].append(page())
        value["domains"][0]["known_page_count"] = 2
        with self.assertRaises(RecordError):
            validate_page_inventory(value)

    def test_current_source_only_does_not_invent_known_build_or_time(self):
        result = inventory_preview_states(with_preview())[0]
        self.assertEqual("current", result["version_state"])
        self.assertIsNone(result["version_reason"])
        self.assertIsNone(result["build_sha256"])
        self.assertIsNone(result["captured_at"])

    def test_current_requires_equal_known_source_and_matching_build_when_present(self):
        self.assertEqual("current", inventory_preview_states(with_preview(build=B, captured_build=B))[0]["version_state"])
        for source, capture in ((None, A), (A, None), (None, None)):
            result = inventory_preview_states(with_preview(source=source, captured_source=capture))[0]
            self.assertEqual("unknown_version", result["version_state"])
            self.assertEqual("source_version_unknown", result["version_reason"])

    def test_one_sided_build_is_unknown(self):
        for build, captured_build in ((None, B), (B, None)):
            result = inventory_preview_states(with_preview(build=build, captured_build=captured_build))[0]
            self.assertEqual("unknown_version", result["version_state"])
            self.assertEqual("build_version_unknown", result["version_reason"])

    def test_known_mismatch_wins_over_missing_other_metadata(self):
        cases = [(dict(captured_source=B), "source_version_mismatch"),
                 (dict(build=A, captured_build=B), "build_version_mismatch"),
                 (dict(source=None, build=A, captured_build=B), "build_version_mismatch"),
                 (dict(captured_source=B, captured_build=A), "source_version_mismatch")]
        for changes, reason in cases:
            result = inventory_preview_states(with_preview(**changes))[0]
            self.assertEqual("historical", result["version_state"])
            self.assertEqual(reason, result["version_reason"])

    def test_capture_time_nullable_aware_offset_literal_and_not_version_signal(self):
        for when in (None, "2026-10-15T01:02:03+09:00", "2026-10-14T16:02:03Z"):
            value = with_preview()
            value["previews"][0]["captured_at"] = when
            result = inventory_preview_states(value)[0]
            self.assertEqual(when, result["captured_at"])
            self.assertEqual("current", result["version_state"])
        for when in ("2026-10-15", "2026-10-15T01:02:03", "2026-02-30T00:00:00Z", 123):
            value["previews"][0]["captured_at"] = when
            with self.assertRaises(RecordError):
                validate_page_inventory(value)

    def test_detached_validation_and_binding_do_not_modify_source(self):
        value = with_preview()
        before = copy.deepcopy(value)
        validated = validate_page_inventory(value, project_id="business")
        states = inventory_preview_states(value)
        validated["domains"][0]["pages"][0]["label"] = "Changed"
        states[0]["artifact_ref"] = "Changed"
        self.assertEqual(before, value)
        self.assertNotIn("version_state", value["previews"][0])

    def test_exact_objects_reject_sensitive_and_derived_fields(self):
        for location in ("root", "domain", "page", "preview"):
            value = with_preview()
            target = {"root": value, "domain": value["domains"][0], "page": value["domains"][0]["pages"][0],
                      "preview": value["previews"][0]}[location]
            target["session_cookie"] = "private"
            with self.assertRaises(RecordError):
                validate_page_inventory(value)
        value = with_preview()
        value["previews"][0]["version_state"] = "current"
        with self.assertRaises(RecordError):
            validate_page_inventory(value)

    def test_artifact_refs_are_lexically_guarded(self):
        for ref in ("https://example.com/a.png", "data/previews/../private.png", "/tmp/image.png",
                    "data/previews/home.png?token=private", "data/previews/token/image.png", "data/previews/image.svg"):
            value = with_preview()
            value["previews"][0]["artifact_ref"] = ref
            with self.assertRaises(RecordError):
                validate_page_inventory(value)

    def test_metadata_bounds_hashes_kinds_and_text(self):
        for changes in ({"label": "x" * 501}, {"label": "  "}, {"label": "https://user:password@example.com"},
                        {"kind": "rendered_instances"}, {"source_sha256": "not-a-hash"}, {"build_sha256": 42}):
            value = inventory()
            value["domains"][0]["pages"][0].update(changes)
            with self.assertRaises(RecordError):
                validate_page_inventory(value)
        for basis in ("x" * 2001, "", "https://example.com?session_id=private"):
            value = inventory()
            value["domains"][0]["counting_basis"] = basis
            with self.assertRaises(RecordError):
                validate_page_inventory(value)
        value = inventory()
        value["domains"][0]["pages"] = [None] * 10001
        value["domains"][0]["known_page_count"] = 10001
        with self.assertRaises(RecordError):
            validate_page_inventory(value)




class PCHDomainActionsTests(unittest.TestCase):
    def setUp(self):
        from test_hub_project_definition import OwnHubSourceFixture
        from hub.design_store import DesignStore
        from hub.design_service import DesignService
        from hub.action_registry import ActionRegistry
        from hub.pch_domain_actions import PCHDomainActions
        self.f = OwnHubSourceFixture(); self.addCleanup(self.f.close)
        self.f.collect()
        store = DesignStore(self.f.root, self.f.root / 'docs/reports/ui_design_governance/unit-04/pch/store.json', fixture=True)
        store.initialize()
        self.actions = ActionRegistry(self.f.service, DesignService(store), host='http://127.0.0.1:1234', backend_instance='a'*32)
        self.domain = PCHDomainActions(self.f.service, self.actions)

    def test_actual_self_catalog_graph_versions_and_reads_do_not_collect_or_write(self):
        from unittest import mock
        from hub.graph_records import validate_graph
        from hub.graph_service import GraphService
        before = self.f.ledger.history(); source = (self.f.root / 'STATE.yaml').read_bytes()
        with mock.patch('hub.connection_sources.SourceResolver.refresh', side_effect=AssertionError('GET collected')):
            value = self.domain.catalog()
            projected = GraphService(self.f.service).get_project(self.f.project_id)
            validate_graph(value['graph']); validate_graph(projected)
        self.assertEqual(value['head'], value['detail']['provenance']['ledger_head'])
        self.assertEqual(value['graph']['projection_version'], projected['projection_version'])
        self.assertEqual(value['route'], 'route:personal-control-hub')
        self.assertEqual(value['versions']['checkout']['state'], 'unknown')
        self.assertFalse(value['versions']['web_resources']['installed_program_proof'])
        self.assertEqual(self.f.ledger.history(), before)
        self.assertEqual((self.f.root / 'STATE.yaml').read_bytes(), source)
        self.assertNotIn(str(self.f.root), str(value['versions']))
        self.assertFalse(value['native_execution_available'])

    def test_self_refresh_replay_and_original_request_reconciliation(self):
        from hub.pch_domain_actions import PROJECT
        command = {'request_id':'self-second', 'project_ids':[PROJECT], 'expected_head':self.f.ledger.head}
        first = self.domain.refresh(command, authorize=lambda:None)
        history = self.f.ledger.history()
        second = self.domain.refresh(command, authorize=lambda:None)
        self.assertEqual(history, self.f.ledger.history())
        self.assertEqual(first['request_result'], second['request_result'])
        self.assertEqual(first['catalog']['head'], first['request_result']['head'])
        wrong = copy.deepcopy(command); wrong['expected_head']['hash']='f'*64
        from hub.service_contract import ServiceError
        with self.assertRaises(ServiceError) as caught:self.domain.request_result(wrong)
        self.assertEqual(caught.exception.code,'PCH_REFRESH_REQUEST_CONFLICT')

    def test_foreign_scope_permission_and_head_mismatch_are_zero_effects(self):
        from unittest import mock
        from hub.service_contract import ServiceError
        before = self.f.ledger.history()
        for ids in (['foreign'], ['personal-control-hub','foreign'], [], 'personal-control-hub'):
            with self.subTest(ids=ids), mock.patch.object(self.f.service,'refresh',side_effect=AssertionError('refresh')):
                with self.assertRaises(ServiceError):self.domain.refresh({'request_id':'bad','project_ids':ids,'expected_head':self.f.ledger.head},authorize=lambda:None)
        def denied():raise ServiceError('OWNER_AUTH_REQUIRED',status=401)
        with self.assertRaises(ServiceError):self.domain.refresh({'request_id':'denied','project_ids':[self.f.project_id],'expected_head':self.f.ledger.head},authorize=denied)
        with mock.patch.object(self.f.service,'ledger_head',return_value={'sequence':99,'hash':'f'*64}):
            with self.assertRaises(ServiceError) as caught:self.domain.catalog()
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(before,self.f.ledger.history())

    def test_current_failure_remains_current_and_uncertain_response_preserves_original_command(self):
        from unittest import mock
        from hub.service_contract import ServiceError
        command = {'request_id':'self-failure','project_ids':[self.f.project_id],'expected_head':self.f.ledger.head}
        (self.f.root / 'STATE.yaml').write_text('broken: [')
        value = self.domain.refresh(command,authorize=lambda:None)
        self.assertFalse(value['request_result']['results'][0]['success'])
        runs=[o for o in value['catalog']['graph']['objects'] if o['type']=='Run']
        self.assertTrue(any(o['source']['applicability']=='current' and not o['data']['success'] for o in runs))
        self.assertEqual(value['command'],command)
        with mock.patch.object(self.domain,'catalog',side_effect=ServiceError('PCH_DOMAIN_HEAD_CHANGED',status=409)):
            with self.assertRaises(ServiceError) as caught:self.domain.refresh(command,authorize=lambda:None)
        self.assertEqual(caught.exception.outcome,'UNKNOWN')
        self.assertEqual(self.domain.request_result(command)['status'],'FINISHED')


    def test_unread_catalog_has_explicit_genesis_refresh_head_and_permission_revocation_refuses(self):
        from test_hub_project_definition import OwnHubSourceFixture
        from hub.pch_domain_actions import PCHDomainActions
        from hub.service_contract import ServiceError
        from unittest import mock
        f=OwnHubSourceFixture(); self.addCleanup(f.close)
        # Fixture initialized the empty ledger; this is still a verified genesis head.
        class EmptyActions:
            def catalog(self, project):raise ServiceError('ACTION_CATALOG_UNAVAILABLE',status=503)
        domain=PCHDomainActions(f.service,EmptyActions());value=domain.catalog()
        self.assertEqual(value['refresh_head'],{'sequence':0,'hash':'0'*64})
        self.assertIsNone(value['artifacts']);self.assertEqual(value['artifact_gap'],'ACTION_CATALOG_UNAVAILABLE')
        self.assertFalse(value['requests'])
        old=f.service.get_project(f.project_id);denied=copy.deepcopy(old);denied['declared']['connection_read_allowed']=False
        with mock.patch.object(f.service,'get_project',side_effect=[old,denied]):
            with self.assertRaises(ServiceError) as caught:domain.catalog()
        self.assertEqual(caught.exception.code,'PCH_DOMAIN_READ_DENIED')



class RegisteredPCHArtifactFixture:
    """Real isolated registry/source/ledger and nonempty DesignService records."""
    def __init__(self):
        import hashlib
        from test_hub_project_definition import OwnHubSourceFixture
        from hub.design_cli import _artifact, _baseline, _candidate
        from hub.design_records import with_content_hash
        from hub.design_store import DesignStore
        from hub.design_service import DesignService
        from hub.action_registry import ActionRegistry
        from hub.pch_domain_actions import PCHDomainActions
        self.source = OwnHubSourceFixture()
        self.root, self.projects = self.source.root, self.source.service
        self.source.collect()
        self.work = self.root / 'docs/reports/ui_design_governance/pch-actions'
        self.work.mkdir(parents=True)
        self.payload = b'\x89PNG\r\n\x1a\nsynthetic-pch-artifact-bytes'
        self.path = self.work / 'candidate.png'; self.path.write_bytes(self.payload)
        baseline_path = self.work / 'baseline.txt'; baseline_path.write_bytes(b'synthetic PCH baseline')
        self.artifact = _artifact('pch-candidate-preview', str(self.path.relative_to(self.root)), hashlib.sha256(self.payload).hexdigest())
        baseline_artifact = _artifact('pch-baseline-preview', str(baseline_path.relative_to(self.root)), hashlib.sha256(baseline_path.read_bytes()).hexdigest())
        for artifact in (self.artifact, baseline_artifact):
            artifact['classification'] = 'imported'
            artifact['scope']['members'][0]['project_id'] = 'personal-control-hub'
        self.baseline = _baseline(1, baseline_artifact)
        self.baseline.update(id='pch-baseline', project_id='personal-control-hub', classification='imported')
        self.baseline = with_content_hash(self.baseline)
        # Use the actual disposable registry to validate PCH, not fixture-only IDs.
        self.store = DesignStore(self.root, self.work / 'store.json')
        self.store.initialize()
        for index, fact in enumerate((baseline_artifact, self.artifact, self.baseline)):
            self.append(fact, 'pch-design-fact-'+str(index))
        self.publish_candidate(1)
        self.designs = DesignService(self.store)
        self.actions = ActionRegistry(self.projects, self.designs, host='http://127.0.0.1:1234', backend_instance='a'*32)
        self.domain = PCHDomainActions(self.projects, self.actions)

    def append(self, fact, request):
        self.store.append_fact(fact, expected_revision=self.store.read()['revision'], request_id=request)

    def publish_candidate(self, revision):
        from hub.design_cli import _candidate
        from hub.design_records import with_content_hash
        candidate = _candidate(revision, self.baseline, self.artifact)
        candidate['id'] = 'pch-candidate'
        candidate['classification'] = 'imported'
        candidate['scope']['members'][0]['project_id'] = 'personal-control-hub'
        candidate['baseline_bindings'][0]['project_id'] = 'personal-control-hub'
        self.candidate = with_content_hash(candidate)
        self.append(self.candidate, 'pch-design-candidate-'+str(revision))

    def close(self):
        self.source.close()


class PCHRegisteredArtifactBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = RegisteredPCHArtifactFixture(); self.addCleanup(self.f.close)

    def bound(self):
        value = self.f.domain.catalog()
        entry = next(e for e in value['artifacts']['entries'] if e['object']['native_id'] == self.f.artifact['id'])
        self.assertTrue(entry['actions'])
        return value, entry, next(a['binding'] for a in entry['actions'] if a['id'] == 'open_registered_artifact')

    def test_nonempty_facade_actual_graph_and_catalog_have_same_trusted_design_versions(self):
        from hub.graph_records import validate_graph
        from hub.graph_service import GraphService
        value, entry, binding = self.bound()
        snapshot = self.f.designs.snapshot()
        graph = GraphService(self.f.projects).get_project('personal-control-hub', design_snapshot=snapshot)
        for published in (graph, value['graph']):
            validate_graph(published)
            self.assertEqual(published['source_version'], value['artifacts']['source_version'])
            self.assertEqual(published['projection_version'], value['artifacts']['projection_version'])
            selected = next(o for o in published['objects'] if (o['id'],o['revision']) == (entry['object']['id'],entry['object']['revision']))
            self.assertEqual(selected, entry['object'])
        self.assertEqual(binding['project_id'], 'personal-control-hub')
        self.assertEqual(binding['candidate_id'], self.f.candidate['id'])
        self.assertEqual(binding['candidate_sha256'], self.f.candidate['content_hash'])
        self.assertEqual(binding['artifact_sha256'], self.f.artifact['sha256'])
        self.assertEqual({a['id'] for a in entry['actions']}, {'open_registered_artifact','verify_registered_artifact','prepare_ordinary_task','native_program'})
        self.assertFalse(next(a for a in entry['actions'] if a['id']=='native_program')['available'])

    def test_real_bytes_read_once_and_receipt_binding_stays_authoritative(self):
        from unittest import mock
        from hub.action_registry import request_id, digest
        _, _, binding = self.bound(); history = self.f.source.ledger.history(); before = self.f.store.path.read_bytes()
        with mock.patch.object(self.f.designs, 'artifact', wraps=self.f.designs.artifact) as read:
            value = self.f.actions.read_with_receipt(binding, authorize=lambda:None)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(value.response.data, self.f.payload)
        self.assertEqual(value.receipt['binding_sha256'], binding['binding_sha256'])
        command = {'action_id':'open_registered_artifact','binding':binding,'request_id':request_id('open_registered_artifact',binding)}
        self.assertEqual(value.receipt['parameter_sha256'], digest(command))
        self.assertEqual(value.receipt['input_sha256'], self.f.artifact['sha256'])
        self.assertFalse(value.receipt['native_execution']); self.assertFalse(value.receipt['durable_receipt'])
        self.assertEqual(self.f.store.path.read_bytes(), before)
        self.assertEqual(self.f.source.ledger.history(), history)

    def test_stale_source_graph_candidate_or_binding_rejected_before_content(self):
        from hub.action_registry import digest
        from hub.service_contract import ServiceError
        from unittest import mock
        _, _, binding = self.bound()
        for field, replacement in [('source_version','f'*64), ('object_revision','foreign-revision'),
                                   ('candidate_revision',99), ('candidate_sha256','f'*64), ('artifact_sha256','f'*64)]:
            bad = {**binding, field:replacement}
            bad['binding_sha256'] = digest({k:v for k,v in bad.items() if k!='binding_sha256'})
            with self.subTest(field=field), mock.patch.object(self.f.designs,'artifact',side_effect=AssertionError('stale content read')):
                with self.assertRaises(ServiceError) as error:self.f.actions.read_with_receipt(bad, authorize=lambda:None)
            self.assertEqual(error.exception.code, 'ACTION_BINDING_STALE')
        # An actual new source result invalidates the old source-bound permission.
        command = {'request_id':'pch-next-result','project_ids':['personal-control-hub'],'expected_head':self.f.source.ledger.head}
        self.f.projects.refresh(command)
        with mock.patch.object(self.f.designs,'artifact',side_effect=AssertionError('old source read')):
            with self.assertRaises(ServiceError) as error:self.f.actions.read(binding,authorize=lambda:None)
        self.assertEqual(error.exception.code, 'ACTION_BINDING_STALE')

    def test_same_head_design_change_has_new_graph_version_and_mixed_snapshot_rejects(self):
        from hub.graph_service import GraphService
        from hub.service_contract import ServiceError
        from unittest import mock
        old = self.f.designs.snapshot(); original = self.f.domain.catalog(); head = self.f.source.ledger.head
        self.f.publish_candidate(2); new = self.f.designs.snapshot()
        changed = self.f.domain.catalog()
        self.assertEqual(original['head'], changed['head']); self.assertEqual(head,self.f.source.ledger.head)
        self.assertNotEqual(original['graph']['projection_version'], changed['graph']['projection_version'])
        old_graph = GraphService(self.f.projects).get_project('personal-control-hub', design_snapshot=old)
        new_graph = GraphService(self.f.projects).get_project('personal-control-hub', design_snapshot=new)
        self.assertEqual(old_graph['projection_version'], original['artifacts']['projection_version'])
        self.assertEqual(new_graph['projection_version'], changed['artifacts']['projection_version'])
        with mock.patch.object(self.f.designs,'snapshot',side_effect=[old,new]):
            with self.assertRaises(ServiceError) as error:self.f.domain.catalog()
        self.assertEqual(error.exception.code,'PCH_DOMAIN_DESIGN_CHANGED')
        self.assertEqual(error.exception.status,409); self.assertTrue(error.exception.retryable)

    def test_design_service_unavailable_preserves_independent_source_and_failure_visibility(self):
        from hub.service_contract import ServiceError
        from unittest import mock
        before=self.f.source.ledger.history()
        with mock.patch.object(self.f.designs,'snapshot',side_effect=ServiceError('DESIGN_STORE_UNAVAILABLE',status=503)):
            value=self.f.domain.catalog()
        self.assertIsNone(value['artifacts']);self.assertEqual(value['artifact_gap'],'ACTION_CATALOG_UNAVAILABLE')
        self.assertEqual(value['head'],before['head'])
        self.assertTrue(any(o['type']=='Run' for o in value['graph']['objects']))
        self.assertEqual(before,self.f.source.ledger.history())


    def test_denied_pch_does_not_observe_design_store(self):
        from hub.service_contract import ServiceError
        from unittest import mock
        value = self.f.projects.get_project('personal-control-hub')
        value['declared']['connection_read_allowed'] = False
        with mock.patch.object(self.f.projects,'get_project',return_value=value), mock.patch.object(self.f.designs,'snapshot',side_effect=AssertionError('denied design access')):
            with self.assertRaises(ServiceError) as error:self.f.domain.catalog()
        self.assertEqual(error.exception.code, 'PCH_DOMAIN_READ_DENIED')

if __name__ == "__main__":
    unittest.main()
