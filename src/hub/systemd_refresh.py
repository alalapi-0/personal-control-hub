"""Render the task-owned local refresh units; never control other user services."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from zoneinfo import ZoneInfo
from hub.refresh_schedule import schedule_policy

MARKER = "# Managed by Personal Control Hub daily refresh installer."
SERVICE = "personal-control-hub-refresh.service"
TIMER = "personal-control-hub-refresh.timer"


def _argument(path: Path) -> str:
    value = str(path)
    if not path.is_absolute() or any(c in value for c in "\x00\n\r"):
        raise ValueError("Unit paths must be absolute and single-line.")
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    value = value.replace('$', '$$')
    return '"' + value + '"'


def preflight_runtime(program_id: str, manifest: Path | None = None) -> dict:
    """Verify this loaded program against the pinned existing runtime manifest."""
    import sys
    from .rr_binding import verify_program
    from .connection_records import require
    code_root = Path(__file__).absolute().parents[2]
    expected = code_root / 'rr-runtime-manifest.json'
    manifest = expected if manifest is None else Path(manifest)
    require(bool(re.fullmatch('[0-9a-f]{64}', program_id)), 'invalid pinned program identity')
    require(manifest.is_absolute() and '..' not in manifest.parts and manifest == expected,
            'manifest must belong to the executing code root')
    import json
    from .rr_cli import _regular
    from .rr_payload import RRFeedbackError
    from .connection_records import RecordError
    captured = {}
    def checked_reader(path, maximum, *, private=False):
        raw = _regular(path, maximum, private=private)
        if path == manifest: captured['manifest'] = raw
        return raw
    try:
        source = verify_program(code_root, program_id, reader=checked_reader)
    except RRFeedbackError:
        raise RecordError('runtime manifest verification failed') from None
    files = json.loads(captured['manifest'])['files']
    require({'scripts/hub_sync.py', 'src/hub/connection_cli.py', 'src/hub/systemd_refresh.py'} <= set(files),
            'service entry and precheck must be manifested')
    for name, module in tuple(sys.modules.items()):
        if name == 'hub' or name.startswith('hub.'):
            origin = getattr(module, '__file__', None)
            require(origin is not None, 'loaded Hub module has no file identity')
            try: relative = str(Path(origin).absolute().relative_to(code_root))
            except ValueError:
                require(False, 'loaded Hub dependency is outside the manifested code root')
            require(relative in files, 'loaded Hub dependency is not manifested')
    return {'interface': 'pch-refresh-service-v1', 'program_id': program_id, 'source': source,
            'runtime_manifest': runtime_manifest('refresh', expected_program_id=program_id)}



def runtime_manifest(role, *, metadata=None, expected_program_id=None, process_identity=None):
    """Bounded self-process observation of this program, never adoption proof.

    Metadata is internal server/Root injection, not an HTTP selection or grant.
    No Git, environment, credentials, other PID, registry or source-root reads.
    """
    import copy, hashlib, os, time
    from .connection_records import require
    from .workbench_store import canonical
    from .rr_binding import private_json, verify_program
    from .rr_payload import RRFeedbackError
    require(role in {'web', 'refresh', 'worker'}, 'invalid runtime role')
    metadata = copy.deepcopy(metadata or {})
    require(type(metadata) is dict and set(metadata) <= {'build_id', 'dirty_inputs', 'config_versions', 'interfaces', 'review_packages', 'artifacts'}, 'invalid runtime metadata')
    sha = lambda v: type(v) is str and re.fullmatch('[0-9a-f]{64}', v) is not None
    token = lambda v: type(v) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', v) is not None
    for field in ('config_versions', 'dirty_inputs'):
        value = metadata.get(field)
        require(value is None or type(value) is dict and len(value) <= 64 and all(token(k) and sha(v) for k, v in value.items()), 'invalid version inputs')
    require(metadata.get('build_id') is None or sha(metadata['build_id']), 'invalid build identity')
    interfaces = metadata.get('interfaces', {})
    require(type(interfaces) is dict and len(interfaces) <= 32, 'invalid interfaces')
    for key, value in interfaces.items():
        require(token(key) and type(value) is dict and set(value) == {'version', 'compatible_versions'} and token(value['version']), 'invalid interface')
        allowed = value['compatible_versions']
        require(type(allowed) is list and 1 <= len(allowed) <= 16 and all(token(v) for v in allowed) and len(set(allowed)) == len(allowed) and value['version'] in allowed, 'invalid compatibility declaration')
    for field in ('review_packages', 'artifacts'):
        rows = metadata.get(field, {})
        require(type(rows) is dict and len(rows) <= 32, 'invalid version references')
        for key, value in rows.items():
            require(token(key) and type(value) is dict and set(value) == {'version', 'sha256', 'inputs_sha256'} and token(value['version']) and sha(value['sha256']) and sha(value['inputs_sha256']), 'invalid version reference')
    root = Path(__file__).absolute().parents[2]
    package = None; source = None
    try:
        package = private_json(root / 'rr-runtime-manifest.json')
        pinned = package.get('program_id') if expected_program_id is None else expected_program_id
        source = verify_program(root, pinned)
        required = {'src/hub/systemd_refresh.py'} | {'web': {'scripts/hub_server.py'},
            'worker': {'src/hub/task_worker.py'}, 'refresh': {'scripts/hub_sync.py', 'src/hub/connection_cli.py'}}[role]
        if not required <= set(package['files']): raise ValueError('unmanifested role')
    except (OSError, ValueError, TypeError, KeyError, RRFeedbackError):
        package = None; source = None
    pid = os.getpid(); ticks = None
    try: ticks = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError): pass
    owned = ticks is not None and (process_identity is None or type(process_identity) is dict and process_identity.get('pid') == pid and str(process_identity.get('start_ticks')) == ticks)
    program = package['program_id'] if package else None
    roles = {name: {'state': 'not_observed', 'pid': None, 'start_ticks': None, 'program_id': None,
                    'unknown_reason': 'No exact owned process observation for this role.'} for name in ('web', 'refresh', 'worker')}
    if owned:
        roles[role] = {'state': 'observed', 'pid': pid, 'start_ticks': ticks, 'program_id': program,
                       'unknown_reason': None if program else 'Current process observed; program identity is not verified.'}
    else: roles[role]['unknown_reason'] = 'Supplied process identity does not match this observer; no foreign process was read.'
    schemas = {}
    for key, name in (('task_package', 'schemas/rebuild/task-package.schema.json'), ('receipt', 'schemas/rebuild/receipt.schema.json')):
        schemas[key] = {'sha256': (package or {}).get('files', {}).get(name, {}).get('sha256'),
                        'version': interfaces.get(key, {}).get('version'),
                        'unknown_reason': 'Schema interface version not declared.' if name in (package or {}).get('files', {}) else 'Schema is not published by the verified program.'}
    dirty_inputs = metadata.get('dirty_inputs')
    if source and source['dirty'] is False:
        require(not dirty_inputs, 'clean source cannot publish dirty inputs'); dirty_inputs = {}
    value = {'schema_version': 'pch-runtime-manifest/1', 'observer': {'pid': pid, 'start_ticks': ticks, 'scope': 'self_process'},
             'roles': roles, 'observed_at': time.time(), 'source': {'commit': source['commit'] if source else None,
                 'dirty': source['dirty'] if source else None, 'dirty_inputs': dirty_inputs,
                 'unknown_reason': 'Dirty input versions not published.' if source and source['dirty'] and dirty_inputs is None else None if source else 'No verified program source provenance.'},
             'program': {'program_id': program, 'manifest_sha256': hashlib.sha256(canonical(package)).hexdigest() if package else None,
                         'build_id': metadata.get('build_id'), 'state': 'verified' if package else 'unknown'},
             'schemas': schemas, 'interfaces': interfaces, 'config_versions': metadata.get('config_versions', {}),
             'review_packages': metadata.get('review_packages', {}), 'artifacts': metadata.get('artifacts', {}),
             'unknown_reasons': {key: 'Version metadata not published.' for key in ('build_id', 'config_versions', 'interfaces', 'review_packages', 'artifacts') if not metadata.get(key)},
             'adoption_proven': False, 'execution_admitted': False}
    require(len(canonical(value)) <= 32768, 'runtime manifest capacity')
    return value


def public_runtime_manifest(value):
    """Validate an internal observer's public projection without reading files."""
    import copy
    from .connection_records import require
    from .workbench_store import canonical
    keys = {'schema_version', 'observer', 'roles', 'observed_at', 'source', 'program', 'schemas', 'interfaces', 'config_versions', 'review_packages', 'artifacts', 'unknown_reasons', 'adoption_proven', 'execution_admitted'}
    require(type(value) is dict and set(value) == keys and value['schema_version'] == 'pch-runtime-manifest/1' and len(canonical(value)) <= 32768, 'invalid runtime observation')
    # A closed vocabulary prevents raw provider/error/path/message fields from
    # being exposed by an incorrectly wired internal callback.
    forbidden = {'path', 'root', 'cwd', 'body', 'messages', 'prompt', 'credentials', 'environment'}
    def inspect(item, field=None):
        if type(item) is dict:
            require(not forbidden.intersection(item), 'private runtime field')
            for key, child in item.items():
                require(type(key) is str and '/' not in key and '\\' not in key, 'private runtime key'); inspect(child, key)
        elif type(item) is list:
            for child in item: inspect(child)
        elif type(item) is str:
            if field == 'schema_version' and item == 'pch-runtime-manifest/1': return
            require(len(item) <= 512 and '/' not in item and '\\' not in item and not any(ord(c) < 32 for c in item), 'private runtime text')
        else: require(item is None or type(item) in (bool, int, float), 'invalid runtime value')
    inspect(value)
    require(value['adoption_proven'] is False and value['execution_admitted'] is False, 'runtime observation is not authority')
    return copy.deepcopy(value)


