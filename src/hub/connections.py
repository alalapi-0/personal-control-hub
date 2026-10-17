"""Read-only registry loading and typed source selectors."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from hub.connection_records import RecordError, identifier, require, string, validate_selector


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


def parse_owned_hub_state(data: bytes) -> dict:
    """Read Hub's canonical state, whose historical entries may share YAML nodes.

    Only the resolver's own Hub root may use this adapter. External sources keep
    the ordinary alias prohibition; duplicate keys and unsafe tags still fail.
    Selected business values are independently bounded and typed before publish.
    """
    try:
        value = yaml.load(data.decode('utf-8'), Loader=UniqueLoader)
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise RecordError('Hub canonical state structure invalid') from exc
    require(type(value) is dict and type(value.get('metadata')) is dict
            and value['metadata'].get('authority') == 'canonical',
            'Hub state must declare canonical authority')
    return value


def validate_registry_value(value: Any) -> dict:
    require(type(value) is dict and type(value.get("projects")) is list, "registry projects required")
    ids = [row.get("id") for row in value["projects"] if type(row) is dict]
    require(len(ids) == len(value["projects"]) and all(type(x) is str for x in ids)
            and len(ids) == len(set(ids)), "registry project identities invalid or duplicated")
    for row in value["projects"]:
        identifier(row["id"], "registry project")
        string(row.get("name"), "registry project name")
        if "project_definition" in row:
            # Extensions import selectors from this module; defer to avoid a
            # module import cycle while sharing the exact same fact contract.
            from .connection_extensions import validate_project_definition
            validate_project_definition(row["project_definition"])
        # Removed rows remain reportable even when malicious activation flags are present.
        if (row.get("local_presence", {}).get("status") == "removed_local"
                or row.get("current_state_status") == "removed_local"):
            continue
        require(type(row.get("enabled")) is bool and type(row.get("connection_read_allowed", False)) is bool,
                "registry connection permissions must be booleans")
    return value


def select_value(source: Any, selector: dict) -> Any:
    """No stringification, execution, fuzzy headings, regex or first-match fallback."""
    validate_selector(selector)
    if "path" in selector:
        current = source
        for part in selector["path"]:
            # An explicit null subtree publishes no fact at any deeper path.
            if current is None:
                return None
            if type(part) is str:
                require(type(current) is dict and part in current, "mapped key is absent")
            else:
                require(type(current) is list and part < len(current), "mapped index is absent")
            current = current[part]
        return current
    require(type(source) is str, "Markdown source required")
    lines = _markdown_visible_lines(source)
    headings = [(i, len(match.group(1)), match.group(2).strip()) for i, line in enumerate(lines)
                if (match := re.fullmatch(r"(#{1,6})\s+(.+?)\s*", line))]
    matches = [(i, level) for i, level, title in headings if title == selector["heading"]]
    require(len(matches) == 1, "Markdown heading absent or ambiguous")
    start, level = matches[0]
    end = next((i for i, depth, _ in headings if i > start and depth <= level), len(lines))
    section = lines[start + 1:end]
    if "table_label" in selector:
        return _markdown_table_value(section, selector["table_label"])
    if "ordered_item" in selector:
        return _markdown_ordered_value(section, selector["ordered_item"])
    values = []
    label = selector.get("boolean_label", selector.get("label"))
    for line in section:
        text = re.sub(r"^\s*[-*]\s+", "", line).strip()
        prefix = label + ":"
        if text.startswith(prefix):
            values.append(text[len(prefix):].strip())
    require(len(values) == 1, "Markdown label absent or ambiguous")
    if "boolean_label" in selector:
        require(values[0] in {"true", "false"}, "Markdown boolean: literal true/false required")
        return values[0] == "true"
    return values[0]


def _markdown_visible_lines(source: str) -> list[str]:
    """Ignore fenced examples without letting their headings end a section."""
    lines = []
    fence = None
    for line in source.splitlines():
        if fence:
            marker, size = fence
            if re.fullmatch(r" {0,3}" + re.escape(marker) + "{" + str(size) + r",}\s*", line):
                fence = None
            lines.append("")
            continue
        match = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if match and (match.group(1)[0] != "`" or "`" not in match.group(2)):
            fence = (match.group(1)[0], len(match.group(1)))
            lines.append("")
        else:
            lines.append(line)
    return lines


def _markdown_cells(line: str) -> list[str]:
    require(line.startswith("|") and line.endswith("|"), "Markdown table row requires outer pipes")
    trailing_slashes = len(line[:-1]) - len(line[:-1].rstrip("\\"))
    require(trailing_slashes % 2 == 0, "Markdown table closing pipe is escaped")
    cells, cell = [], ""
    escaped = False
    for char in line[1:-1]:
        if char == "|" and not escaped:
            cells.append(cell.strip())
            cell = ""
        else:
            cell += char
        escaped = char == "\\" and not escaped
    cells.append(cell.strip())
    require(len(cells) == 2 and all(cells), "Markdown table must have exactly two nonempty cells")
    return cells


def _markdown_table_value(lines: list[str], label: str) -> str:
    values = []
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            index += 1
            continue
        block = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            block.append(_markdown_cells(lines[index].strip()))
            index += 1
        require(len(block) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in block[1]),
                "Markdown table header/separator malformed")
        require(not any(all(re.fullmatch(r":?-{3,}:?", cell) for cell in row) for row in block[2:]),
                "Markdown table has an ambiguous separator")
        values.extend(row[1] for row in block[2:] if row[0] == label)
    require(len(values) == 1, "Markdown table label absent or ambiguous")
    return values[0]


def _markdown_ordered_value(lines: list[str], number: int) -> str:
    matches = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"([1-9][0-9]*)\. +(.+)", line)
        nested = re.fullmatch(r"\s+([1-9][0-9]*)\. +(.+)", line)
        require(not (nested and nested.group(1) == str(number)), "Markdown ordered item is nested")
        if match and match.group(1) == str(number):
            matches.append((index, match.group(2).strip()))
    require(len(matches) == 1, "Markdown ordered item absent or ambiguous")
    index, value = matches[0]
    require(bool(value), "Markdown ordered item is empty")
    for line in lines[index + 1:]:
        if re.fullmatch(r"[1-9][0-9]*\. +.+", line):
            break
        require(not line.strip(), "Markdown ordered item is multiline or nested")
    return value
