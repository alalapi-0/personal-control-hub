"""Read-only, epoch-bound storage accounting; never executes storage checks."""
from pathlib import Path

from hub.connection_records import content_hash
from hub.metric_documents import Projection, count
from hub.metric_sources import read_structured


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _identities(value):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        return None
    return sorted({content_hash(x) for x in value})


def collect_storage(root, pid, observed_at, spec=None):
    """Use STATE.yaml and only a configured, exactly matching closure report.

    spec requires evidence_root (absolute directory) and evidence_path (relative
    filename). A changed pointer requires owner configuration; no history search.
    """
    spec = spec or {}
    p = Projection(root, pid, observed_at, 'storage')
    state_path = 'STATE.yaml'
    evidence_root, evidence_path = spec.get('evidence_root'), spec.get('evidence_path')

    def ref(base, path):
        return 'sha256:' + content_hash([str(base), path])

    state_ref = ref(root, state_path)
    evidence_ref = ref(evidence_root, evidence_path)
    p.dimensions = {'scope': 'historical_migration_epoch', 'epoch': evidence_ref}

    def read(base, path, source):
        try:
            data, _ = read_structured(base, path)
            if not isinstance(data, dict):
                raise ValueError('object required')
            return data
        except (OSError, ValueError, TypeError):
            p.problem(source)
            return {}

    state = read(root, state_path, state_ref)
    state_valid = type(state.get('schema_version')) is int and state['schema_version'] == 3
    accounting = _mapping(state.get('project_accounting')) if state_valid else {}
    state_time = _mapping(state.get('metadata')).get('updated_at') if state_valid else None
    basis = 'Explicit STATE accounting for the selected historical migration epoch; not current disk inventory.'
    for field, unit in [('cleaned_local_source_roots', 'roots'),
                        ('existing_external_projects_linked', 'projects'),
                        ('pending_projects', 'projects'), ('active_projects', 'projects'),
                        ('remaining_execution_candidates', 'projects'),
                        ('released_bytes', 'bytes'), ('external_added_bytes', 'bytes')]:
        p.emit(field, count(accounting.get(field)), unit, state_ref + '#' + field,
               basis=basis, business_at=state_time)
    removed = _identities(accounting.get('removed_projects'))
    p.emit('removed_projects', len(removed) if removed is not None else None, 'projects',
           state_ref + '#removed_projects', removed, basis=basis + ' Unique hashed declared identities.',
           business_at=state_time)

    configured = (isinstance(evidence_root, str) and Path(evidence_root).is_absolute()
                  and isinstance(evidence_path, str) and bool(evidence_path)
                  and not Path(evidence_path).is_absolute() and '..' not in Path(evidence_path).parts)
    expected = str(Path(evidence_root) / evidence_path) if configured else None
    bound = state_valid and configured and _mapping(state.get('closure')).get('final_report') == expected
    report = read(evidence_root, evidence_path, evidence_ref) if bound else {}
    if not bound:
        p.problem(evidence_ref + '#closure_pointer', kind='data_gap')
    report_valid = type(report.get('schema_version')) is int and report['schema_version'] == 1
    report_time = report.get('verified_at') if report_valid else None
    historical_basis = 'Recorded historical acceptance only; no current validation or delivery claim.'
    for field, accepted in [('identity_guard', 'PASS'),
                            ('external_copy_validation', 'PASS_before_each_source_cleanup')]:
        # Only this exact schema token establishes a pass; prose is not parsed.
        value = 1 if report_valid and report.get(field) == accepted else None
        p.emit('historical_' + field + '_passed', value, 'checks', evidence_ref + '#' + field,
               business_at=report_time, basis=historical_basis)
    retained = _identities(report.get('retained_local_sources')) if report_valid else None
    p.emit('historical_retained_local_sources', len(retained) if retained is not None else None,
           'roots', evidence_ref + '#retained_local_sources', retained, business_at=report_time,
           basis=historical_basis + ' Unique hashed retained source identities.')
    p.emit('current_validation_failures', None, 'checks', 'adapter:storage.current_validation_failures',
           dims={'scope': 'current_validation'},
           basis='No current validation evidence is collected; historical acceptance cannot establish zero failures.',
           reason='Current validation is not collected by this read-only historical adapter.')
    return p.finish()
