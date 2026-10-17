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


def render_units(root: Path, code_root: Path, python: Path, *,
                 time: str = "09:00", timezone: str = "Asia/Tokyo", incremental: bool = False,
                 unit_prefix: str = "personal-control-hub-refresh", interval_hours: int = 12) -> dict[str, str]:
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
    mode='--scheduled'
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
    parser.add_argument("--time", default="09:00")
    parser.add_argument("--timezone", default="Asia/Tokyo")
    parser.add_argument("--incremental",action='store_true',help='Deprecated compatibility option; uses the configured snapshot cadence')
    parser.add_argument("--interval-hours", type=int, choices=(12,24), default=12)
    parser.add_argument("--unit-prefix",default='personal-control-hub-refresh',help='Explicit isolated test unit identity if needed')
    parser.add_argument("--unit-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    units = render_units(args.root, args.code_root, args.python,
                         time=args.time, timezone=args.timezone,incremental=args.incremental,unit_prefix=args.unit_prefix,interval_hours=args.interval_hours)
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
