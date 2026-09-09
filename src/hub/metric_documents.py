"""Body-free numeric projections of selected document metadata.

Only selected identifiers, statuses and declared versions participate in hashes.
Missing metadata is unknown; no workflow, prose, image or review body is opened.
"""
import json
import re

from hub.connection_records import content_hash, timestamp
from hub.metric_sources import read_json, read_metadata, read_structured
from hub.metrics import issue, metric


class Projection:
    def __init__(self, root, pid, observed, prefix):
        self.root, self.pid, self.observed, self.prefix = root, pid, observed, prefix
        self.rows, self.problems = [], []
        self.dimensions = {}

    def problem(self, path, *, kind='read_failure'):
        self.problems.append(issue(self.pid, self.prefix + '_metadata_unknown', path,
            kind=kind, recovery_condition='Repair the selected authoritative metadata and recollect.'))

    def read(self, path, structured=False, metadata_root=None):
        try:
            obj, _ = (read_structured if structured else read_json)(self.root if metadata_root is None else metadata_root, path)
            if not isinstance(obj, dict):
                raise ValueError('object required')
            return obj
        except (OSError, ValueError, TypeError):
            self.problem(path)
            return {}

    def emit(self, name, value, unit, path, semantic=None, dims=None, basis=None, business_at=None, reason=None):
        if business_at is not None:
            try:
                timestamp(business_at, "business_at")
            except (ValueError, TypeError, AttributeError):
                self.problem(path + "#invalid_business_time")
                business_at = None
        if value is None:
            self.problem(path + '#' + name, kind='data_gap')
        self.rows.append(metric(self.pid, self.prefix + '.' + name, value, unit, path,
            content_hash([name, value, semantic, business_at]), self.observed,
            dimensions={**self.dimensions, **(dims or {})}, business_at=business_at,
            reason=(reason or 'Selected authoritative metadata is unavailable, invalid, or does not establish this fact.') if value is None else None,
            counting_basis=basis or 'Unique declared metadata identities within this selected source; no quality or approval inference.'))

    def finish(self):
        return dict(metrics=self.rows, issues=self.problems,
            disposition='partial' if self.problems else 'resolved',
            source_version=content_hash([(r['metric_id'], r['dimensions'], r['source_version']) for r in self.rows]))


def ids(rows, key='id', pattern=r'[A-Za-z0-9_.-]{1,180}'):
    if not isinstance(rows, list) or any(not isinstance(x, dict) or not isinstance(x.get(key), str)
        or not re.fullmatch(pattern, x[key]) for x in rows):
        return None
    result = [x[key] for x in rows]
    return sorted(result) if len(set(result)) == len(result) else None


def count(value):
    return value if type(value) is int and value >= 0 else None


