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


if __name__ == "__main__":
    unittest.main()
