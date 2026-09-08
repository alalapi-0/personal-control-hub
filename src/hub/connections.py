"""Read-only registry loading and typed source selectors."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from hub.connection_records import RecordError, identifier, require, string


class UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        require(type(key) is str and key not in result, "duplicate or non-string YAML key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _json_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def parse_source(data: bytes, format_name: str) -> Any:
    try:
        text = data.decode("utf-8")
        if format_name == "markdown":
            return text
        if format_name == "json":
            return json.loads(text, object_pairs_hook=_json_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(RecordError("nonfinite JSON value")))
        require(format_name == "yaml", "unsupported source format")
        require(not any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                        for token in yaml.scan(text)), "YAML aliases/anchors are not state facts")
        return yaml.load(text, Loader=UniqueLoader)
    except (UnicodeError, yaml.YAMLError, json.JSONDecodeError, RecursionError) as exc:
        raise RecordError("source encoding or structure invalid") from exc


def load_registry_at(path: Path) -> dict:
    return validate_registry_value(parse_source(path.read_bytes(), "yaml"))


def validate_registry_value(value: Any) -> dict:
    require(type(value) is dict and type(value.get("projects")) is list, "registry projects required")
    ids = [row.get("id") for row in value["projects"] if type(row) is dict]
    require(len(ids) == len(value["projects"]) and all(type(x) is str for x in ids)
            and len(ids) == len(set(ids)), "registry project identities invalid or duplicated")
    for row in value["projects"]:
        identifier(row["id"], "registry project")
        string(row.get("name"), "registry project name")
        # Removed rows remain reportable even when malicious activation flags are present.
        if (row.get("local_presence", {}).get("status") == "removed_local"
                or row.get("current_state_status") == "removed_local"):
            continue
        require(type(row.get("enabled")) is bool and type(row.get("connection_read_allowed", False)) is bool,
                "registry connection permissions must be booleans")
    return value


def select_value(source: Any, selector: dict) -> Any:
    """No stringification, execution, fuzzy headings, regex or first-match fallback."""
    if "path" in selector:
        current = source
        for part in selector["path"]:
            if type(part) is str:
                require(type(current) is dict and part in current, "mapped key is absent")
            else:
                require(type(current) is list and part < len(current), "mapped index is absent")
            current = current[part]
        return current
    require(type(source) is str, "Markdown source required")
    lines = source.splitlines()
    headings = [(i, len(match.group(1)), match.group(2).strip()) for i, line in enumerate(lines)
                if (match := re.fullmatch(r"(#{1,6})\s+(.+?)\s*", line))]
    matches = [(i, level) for i, level, title in headings if title == selector["heading"]]
    require(len(matches) == 1, "Markdown heading absent or ambiguous")
    start, level = matches[0]
    end = next((i for i, depth, _ in headings if i > start and depth <= level), len(lines))
    values = []
    for line in lines[start + 1:end]:
        text = re.sub(r"^\s*[-*]\s+", "", line).strip()
        prefix = selector["label"] + ":"
        if text.startswith(prefix):
            values.append(text[len(prefix):].strip())
    require(len(values) == 1, "Markdown label absent or ambiguous")
    return values[0]
