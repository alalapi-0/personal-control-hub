"""Explicit check scope. Selecting a scope never grants action authority."""
from __future__ import annotations
import os
import json
from pathlib import Path
import yaml

TASK_ID = 'ALL-PROJECTS-CODEX-GOVERNANCE-V1'
WORKBENCH_TASK_ID = 'HUB-LINUX-VISUAL-WORKBENCH-V1'
TASK_KEYS = {TASK_ID: 'all_projects_governance', WORKBENCH_TASK_ID: 'linux_visual_workbench'}
ENV_NAME = 'HUB_GOVERNANCE_TASK'
BOOT_CONTRACT_VERSION = 2


class UniqueKeyLoader(yaml.SafeLoader):
    """Validate the whole authority before extracting a task."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError('Duplicate YAML key in canonical state')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_canonical_state(root: Path) -> dict:
    try:
        state = yaml.load((root / 'STATE.yaml').read_text(encoding='utf-8'), Loader=UniqueKeyLoader)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ValueError('Invalid canonical STATE.yaml') from exc
    if (not isinstance(state, dict) or not isinstance(state.get('metadata'), dict)
            or state['metadata'].get('authority') != 'canonical'):
        raise ValueError('STATE.yaml must be canonical')
    return state


def task_entry(state: dict, task_id: str | None = None) -> dict:
    task_id = selected_task() if task_id is None else task_id
    if task_id not in TASK_KEYS:
        raise ValueError('Unknown Hub governance task scope')
    task = state.get(TASK_KEYS[task_id])
    required = {'task_id', 'status', 'next_action', 'candidate_paths'}
    if task_id == WORKBENCH_TASK_ID:
        required |= {'current_round', 'next_round', 'stage', 'plan', 'round_cards', 'acceptance_contract', 'authorization'}
    else:
        required |= {'unit', 'execution_document', 'delivery'}
    if (not isinstance(task, dict) or not required <= task.keys()
            or task.get('task_id') != task_id or not isinstance(task.get('next_action'), str)
            or not task['next_action'].strip()):
        raise ValueError('Selected task management entry is malformed')
    if task_id == WORKBENCH_TASK_ID:
        if (not isinstance(task['authorization'], dict) or not isinstance(task['stage'], int)
                or any(not isinstance(task[k], str) or not task[k] for k in
                       ('current_round', 'next_round', 'plan', 'round_cards', 'acceptance_contract'))):
            raise ValueError('Selected workbench task fields are malformed')
    return task


def candidate_paths(root: Path, task: dict) -> list[str]:
    paths = task.get('candidate_paths')
    if not isinstance(paths, list) or not paths:
        raise ValueError('Current task candidate_paths must list owned files')
    for path in paths:
        if (not isinstance(path, str) or not path or Path(path).is_absolute()
                or any(part in {'..', '.', '', '.git'} for part in path.split('/'))):
            raise ValueError('Invalid candidate path for scoped Git check')
        current = root
        for part in Path(path).parts:
            current /= part
            if current.is_symlink():
                raise ValueError('Candidate path cannot traverse symlinks')
    return sorted(set([*paths, 'STATE.yaml']))


def boot_packet(root: Path, task_id: str | None = None) -> bytes:
    """AGENTS plus a deterministic task projection, never a second authority."""
    state = load_canonical_state(root)
    rules = (root / 'AGENTS.md').read_bytes()
    if task_id is None:
        task_id = selected_task()
    if task_id is None:
        return rules + (root / 'STATE.yaml').read_bytes()
    task = task_entry(state, task_id)
    candidate_paths(root, task)
    if task_id == WORKBENCH_TASK_ID:
        # History/evidence remain in STATE; entry readers need current execution facts.
        # Path inventory stays in STATE and is validated above. It is not copied into
        # the entry packet: the historical list no longer fits the 8192-byte cap.
        fields = ('task_id', 'status', 'implementation_status', 'stage', 'current_round', 'next_round',
                  'next_action', 'lane', 'owned_repository', 'scope', 'plan', 'round_cards',
                  'acceptance_contract', 'authorization', 'blockers', 'evidence_index')
        task = {k: task[k] for k in fields if k in task}
    projection = {'boot_contract_version': BOOT_CONTRACT_VERSION,
                  'metadata': state['metadata'], TASK_KEYS[task_id]: task}
    return rules + json.dumps(projection, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')).encode('utf-8')


def selected_task() -> str | None:
    value = os.environ.get(ENV_NAME) or None
    if value is not None and value not in TASK_KEYS:
        raise ValueError('Unknown Hub governance task scope')
    return value


def add_scope_argument(parser):
    parser.add_argument('--task-id', choices=list(TASK_KEYS), default=selected_task(),
                        help='Select project governance checks; does not authorize effects')


def activate_scope(value):
    if value is not None and value not in TASK_KEYS:
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
