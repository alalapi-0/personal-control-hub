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
    return {'interface': 'pch-refresh-service-v1', 'program_id': program_id, 'source': source}


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
