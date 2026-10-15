"""Pure source-declared page inventory and preview version metadata.

A current binding says nothing about artifact bytes, capture permission, rendered
behavior or human acceptance. No filesystem, browser or clock is consulted.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .connection_records import SECRET, exact, fingerprint, identifier, require, string
from .preview_policy import validate_artifact_ref
from .time_semantics import validate_time_point

_DOMAINS = {"implementation", "design"}
_KINDS = {"route", "dialog", "tab", "screen", "flow", "document", "other"}
_PAGE_FIELDS = {"id", "label", "kind", "source_sha256", "build_sha256"}
_PREVIEW_FIELDS = {"id", "project_id", "domain", "page_id", "artifact_ref", "artifact_sha256",
                   "captured_at", "source_sha256", "build_sha256"}
_UNSAFE_TEXT = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|\bwww\.|"
                          r"[?&](?:session(?:_id)?|token|access_token|password|secret|api_key|cookie)=)")


def _text(value, label, limit):
    string(value, label)
    require(len(value) <= limit and not _UNSAFE_TEXT.search(value) and not SECRET.search(value),
            label + ": invalid metadata text")


def _hashes(value):
    for key in ("source_sha256", "build_sha256"):
        if value[key] is not None:
            fingerprint(value[key], key)


def _domain(value):
    require(type(value) is str and value in _DOMAINS, "page inventory: invalid domain")


def validate_page_inventory(value, *, project_id=None):
    """Return a detached exact source value; reject incomplete reference context."""
    exact(value, {"schema_version", "project_id", "domains", "previews"}, "page inventory")
    require(value["schema_version"] == "1.0", "unsupported page inventory schema")
    identifier(value["project_id"], "page inventory project")
    if project_id is not None:
        identifier(project_id, "expected page inventory project")
        require(project_id == value["project_id"], "page inventory project mismatch")
    domains = value["domains"]
    require(type(domains) is list and len(domains) == 2, "page inventory: both domains required")
    page_index = {}
    for domain in domains:
        exact(domain, {"domain", "known_page_count", "completeness", "counting_basis", "pages"}, "page domain")
        name = domain["domain"]
        _domain(name)
        require(name not in page_index, "page inventory: duplicate domain")
        _text(domain["counting_basis"], "page counting basis", 2000)
        state = domain["completeness"]
        require(type(state) is str and state in {"complete", "partial", "unknown"}, "page domain: invalid completeness")
        pages = domain["pages"]
        require(type(pages) is list and len(pages) <= 10000, "page domain: bounded pages required")
        count = domain["known_page_count"]
        if state == "unknown":
            require(count is None and not pages, "unknown page domain: null count and no pages required")
        else:
            require(type(count) is int and count == len(pages), "page domain: declared count must match listed units")
        page_index[name] = {}
        for page in pages:
            exact(page, _PAGE_FIELDS, "inventory page")
            identifier(page["id"], "inventory page identity")
            require(page["id"] not in page_index[name], "page domain: duplicate page identity")
            _text(page["label"], "page label", 500)
            require(type(page["kind"]) is str and page["kind"] in _KINDS, "inventory page: invalid kind")
            _hashes(page)
            page_index[name][page["id"]] = page
    previews = value["previews"]
    require(type(previews) is list and len(previews) <= 3, "page inventory: at most three representative previews")
    seen = set()
    for preview in previews:
        exact(preview, _PREVIEW_FIELDS, "inventory preview")
        identifier(preview["id"], "preview identity")
        require(preview["id"] not in seen, "page inventory: duplicate preview identity")
        seen.add(preview["id"])
        identifier(preview["project_id"], "preview project")
        require(preview["project_id"] == value["project_id"], "preview project mismatch")
        _domain(preview["domain"])
        identifier(preview["page_id"], "preview page identity")
        require(preview["page_id"] in page_index[preview["domain"]], "preview page missing from declared domain")
        validate_artifact_ref(preview["artifact_ref"])
        fingerprint(preview["artifact_sha256"], "preview artifact hash")
        _hashes(preview)
        if preview["captured_at"] is not None:
            validate_time_point({"kind": "timestamp", "value": preview["captured_at"], "timezone": None})
    return deepcopy(value)


def inventory_preview_states(value):
    """Bind preview metadata to its exact current domain/page, without opening it."""
    inventory = validate_page_inventory(value)
    pages = {(domain["domain"], page["id"]): page
             for domain in inventory["domains"] for page in domain["pages"]}
    result = []
    for preview in inventory["previews"]:
        page = pages[preview["domain"], preview["page_id"]]
        source_pair = (page["source_sha256"], preview["source_sha256"])
        build_pair = (page["build_sha256"], preview["build_sha256"])
        if None not in source_pair and source_pair[0] != source_pair[1]:
            state, reason = "historical", "source_version_mismatch"
        elif None not in build_pair and build_pair[0] != build_pair[1]:
            state, reason = "historical", "build_version_mismatch"
        elif None in source_pair:
            state, reason = "unknown_version", "source_version_unknown"
        elif (build_pair[0] is None) != (build_pair[1] is None):
            state, reason = "unknown_version", "build_version_unknown"
        else:
            state, reason = "current", None
        result.append({**preview, "version_state": state, "version_reason": reason})
    return result
