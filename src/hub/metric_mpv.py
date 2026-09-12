"""Bounded saved MPV working-instance facts; no media or execution inference."""
import re
from pathlib import PurePosixPath

from hub.connection_records import content_hash, timestamp
from hub.metric_documents import Projection
from hub.metric_sources import metadata_path, read_json, MAX_METADATA_BYTES
from hub.metrics import issue

MAX_FILES = 100
MAX_BYTES = 32 * 1024 * 1024
BASIS = ('Selected historical local working instance, not a customer project or current code/test acceptance. '
         'Counts are saved metadata only; current application-code binding is unknown.')


def _id(value):
    return type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', value))


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _time(value, p, path):
    try:
        timestamp(value, 'saved timestamp')
        return value
    except (ValueError, TypeError, AttributeError):
        p.problem(path)
        return None


def collect_mpv(root, pid, observed_at, spec=None):
    p = Projection(root, pid, observed_at, 'mpv')
    p.dimensions = {'scope': 'historical_work_instance', 'current_code_binding': 'unknown'}
    files = (spec or {}).get('project_files')
    if (not isinstance(files, list) or not files or len(files) > MAX_FILES or
            any(type(f) is not str or len(f) > 700 for f in files) or len(set(files)) != len(files)):
        p.emit('declared_instances', None, 'working_instances', 'adapter:mpv.project_files', basis=BASIS)
        return p.finish()
    # Validate the entire explicit selection before reads; never discover more files.
    paths, used = {}, 0
    for relative in files:
        try:
            parts = PurePosixPath(relative).parts
            if (len(parts) != 4 or parts[0] != '.local-runtime' or parts[2] != 'projects' or
                    not parts[3].endswith('.mpvclip.json')):
                raise ValueError('not working-instance metadata')
            path = metadata_path(root, relative)
            size = path.stat().st_size
            if size > MAX_METADATA_BYTES or used + size > MAX_BYTES:
                raise ValueError('metadata budget')
            used += size
            paths[relative] = path
        except (OSError, ValueError, TypeError):
            p.problem('adapter:mpv.project_files#' + content_hash(relative))
    p.emit('declared_instances', len(files), 'working_instances', 'adapter:mpv.project_files',
           semantic=sorted(files), basis=BASIS)
    readable, identities = [], []
    for relative in sorted(files):
        if relative not in paths:
            continue
        try:
            obj, _ = read_json(root, relative)
            if not isinstance(obj, dict):
                raise ValueError('object required')
        except (OSError, ValueError, TypeError):
            p.problem(relative)
            continue
        readable.append(relative)
        project = obj.get('project_id')
        schema = obj.get('schema_version')
        application = obj.get('application_version')
        valid = _id(project) and type(schema) is int and schema == 1
        dims = {'artifact_id': content_hash(relative), 'project_identity': content_hash(project) if _id(project) else 'unknown',
                'schema_version': schema if type(schema) is int and schema == 1 else 'unknown',
                'application_version': application if _id(application) else 'unknown'}
        if valid:
            identities.append([relative, project])
        created = _time(obj.get('created_at'), p, relative + '#created_at')
        updated = _time(obj.get('updated_at'), p, relative + '#updated_at')
        # Source versions depend solely on the selected fact and relevant declarations.
        def emit(name, value, unit, field, selected=None, segment=None, when=updated):
            sd = {'segment_identity': content_hash([relative, project, segment])} if segment is not None else {}
            p.emit(name, value, unit, relative + '#' + field,
                   semantic=[schema, application if _id(application) else None, selected],
                   dims={**dims, **sd}, business_at=when, basis=BASIS)
        emit('saved_created_timestamp_known', 1 if created else None, 'timestamps', 'created_at', created, when=created)
        source = obj.get('source')
        source = source if isinstance(source, dict) else {}
        source_valid = valid and _id(source.get('source_id'))
        emit('source_duration', _number(source.get('duration_us')) if source_valid else None,
             'microseconds', 'source.duration_us', source.get('source_id') if source_valid else None)
        segments = obj.get('segments')
        segments_valid = (valid and isinstance(segments, list) and
                          all(isinstance(s, dict) and _id(s.get('segment_id')) for s in segments) and
                          len({s['segment_id'] for s in segments}) == len(segments))
        emit('segments_declared', len(segments) if segments_valid else None, 'segments', 'segments',
             sorted(s['segment_id'] for s in segments) if segments_valid else None)
        if not segments_valid:
            continue
        for s in sorted(segments, key=lambda s: s['segment_id']):
            sid = s['segment_id']
            when = _time(s.get('updated_at'), p, relative + '#segments.updated_at')
            def seg(name, value, unit, field, selected=None):
                emit(name, value, unit, 'segments.' + field, selected, sid, when)
            seg('segment_enabled', int(s['enabled']) if type(s.get('enabled')) is bool else None,
                'segments', 'enabled')
            decision = s.get('decision')
            seg('segment_saved_pending', int(decision == 'pending') if _id(decision) else None,
                'segments', 'decision', content_hash(decision) if _id(decision) else None)
            seg('segment_duration', _number(s.get('duration_us')), 'microseconds', 'duration_us')
            # Output boundary is not an approval or proof that an output file exists.
            boundary = s.get('actual_output_boundary')
            boundary_valid = (isinstance(boundary, dict) and _number(boundary.get('in_us')) is not None and
                              _number(boundary.get('out_us')) is not None and boundary['out_us'] >= boundary['in_us'])
            seg('saved_output_boundary_duration', boundary['out_us'] - boundary['in_us'] if boundary_valid else None,
                'microseconds', 'actual_output_boundary',
                [boundary['in_us'], boundary['out_us']] if boundary_valid else None)
            results = s.get('validation_results')
            results_valid = (isinstance(results, list) and all(isinstance(v, dict) and _id(v.get('checker'))
                             and _id(v.get('status')) for v in results) and
                             len({v['checker'] for v in results}) == len(results))
            seg('saved_validation_results', len(results) if results_valid else None, 'checks', 'validation_results',
                sorted(v['checker'] for v in results) if results_valid else None)
            for status in ('pass', 'fail'):
                selected = sorted(v['checker'] for v in results if v['status'] == status) if results_valid else None
                seg('saved_validation_' + status, len(selected) if selected is not None else None, 'checks',
                    'validation_results.status', selected)
                if status == 'fail' and selected:
                    p.problems.append(issue(pid, 'mpv_saved_validation_fail',
                        relative + '#segment/' + content_hash([relative, project, sid]),
                        affected_items=1, kind='historical_work_instance_validation',
                        recovery_condition='Review saved failed checks for this historical instance; current-code acceptance remains unknown.'))
    p.emit('readable_instances', len(readable), 'working_instances', 'adapter:mpv.project_files', semantic=readable, basis=BASIS)
    p.emit('identity_covered_instances', len(identities), 'working_instances', 'adapter:mpv.project_files', semantic=identities, basis=BASIS)
    p.emit('total_saved_instances', len(identities) if len(identities) == len(files) else None,
           'working_instances', 'adapter:mpv.project_files', semantic=identities, basis=BASIS)
    return p.finish()
