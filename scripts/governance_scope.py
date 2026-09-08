"""Explicit check scope. Selecting a scope never grants action authority."""
from __future__ import annotations
import os
from pathlib import Path

TASK_ID = 'ALL-PROJECTS-CODEX-GOVERNANCE-V1'
ENV_NAME = 'HUB_GOVERNANCE_TASK'


def selected_task() -> str | None:
    value = os.environ.get(ENV_NAME) or None
    if value not in (None, TASK_ID):
        raise ValueError('Unknown Hub governance task scope')
    return value


def add_scope_argument(parser):
    parser.add_argument('--task-id', choices=[TASK_ID], default=selected_task(),
                        help='Select Codex-only checks; does not authorize effects')


def activate_scope(value):
    if value not in (None, TASK_ID):
        raise ValueError('Unknown Hub governance task scope')
    if value:
        os.environ[ENV_NAME] = value
    else:
        os.environ.pop(ENV_NAME, None)


def excluded_path(path: str | Path) -> bool:
    # No stat/resolve is needed to reject a path owned by the excluded host.
    return bool(selected_task()) and any('cursor' in part.lower() for part in Path(path).parts)
