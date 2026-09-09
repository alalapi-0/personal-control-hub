"""Read one saved RPN fixture result; never execute a test or read connection data."""
from datetime import datetime
import hashlib
from pathlib import Path
import re

from hub.connection_records import RecordError, timestamp
from hub.metric_documents import Projection, count
from hub.metric_guard_report import _current_files
from hub.metric_sources import read_metadata, metadata_path
from hub.connections import parse_source
from hub.metrics import issue

SOURCES = (
    'scripts/test_client_generation.sh', 'scripts/lib/client_fixture_result.py',
    'scripts/lib/storage_runtime.sh', 'scripts/external-sing-box',
    'scripts/generate_singbox_config.sh', 'scripts/generate_shadowrocket_link.sh',
    'scripts/generate_shadowrocket_config.sh', 'scripts/generate_shadowrocket_macos_config.sh',
    'scripts/validate_client_artifacts.sh', 'scripts/validate_xray_config.sh',
    'scripts/validate_shadowrocket_link.sh', 'scripts/lib/check_ai_workflow_domains.sh',
    'templates/singbox_client_template.json',
    'templates/singbox_client_ios_legacy_1.11.4_template.json',
    'templates/shadowrocket_client.conf.template',
    'templates/shadowrocket_macos_ai_workflow.conf.template',
)
POSITIVE = ('modern_template', 'legacy_schema', 'artifact_validation', 'transaction_backup')
NEGATIVE = ('duplicate_manifest', 'invalid_dns_style', 'unauthorized_external_output',
            'legacy_mixed', 'symlink_output', 'symlink_ancestor', 'dangling_symlink',
            'misindexed_artifacts', 'unsafe_permissions', 'missing_artifact',
            'unexpected_artifact', 'unapproved_transaction_backup')


def _aggregate(files):
    return hashlib.sha256(b''.join(name.encode() + b'\0' + files[name].encode() + b'\n'
                                  for name in SOURCES)).hexdigest()


