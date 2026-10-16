"""Render the task-owned local refresh units; never control other user services."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from zoneinfo import ZoneInfo

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


def render_units(root: Path, code_root: Path, python: Path, *,
                 time: str = "09:00", timezone: str = "Asia/Tokyo", incremental: bool = False,
                 unit_prefix: str = "personal-control-hub-refresh") -> dict[str, str]:
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',unit_prefix):raise ValueError('Invalid unit prefix.')
    if incremental and (time!='09:00' or timezone!='Asia/Tokyo'):
        raise ValueError('Scheduled daily dispatch currently requires 09:00 Asia/Tokyo.')
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
    mode='--scheduled' if incremental else '--daily'
    service = f"""{MARKER}
[Unit]
Description=Personal Control Hub finite local source refresh

[Service]
Type=oneshot
WorkingDirectory={working_directory}
ExecStart={python_arg} {script} --root {root_arg} sync {mode}
UMask=0077
NoNewPrivileges=true
Nice=10
TimeoutStartSec=300
UnsetEnvironment=OPENAI_API_KEY ANTHROPIC_API_KEY HUB_OWNER_TOKEN
"""
    minute_calendar='OnCalendar=*-*-* *:*:00 Asia/Tokyo\n' if incremental else ''
    accuracy='1s' if incremental else '1min'
    timer = f"""{MARKER}
[Unit]
Description=Personal Control Hub daily local source refresh

[Timer]
OnCalendar=*-*-* {time}:00 {timezone}
{minute_calendar}Persistent=true
AccuracySec={accuracy}
Unit={service_name}

[Install]
WantedBy=timers.target
"""
    return {service_name: service, timer_name: timer}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write only new Hub daily refresh user units; enable separately.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--time", default="09:00")
    parser.add_argument("--timezone", default="Asia/Tokyo")
    parser.add_argument("--incremental",action='store_true',help='Render the same service with 60-second checks and 09:00 full batch')
    parser.add_argument("--unit-prefix",default='personal-control-hub-refresh',help='Explicit isolated test unit identity if needed')
    parser.add_argument("--unit-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    units = render_units(args.root, args.code_root, args.python,
                         time=args.time, timezone=args.timezone,incremental=args.incremental,unit_prefix=args.unit_prefix)
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