def compare_runtime_manifests(observed, expected):
    """Directional compatibility; version difference alone is not a fault."""
    from .connection_records import require
    from .workbench_store import canonical
    require(all(type(v) is dict and v.get('schema_version') == 'pch-runtime-manifest/1' and len(canonical(v)) <= 32768 for v in (observed, expected)), 'invalid runtime comparison')
    checks = []
    def compare(key, actual, wanted):
        state = 'unknown' if actual is None or wanted is None else 'match' if actual == wanted else 'check_required'
        checks.append({'field': key, 'status': state})
    for section, fields in (('source', ('commit', 'dirty', 'dirty_inputs')), ('program', ('program_id', 'build_id'))):
        for key in fields: compare(section + '.' + key, observed.get(section, {}).get(key), expected.get(section, {}).get(key))
    for section in ('schemas', 'config_versions', 'review_packages', 'artifacts'):
        a, b = observed.get(section, {}), expected.get(section, {})
        # Fixed section names only: never echo caller/provider keys or paths.
        compare(section, a or None, b or None)
    a, b = observed.get('interfaces', {}), expected.get('interfaces', {})
    if not a or not b: checks.append({'field': 'interfaces', 'status': 'unknown'})
    else:
        state = 'match'
        for key, wanted in b.items():
            current = a.get(key)
            if current is None: state = 'unknown'; continue
            require(type(wanted) is dict and type(wanted.get('compatible_versions')) is list, 'invalid interface comparison')
            if current.get('version') not in wanted['compatible_versions']:
                state = 'incompatible'; break
        checks.append({'field': 'interfaces', 'status': state})
    status = next((s for s in ('incompatible', 'check_required', 'unknown') if any(c['status'] == s for c in checks)), 'match')
    return {'status': status, 'checks': checks, 'adoption_proven': False, 'execution_admitted': False}