def collect_study(root, pid, observed_at, spec=None):
    p = Projection(root, pid, observed_at, 'study')
    progress = p.read('progress.json')
    course = progress.get('active_course_id')
    course_valid = isinstance(course, str) and bool(re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', course))
    p.dimensions = {'active_course_id': course if course_valid else 'unknown'}
    rounds, groups = None, {}
    try:
        raw, _ = read_metadata(root, 'rounds_data.js')
        match = re.fullmatch(r'(?:\s*//[^\n]*\n)*\s*window\.ROUNDS_DATA\s*=\s*(\[.*\])\s*;?\s*', raw.decode(), re.S)
        rounds = json.loads(match.group(1)) if match else None
        if ids(rounds) is None:
            raise ValueError('round identities')
        for item in rounds:
            weeks = item.get('weeks')
            if ids(weeks) is None:
                raise ValueError('week identities')
            if any(not isinstance(week.get('tasks'), list) for week in weeks):
                raise ValueError('task collection')
            tasks = [task for week in weeks for task in week['tasks']]
            group = ids(tasks)
            if group is None:
                raise ValueError('task identities')
            groups[item['id']] = group
    except (OSError, ValueError, TypeError, AttributeError):
        p.problem('rounds_data.js')
        groups = {}
    tasks = progress.get('tasks')
    expected = [t for group in groups.values() for t in group]
    valid = course_valid and bool(groups) and len(set(expected)) == len(expected) and progress.get('version') == 2 and isinstance(tasks, dict) and set(tasks) == set(expected) and all(isinstance(x, dict) for x in tasks.values())
    semantic = sorted(tasks) if valid else None
    p.emit('tasks_assigned', len(tasks) if valid else None, 'tasks', 'progress.json#tasks', semantic)
    statuses = valid and all(type(t.get('done')) is bool for t in tasks.values())
    for name, state in [('tasks_done', True), ('tasks_pending', False)]:
        selected = sorted(k for k,v in tasks.items() if v.get('done') is state) if statuses else None
        p.emit(name, len(selected) if selected is not None else None, 'tasks', 'progress.json#tasks.done', selected,
            basis='Assigned tasks with explicit done boolean; completion does not measure mastery.')
    for rid, tids in sorted(groups.items()):
        states = [(t, tasks[t].get('done')) for t in tids] if valid else None
        p.emit('round_tasks_assigned', len(tids) if valid else None, 'tasks', 'rounds_data.js#id', tids, {'round_id':rid})
        p.emit('round_tasks_done', sum(s is True for _,s in states) if states is not None and all(type(s) is bool for _,s in states) else None,
            'tasks','progress.json#tasks.done',states,{'round_id':rid})
    p.emit('backlog_age_days', None, 'days', 'progress.json#tasks.done_at', basis='Continuous pending age requires historical continuity; date or resettable timestamps do not establish it.')
    return p.finish()


def collect_story(root, pid, observed_at, spec=None):
    p = Projection(root,pid,observed_at,'story')
    production = 'season-0-daily-v005'
    production_id = 'story_faceless_utopia__production__season_0_daily_v005'
    p.dimensions = {'production_id': production_id}
    base = 'productions/scripts/' + production + '/'
    season, manifest = p.read(base+'season.json'), p.read(base+'manifest.json')
    episode_ids = ids(season.get('episode_refs'), 'episode_id', r'EP\d{2}')
    valid = season.get('schema_version') == 'season0_daily_season_v0_5' and season.get('production_id') == production_id and episode_ids is not None and count(season.get('episode_count')) == len(episode_ids)
    p.emit('episodes_declared',len(episode_ids) if valid else None,'episodes',base+'season.json#episode_refs',episode_ids)
    arts = ids(manifest.get('artifacts'),'artifact_id')
    mvalid = manifest.get('schema_version') == 'production_manifest_v0_1' and manifest.get('production_id') == production_id and manifest.get('production_version') == 'v005' and arts is not None and count(manifest.get('artifact_count')) == len(arts)
    p.emit('artifacts_declared',len(arts) if mvalid else None,'artifacts',base+'manifest.json#artifacts',arts)
    reviewpath='workbench/editorial_review/season-0-daily-v003/verdicts/'+production+'.json'
    review=p.read(reviewpath); entries=review.get('episodes')
    rvalid=valid and review.get('schema_version') == 'season0_daily_human_editorial_review_v0_3' and review.get('production_id') == production_id and review.get('source_id') == production and isinstance(entries,dict) and set(entries) == set(episode_ids) and all(isinstance(v,dict) and v.get('verdict') in ('pending','advance','revise','rewrite') for v in entries.values())
    for state in ('pending', 'advance', 'revise', 'rewrite'):
        selected=sorted(k for k,v in entries.items() if v['verdict']==state) if rvalid else None
        p.emit('editorial_'+state,len(selected) if selected is not None else None,'episodes',reviewpath+'#episodes.verdict',selected,
            business_at=review.get('updated_at') if rvalid else None)
    phases=season.get('arc_phases')
    phase_ids=ids(phases,'phase_id')
    ranges=[]
    if valid and phase_ids is not None:
        for phase in phases:
            start,end=phase.get('episode_start'),phase.get('episode_end')
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(episode_ids):
                ranges=[];break
            ranges.append((phase['phase_id'],list(range(start,end+1))))
        flattened=[n for _,ns in ranges for n in ns]
        if sorted(flattened)!=list(range(1,len(episode_ids)+1)):
            ranges=[]
    if ranges:
        for phase, ns in ranges:
            p.emit('phase_episodes',len(ns),'episodes',base+'season.json#arc_phases',[phase,ns],{'phase_id':phase})
    else:
        p.emit('phase_episodes',None,'episodes',base+'season.json#arc_phases')
    p.emit('backlog_age_days',None,'days',reviewpath+'#updated_at')
    return p.finish()


def collect_zarathustra(root,pid,observed_at,spec=None):
    p=Projection(root,pid,observed_at,'zarathustra')
    index=p.read('source/de/index.json'); units=ids(index.get('units'))
    valid=units is not None and count(index.get('unit_count'))==len(units)
    p.emit('source_units',len(units) if valid else None,'units','source/de/index.json#units',units)
    coverage=p.read('analysis/modern_reader/coverage.json'); entries=coverage.get('units')
    cvalid=valid and coverage.get('schema_version')=='zarathustra_modern_reader_coverage_v0_1' and isinstance(entries,dict) and set(entries)==set(units) and coverage.get('expected_unit_count')==len(units) and all(isinstance(v,dict) for v in entries.values())
    accepted=sorted((k,v.get('version')) for k,v in entries.items() if v.get('status')=='accepted') if cvalid else None
    statuses=cvalid and all(v.get('status') in ('not_started','drafted','blind_reviewed','fidelity_reviewed','accepted') and count(v.get('version')) is not None for v in entries.values())
    p.emit('modern_reader_accepted',len(accepted) if statuses and coverage.get('accepted_count')==len(accepted) else None,'units','analysis/modern_reader/coverage.json#units',accepted)
    claims=p.read('analysis/claims.json'); claimids=ids(claims.get('claims'))
    claims_valid=valid and claimids is not None and count(claims.get('claim_count'))==len(claimids) and all(isinstance(x.get('source_unit_ids'),list) and all(isinstance(u,str) for u in x['source_unit_ids']) and len(set(x['source_unit_ids']))==len(x['source_unit_ids']) and set(x['source_unit_ids']) <= set(units) for x in claims['claims'])
    p.emit('claims',len(claimids) if claims_valid else None,'claims','analysis/claims.json#claims',[(x['id'],sorted(x['source_unit_ids'])) for x in claims['claims']] if claims_valid else None)
    current=p.read('workbench/editorial_review/current.yaml',True); sid=current.get('source_id')
    current_valid=current.get('schema_version')=='zarathustra_editorial_review_current_v0_1' and isinstance(sid,str) and re.fullmatch(r'phase1d-modern-reader-v\d{3}',sid)
    path='workbench/editorial_review/sources/'+sid+'/manifest.json' if current_valid else 'workbench/editorial_review/current.yaml'
    editorial_root = (spec or {}).get('editorial_root')
    if current_valid and isinstance(editorial_root, str):
        relative = sid + '/manifest.json'
        manifest = p.read(relative, metadata_root=editorial_root)
        path = editorial_root.rstrip('/') + '/' + relative
    else:
        manifest=p.read(path) if current_valid else {}
    mvalid=valid and current_valid and manifest.get('schema_version')=='zarathustra_editorial_review_bundle_v0_6' and manifest.get('source_id')==sid and count(manifest.get('item_count'))==len(units)
    for name,field in [('review_bundle_items','item_count'),('review_bundle_accepted','accepted_count')]:
        value=count(manifest.get(field)) if mvalid else None
        if value is not None and value>len(units): value=None
        p.emit(name,value,'units',path+'#'+field,[sid,value],basis='Current bundle declared metadata; bundle acceptance is not human editorial approval.')
    p.emit('current_editorial_pending',None,'units','adapter:zarathustra.current_editorial_pending',sid,
        reason='Current verdict and feedback fields are not integrated into this metadata adapter.',
        basis='Not integrated: current candidate-bound editorial verdicts and feedback.')
    p.emit('current_reviews_passed',None,'reviews','adapter:zarathustra.current_reviews_passed',sid,
        reason='Current candidate-bound review pass fields are not integrated into this metadata adapter.',
        basis='Not integrated: current candidate-bound review coverage; historical reviews do not establish current pass.')
    return p.finish()


def collect_cognitive(root,pid,observed_at,spec=None):
    p=Projection(root,pid,observed_at,'cognitive')
    registry=p.read('data/topic_registry.json'); topics=registry.get('topics'); topicids=ids(topics)
    valid=registry.get('version')=='0.1.0' and topicids is not None
    p.emit('topics_registered',len(topicids) if valid else None,'topics','data/topic_registry.json#topics',topicids)
    source=p.read('data/source_registry.json'); sourceids=ids(source.get('sources'))
    p.emit('sources_registered',len(sourceids) if source.get('version')=='0.1.0' and sourceids is not None else None,'sources','data/source_registry.json#sources',sourceids)
    published=[]; statuses_valid=valid
    for topic in topics if valid else []:
        tid=topic['id']; folder=topic.get('output_dir')
        if not isinstance(folder,str): folder='invalid_metadata_path'
        card=p.read(folder+'/topic_card.json'); references=p.read(folder+'/references.json'); images=p.read(folder+'/image_manifest.json')
        cv=card.get('id')==tid
        status=card.get('status')
        if not cv or status not in ('planned','drafting','review','published','deprecated'):
            statuses_valid=False
        elif status=='published': published.append(tid)
        refs=ids(references.get('sources'))
        rv=references.get('article_id')==tid and refs is not None
        p.emit('topic_sources',len(refs) if rv else None,'sources',folder+'/references.json#sources',refs,{'topic_id':tid})
        for name,key in [('topic_sources_required','min_total'),('topic_academic_official_required','min_academic_or_official')]:
            p.emit(name,count(references.get(key)) if references.get('article_id')==tid else None,'sources',folder+'/references.json#'+key,None,{'topic_id':tid})
        imageids=ids(images.get('images')); iv=images.get('article_id')==tid and imageids is not None
        p.emit('topic_images_declared',len(imageids) if iv else None,'images',folder+'/image_manifest.json#images',imageids,{'topic_id':tid})
        states=[(x['id'],x.get('status')) for x in images['images']] if iv else []
        p.emit('topic_images_planned',sum(s=='planned' for _,s in states) if iv and all(s in ('planned','generated','approved','rejected','complete') for _,s in states) else None,'images',folder+'/image_manifest.json#images.status',states,{'topic_id':tid})
    p.emit('topics_published',len(published) if statuses_valid else None,'topics','data/topic_registry.json#topics.output_dir',published,
        basis='Explicit published topic-card status within registered topics only; no formal article completion inference.')
    p.emit('backlog_age_days',None,'days','data/topic_registry.json#created_at')
    return p.finish()
