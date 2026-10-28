"""Bounded, private child process for the registered light-novel state only.

The full YAML document is parsed and checked, including history; only declared
facts leave this process. No source data or exception text is written to logs.
"""
import copy
import json
import resource
import sys
from pathlib import Path

MEMORY_LIMIT = 512 * 1024 * 1024
CPU_SECONDS = 30
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_HEADER_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def main():
    try:
        # Do not raise inherited limits. Failure to install either limit is a
        # closed failure, not an unbounded fallback in the caller's process.
        for kind, wanted in ((resource.RLIMIT_AS, MEMORY_LIMIT), (resource.RLIMIT_CPU, CPU_SECONDS)):
            soft, hard = resource.getrlimit(kind)
            if hard != resource.RLIM_INFINITY:
                wanted = min(wanted, hard)
            if soft != resource.RLIM_INFINITY:
                wanted = min(wanted, soft)
            resource.setrlimit(kind, (wanted, wanted))
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import yaml
        from hub.connection_records import FIELDS, SECRET, RecordError, get_field, put_field, require, typed
        from hub.connection_normalize import normalize_source
        from hub.connections import UniqueLoader, _unique_mapping, select_value

        packet = sys.stdin.buffer.read(4 + MAX_HEADER_BYTES + MAX_SOURCE_BYTES + 1)
        require(len(packet) >= 4, "missing request header")
        size = int.from_bytes(packet[:4], "big")
        require(0 < size <= MAX_HEADER_BYTES and 4 + size <= len(packet), "invalid request header")
        header = json.loads(packet[4:4 + size])
        data = packet[4 + size:]
        require(len(data) <= MAX_SOURCE_BYTES, "source exceeds read budget")
        source = header["source"]
        require(header["declaration"]["project_id"] == "light-novel"
                and source.get("id") == "state" and source.get("role") == "current_state"
                and source.get("path") == "governance/round_state.yaml" and source.get("format") == "yaml",
                "invalid large source identity")
        text = data.decode("utf-8")
        loader_base = getattr(yaml, "CSafeLoader", UniqueLoader)
        require(not any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                        for token in yaml.scan(text, Loader=loader_base)), "YAML aliases/anchors are not state facts")
        if loader_base is UniqueLoader:
            loader = UniqueLoader
        else:
            class Loader(loader_base):
                pass
            Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)
            loader = Loader
        parsed = yaml.load(text, Loader=loader)
        declaration = header["declaration"]
        rule = declaration["mapping"].get("current_work.status")
        if (rule is not None and rule["value_map"] is None
                and rule["selector"] == {"path": ["codex_governance", "current_work", "status"]}
                and select_value(parsed, rule["selector"]) == "in_progress"):
            # Explicit, source-specific semantic mapping; the raw value and
            # mapping are retained in the field proof.
            declaration = copy.deepcopy(declaration)
            declaration["mapping"]["current_work.status"]["value_map"] = {"in_progress": "active"}
        value = normalize_source(parsed, declaration, source,
                                 header["metadata"], header["observed_at"])
        # A valid full source may contain a declared value that the bounded Hub
        # DTO cannot represent. Leave that whole field unknown; never truncate
        # a list or emit its raw value. The original source stays authoritative.
        for field, proof in list(value["field_provenance"].items()):
            try:
                typed(get_field(value["business"], field), FIELDS[field])
                require(not SECRET.search(json.dumps(proof, ensure_ascii=False, allow_nan=False)),
                        "sensitive field evidence")
            except (RecordError, TypeError, ValueError):
                put_field(value["business"], field, None)
                del value["field_provenance"][field]
                value["unknown_fields"][field] = (
                    "Declared value is outside Hub type, size or privacy limits; the complete source remains unchanged.")
        response = {"ok": True, "value": value, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "limits": {"address_space_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
                               "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU)[0]}}
        raw = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        require(len(raw) <= MAX_OUTPUT_BYTES, "normalized facts exceed output budget")
        sys.stdout.buffer.write(raw)
    except MemoryError:
        sys.stdout.buffer.write(b'{"ok":false,"failure":"memory_limit"}')
    except Exception:
        sys.stdout.buffer.write(b'{"ok":false,"failure":"invalid_or_limit"}')


if __name__ == "__main__":
    main()
