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
                        help='Select project governance checks; does not authorize effects')


def activate_scope(value):
    if value not in (None, TASK_ID):
        raise ValueError('Unknown Hub governance task scope')
    if value:
        os.environ[ENV_NAME] = value
    else:
        os.environ.pop(ENV_NAME, None)


def excluded_path(path: str | Path) -> bool:
    # Optional workstation integrations are not project check prerequisites.
    # This is not an author/editor filter: ordinary project paths stay visible.
    parts = Path(path).parts
    optional = {
        'docs/08_codex_cursor_workflow.md', 'docs/13_cursor_mcp_workspace_setup.md',
        'prompts/codex_project_driver.md', 'prompts/cursor_project_driver.md',
        'prompts/cursor_mcp_usage_prompt.md', 'data/codex_queue',
    }
    return bool(selected_task()) and bool(parts) and (
        parts[0] in {'.cursor', '.codex'} or str(path) in optional
    )
