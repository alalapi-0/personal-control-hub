"""Bounded, transaction-consistent Wechat business metadata; no payload reads."""
import sqlite3
import stat
from datetime import datetime, timezone
import re

from hub.connection_records import content_hash
from hub.metric_sources import metadata_path
from hub.metrics import issue, metric

MAX_ROWS = 50000
MAX_VM_STEPS = 5000000
JOB_STATES = {'pending', 'running', 'failed', 'done', 'waiting_confirmation', 'cancelled'}


def collect_wechat(root, project_id, observed_at, spec=None):
    relative = (spec or {}).get('path', 'data/app.sqlite3')
    rows, problems, projections = [], [], {}

    def problem(code, kind='read_failure'):
        problems.append(issue(project_id, 'wechat_' + code, relative,
            kind=kind, recovery_condition='Refresh or repair authoritative metadata and recollect.'))

    times = {}

    def emit(name, data, basis, scope='all_history', reason=None, unit=None):
        unit = unit or ('articles' if name.startswith('articles_') or name == 'current_distinct_articles' else
                        'drafts' if name.startswith('drafts_') else
                        'proofs' if name.startswith('proof_') else 'jobs')
        time_group = 'article_times' if unit == 'articles' else 'job_times' if unit == 'jobs' else None
        selected = [times.get(time_group, {}).get(r[0] if isinstance(r, (list, tuple)) else r)
                    for r in data] if data is not None else []
        business_at = max(selected) if selected and all(selected) else None
        value = len(data) if data is not None else None
        reason = reason or ('Required metadata is missing or invalid.' if data is None else None)
        rows.append(metric(project_id, 'wechat.' + name, value, unit, relative + '#' + name,
            content_hash([name, data, reason, business_at]), observed_at, business_at=business_at, dimensions={'scope': scope},
            reason=reason, counting_basis=basis))

    conn = None
    try:
        path = metadata_path(root, relative)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('ordinary database required')
        for suffix in ('-wal', '-shm', '-journal'):
            metadata_path(root, relative + suffix)
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        ticks = [0]

        def budget():
            ticks[0] += 1000
            return int(ticks[0] > MAX_VM_STEPS)

        conn.set_progress_handler(budget, 1000)
        conn.execute('BEGIN')

        def read(key, table, fields):
            try:
                # Only table/column identifiers from this module are interpolated.
                columns = {r[0] for r in conn.execute(
                    'SELECT name FROM pragma_table_info(?) LIMIT 256', (table,))}
                if not set(fields) <= columns:
                    raise ValueError('missing columns')
                data = conn.execute('SELECT ' + ','.join(fields) + ' FROM ' + table +
                                    ' ORDER BY id LIMIT ?', (MAX_ROWS + 1,)).fetchall()
                if len(data) > MAX_ROWS or any(any(isinstance(v, (bytes, str)) and
                        (isinstance(v, bytes) or len(v) > 100) for v in row) for row in data):
                    raise ValueError('metadata bounds exceeded')
                if any(type(row[0]) is not int or row[0] <= 0 for row in data):
                    raise ValueError('invalid identity')
                if len({row[0] for row in data}) != len(data):
                    raise ValueError('duplicate identity')
                projections[key] = data
            except (sqlite3.Error, ValueError):
                projections[key] = None
                problem(key + '_unavailable')

        read('articles', 'articles', ('id',))
        read('article_times', 'articles', ('id', 'updated_at'))
        read('job_times', 'publish_jobs', ('id', 'updated_at'))
        read('active', 'articles', ('id', 'deleted_at'))
        read('jobs', 'publish_jobs', ('id',))
        read('queue', 'publish_jobs', ('id', 'article_id', 'status'))
        read('modes', 'publish_jobs', ('id', 'adapter_mode'))
        read('drafts', 'wechat_drafts', ('id', 'article_id', 'status'))
        read('draft_modes', 'wechat_drafts', ('id', 'adapter_mode', 'publish_job_id'))
        read('proofs', 'publish_proofs', ('id', 'publish_job_id', 'article_id', 'confirmed_at'))
        after = path.stat()
        if path.is_symlink() or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError('database identity changed')
        conn.rollback()
    except (OSError, ValueError, TypeError, sqlite3.Error):
        projections.clear()
        problem('database_unavailable')
    finally:
        if conn is not None:
            conn.close()

    for group in ('article_times', 'job_times'):
        times[group] = {}
        invalid = False
        for identity, raw in projections.get(group) or []:
            try:
                if not isinstance(raw, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', raw):
                    raise ValueError('unverified timestamp format')
                times[group][identity] = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                invalid = True
        if invalid:
            problem('invalid_' + group)

    articles = projections.get('articles')
    active_rows = projections.get('active')
    active = {r[0] for r in active_rows if r[1] is None} if active_rows is not None else None
    emit('articles_total', articles, 'All article IDs, including soft-deleted historical articles.')
    emit('articles_active', sorted(active) if active is not None else None,
         'Article IDs with deleted_at IS NULL.', 'active_articles')
    emit('jobs_total', projections.get('jobs'), 'All publish job IDs, including historical and cancelled jobs.')
    queue = projections.get('queue')
    identities_valid = queue is not None and all(type(r[1]) is int and r[1] > 0 for r in queue)
    if queue is not None and (not identities_valid or any(r[2] not in JOB_STATES for r in queue)):
        problem('invalid_historical_job_metadata')
    current = [r for r in queue if r[1] in active and r[2] != 'cancelled'] if identities_valid and active is not None else None
    emit('queue_total', [r[0] for r in current] if current is not None else None,
         'All non-cancelled jobs joined to active articles, including unknown statuses.', 'current_queue')
    emit('current_distinct_articles', sorted({r[1] for r in current}) if current is not None else None,
         'Distinct active article IDs referenced by non-cancelled current jobs.', 'current_queue')
    article_ids = {r[0] for r in articles} if articles is not None else None
    emit('orphan_jobs', [r[0] for r in queue if r[1] not in article_ids] if identities_valid and article_ids is not None else None,
         'Historical job IDs whose article_id has no matching article row; deleted articles still match.')
    if current is not None and any(r[2] not in JOB_STATES for r in current):
        problem('invalid_current_job_states')
        current = None
    for state in sorted(JOB_STATES - {'cancelled'}):
        emit('queue_' + state, [r[0] for r in current if r[2] == state] if current is not None else None,
             'Non-cancelled jobs joined to active articles, status=' + state + '; done is not publication proof.',
             'current_queue')
    modes = projections.get('modes')
    allowed_modes = {'mock', 'real', 'browser_assist', 'manual_export', 'unknown'}
    if modes is not None and any(r[1] not in allowed_modes for r in modes):
        problem('invalid_historical_job_modes')
    mode_map = dict(modes) if modes is not None else {}
    done = [r for r in current if r[2] == 'done'] if current is not None else None
    mode_valid = modes is not None and done is not None and all(mode_map.get(r[0]) in allowed_modes for r in done)
    emit('queue_done_real', [r[0] for r in done if mode_map.get(r[0]) == 'real'] if done is not None and mode_valid else None,
         'Current done jobs with stored adapter_mode=real; flag does not verify external publication.', 'current_queue')
    drafts = projections.get('drafts')
    emit('drafts_total', [r[0] for r in drafts] if drafts is not None else None, 'All historical draft row IDs; not published articles.')
    draft_modes = projections.get('draft_modes')
    draft_valid = draft_modes is not None and all(r[1] in {'unknown', 'mock', 'real'} for r in draft_modes)
    emit('drafts_real', [r[0] for r in draft_modes if r[1] == 'real'] if draft_valid else None,
         'All historical drafts explicitly marked real; old schemas cannot establish mode.')
    emit('drafts_linked_job', [r[0] for r in draft_modes if r[2] is not None]
         if draft_modes is not None and all(r[2] is None or type(r[2]) is int and r[2] > 0 for r in draft_modes) else None,
         'Historical draft IDs with an explicit publish_job_id; old schemas cannot establish linkage.')
    proofs = projections.get('proofs')
    emit('proof_rows_total', [r[0] for r in proofs] if proofs is not None else None,
         'All historical proof records; record presence does not independently verify publication.')
    proof_valid = proofs is not None and all(type(r[1]) is int and type(r[2]) is int and
        isinstance(r[3], str) and bool(r[3].strip()) for r in proofs)
    if proofs is not None and not proof_valid:
        problem('invalid_proof_metadata')
    coverage = {(r[1], r[2]) for r in proofs} if proof_valid else set()
    covered = [r[0] for r in done if (r[0], r[1]) in coverage] if done is not None and proof_valid else None
    emit('current_done_with_proof', covered,
         'Distinct current done job IDs with matching job/article proof and nonempty confirmed_at; no proof content inspected.', 'current_queue')
    uncovered = [r[0] for r in done if (r[0], r[1]) not in coverage] if done is not None and proof_valid else None
    emit('current_done_without_proof', uncovered,
         'Current done jobs lacking matching confirmation metadata; not a failed-job or blocker count.', 'current_queue')
    if uncovered:
        problem('publication_unverified', 'unverified_publication')
    emit('pending_oldest_age', None, 'Pending age requires a verified queue-entry timestamp convention.',
         'current_queue', reason='Queue-entry timestamp semantics are not established; scheduled_at is not pending age.', unit='seconds')
    return {'metrics': rows, 'issues': problems, 'disposition': 'partial' if problems else 'resolved',
            'source_version': content_hash([(r['metric_id'], r['source_version']) for r in rows])}