def collect_fixture_report(root, pid, observed_at, spec):
    p = Projection(root, pid, observed_at, 'validation.client_fixture')
    p.dimensions = {'report_id': 'client-generation-placeholder', 'scope': 'saved_fixture_run',
                    'binding_scope': 'selected_source_files'}
    base, path = Path(spec['report_root']), spec['report_path']
    source = str(base / path)
    try:
        if base.resolve(strict=True) != base or metadata_path(base, path).stat().st_size > 8192:
            raise ValueError('report path or byte budget')
        raw, _ = read_metadata(base, path)
        if len(raw) > 8192:
            raise ValueError('report byte budget')
        data = parse_source(raw, 'json')
        if (type(data) is not dict or data.get('schema_version') != 'rpn.client-fixture-result.v1'
                or data.get('test_id') != 'client-generation-placeholder'
                or not re.fullmatch(r'[0-9a-f-]{36}', str(data.get('run_id', '')))):
            raise ValueError('report identity')
    except (OSError, ValueError, TypeError):
        data = {}
        p.problem(source + '#identity')
    business_at = data.get('finished_at')
    try:
        timestamp(business_at, 'finished_at')
    except (ValueError, TypeError, AttributeError):
        business_at = None
    def emit(name, value, unit, basis, selected=None):
        p.emit(name, value, unit, source + '#' + name, selected, business_at=business_at, basis=basis)

    counts = {}
    for name, allowed in (('positive', POSITIVE), ('negative', NEGATIVE)):
        group = data.get(name, {})
        group = group if type(group) is dict else {}
        rows = group.get('completed')
        valid = (type(rows) is list and len(rows) <= len(allowed)
                 and all(type(x) is str and x in allowed for x in rows)
                 and len(set(rows)) == len(rows) and group.get('completed_count') == len(rows)
                 and type(group.get('completed_count')) is int and group.get('expected') == len(allowed))
        counts[name] = sorted(rows) if valid else None
        emit(name + '.completed', len(rows) if valid else None, 'cases',
             'Completed unique named fixture cases in this saved run, not validator assertions or product features.', counts[name])
        emit(name + '.expected', len(allowed) if data else None, 'cases',
             'Expected case identities for this producer schema; never a passed count.')
    manifest, generated = count(data.get('manifest_count')), count(data.get('artifact_count'))
    if manifest is not None and manifest > 1000:
        manifest = None
    if generated is not None and (generated > 1000 or manifest is not None and generated > manifest):
        generated = None
    emit('manifest_entries', manifest, 'artifacts', 'Runtime manifest entry count; unknown before the manifest was loaded.')
    emit('generated_artifacts', generated, 'artifacts', 'Successful generator returns with an existing fixture file; fixture files are subsequently deleted.')
    code = data.get('code', {})
    code = code if type(code) is dict else {}
    files = code.get('files')
    valid_code = (type(files) is dict and set(files) == set(SOURCES)
                  and all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for v in files.values()))
    valid_code = bool(valid_code and code.get('stable') is True
                      and _aggregate(files) == code.get('sha256') == code.get('sha256_after'))
    current = None
    if valid_code:
        try:
            current = _current_files(root, SOURCES)
        except (OSError, RecordError, ValueError, TypeError):
            p.problem('selected-source-files#identity')
    bound = int(current == files) if valid_code and current is not None else None
    emit('bound_to_selected_code', bound, 'boolean',
         'All 16 declared repository source hashes and aggregate match; external guard and current environment are excluded.', current)
    modern = data.get('modern', {})
    modern = modern if type(modern) is dict else {}
    legacy = data.get('legacy', {})
    legacy = legacy if type(legacy) is dict else {}
    for name, value, allowed in (
        ('modern_binary_checks_complete', modern.get('binary_validation'), ('pass', 'not_completed')),
        ('legacy_schema_complete', legacy.get('schema'), ('pass', 'not_completed')),
        ('cleanup_complete', data.get('cleanup'), ('pass', 'not_created', 'pending', 'fail')),
    ):
        emit(name, int(value == 'pass') if value in allowed else None, 'boolean',
             'Saved producer state only; no current execution or live network acceptance.')
    emit('legacy_binary_checks_executed', 0 if legacy.get('target') == '1.11.4' and
         legacy.get('binary_validation') == 'not_run' else None, 'checks',
         'Producer explicitly did not run the legacy 1.11.4 binary; zero executions is not a pass.')
    binary_hash = modern.get('sha256_before')
    environment = (type(binary_hash) is str and re.fullmatch('[0-9a-f]{64}', binary_hash)
                   and modern.get('sha256_after') == binary_hash and modern.get('unchanged') is True
                   and type(modern.get('version')) is str
                   and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', modern['version']))
    emit('environment_recorded', int(bool(environment)) if data else None, 'boolean',
         'Saved actual validator version and stable binary hashes; no equality with current runtime.',
         [binary_hash, modern.get('version')] if environment else None)
    status, exit_code = data.get('status'), data.get('exit_code')
    complete = (counts['positive'] == sorted(POSITIVE) and counts['negative'] == sorted(NEGATIVE)
                and manifest is not None and manifest > 0 and generated == manifest
                and data.get('cleanup') == 'pass' and data.get('stage') == 'complete'
                and modern.get('binary_validation') == 'pass' and legacy.get('schema') == 'pass'
                and data.get('event_stream_valid') is True and bool(environment) and valid_code)
    status_valid = type(exit_code) is int and 0 <= exit_code <= 255 and (
        status == 'fail' and exit_code != 0 or status == 'pass' and exit_code == 0 and complete)
    passed = int(status == 'pass') if status_valid else None
    emit('saved_run_passed', passed, 'boolean', 'Saved outcome after consistency checks; historical success survives later source changes.')
    emit('current_selected_code_passed', passed if bound == 1 else None, 'boolean',
         'Saved result bound only to the selected current source files; runtime equality and network health are not established.', current)
    duration = age = None
    if business_at:
        finish = datetime.fromisoformat(business_at.replace('Z', '+00:00'))
        age = (datetime.fromisoformat(observed_at.replace('Z', '+00:00')) - finish).total_seconds()
        age = age if age >= 0 else None
        try:
            timestamp(data.get('started_at'), 'started_at')
            duration = (finish - datetime.fromisoformat(data['started_at'].replace('Z', '+00:00'))).total_seconds()
            duration = duration if duration >= 0 else None
        except (ValueError, TypeError, AttributeError):
            pass
    emit('duration', duration, 'seconds', 'Recorded finished_at minus started_at; negative or unknown times stay unknown.')
    emit('age', age, 'seconds', 'Collection time minus recorded business time; filesystem mtime is not a business timestamp.')
    if passed == 0:
        p.problems.append(issue(pid, 'client_fixture_failed', source, kind='validation_failure', affected_items=1,
                                updated_at=business_at, recovery_condition='Repair the isolated test failure and run the normal producer.'))
    return p.finish()