def render_units(root: Path, code_root: Path, python: Path, *,
                 time: str = "09:00", timezone: str = "Asia/Tokyo", incremental: bool = False,
                 unit_prefix: str = "personal-control-hub-refresh", interval_hours: int = 12,
                 program_id: str | None = None, runtime_manifest: Path | None = None) -> dict[str, str]:
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',unit_prefix):raise ValueError('Invalid unit prefix.')
    policy = schedule_policy({'interval_hours': interval_hours})
    if time!='09:00' or timezone!='Asia/Tokyo':
        raise ValueError('Snapshot dispatch requires the 09:00 Asia/Tokyo anchor.')
    service_name=unit_prefix+'.service';timer_name=unit_prefix+'.timer'
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time):
        raise ValueError("Time must be HH:MM.")
    if not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z_+-]+)+", timezone):
        raise ValueError("Timezone must be an IANA area/location.")
    ZoneInfo(timezone)
    root_arg, python_arg = _argument(root), _argument(python)
    # WorkingDirectory is a path setting, not an ExecStart argument list:
    # systemd retains quote characters there and rejects the resulting path.
    working_directory = str(root).replace('%', '%%')
    script = _argument(code_root / "scripts/hub_sync.py")
    mode='sync --scheduled'
    precheck=''
    if runtime_manifest is not None and program_id is None:
        raise ValueError('A manifest requires a pinned program identity.')
    if program_id is not None:
        if not re.fullmatch('[0-9a-f]{64}', program_id): raise ValueError('Invalid program identity.')
        manifest = runtime_manifest if runtime_manifest is not None else code_root / 'rr-runtime-manifest.json'
        if manifest != code_root / 'rr-runtime-manifest.json':
            raise ValueError('Manifest must be inside the same frozen program.')
        binding = '--program-id ' + program_id + ' --runtime-manifest ' + _argument(manifest)
        precheck = f'ExecStartPre={python_arg} {script} --root {root_arg} service-precheck-v1 {binding}\n'
        mode = 'service-scheduled-v1 ' + binding
    service = f"""{MARKER}
[Unit]
Description=Personal Control Hub finite local source refresh

[Service]
Type=oneshot
WorkingDirectory={working_directory}
{precheck}ExecStart={python_arg} {script} --root {root_arg} {mode}
UMask=0077
NoNewPrivileges=true
Nice=10
TimeoutStartSec=300
UnsetEnvironment=OPENAI_API_KEY ANTHROPIC_API_KEY HUB_OWNER_TOKEN
"""
    evening_calendar='OnCalendar=*-*-* 21:00:00 Asia/Tokyo\n' if policy['interval_hours'] == 12 else ''
    timer = f"""{MARKER}
[Unit]
Description=Personal Control Hub periodic source snapshots

[Timer]
OnCalendar=*-*-* {time}:00 {timezone}
{evening_calendar}Persistent=true
AccuracySec=1min
Unit={service_name}

[Install]
WantedBy=timers.target
"""
    return {service_name: service, timer_name: timer}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write only new Hub 12/24-hour snapshot units; enable separately.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--program-id", help="Opt in to verified service-v1 using this frozen program identity")
    parser.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--time", default="09:00")
    parser.add_argument("--timezone", default="Asia/Tokyo")
    parser.add_argument("--incremental",action='store_true',help='Deprecated compatibility option; uses the configured snapshot cadence')
    parser.add_argument("--interval-hours", type=int, choices=(12,24), default=12)
    parser.add_argument("--unit-prefix",default='personal-control-hub-refresh',help='Explicit isolated test unit identity if needed')
    parser.add_argument("--unit-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    units = render_units(args.root, args.code_root, args.python,
                         time=args.time, timezone=args.timezone,incremental=args.incremental,unit_prefix=args.unit_prefix,interval_hours=args.interval_hours,program_id=args.program_id,runtime_manifest=args.runtime_manifest)
    # Never overwrite an existing operator-owned or previously installed unit.
    # Changes use an explicit reviewed preimage rather than a broad replace flag.
    args.unit_dir.mkdir(parents=True, exist_ok=True)
    if args.unit_dir.is_symlink() or any((args.unit_dir / name).exists()
                                        or (args.unit_dir / name).is_symlink() for name in units):
        parser.error("Unit directory is unsafe or a target already exists; inspect its exact preimage first.")
    for name, text in units.items():
        with (args.unit_dir / name).open("x", encoding="utf-8") as stream:
            stream.write(text)
    print("Wrote refresh service and timer. Verify, then enable the timer with systemctl --user.")
    return 0
