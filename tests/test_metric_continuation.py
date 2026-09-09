import hashlib
import json
from pathlib import Path

import tempfile
import unittest

from hub.metric_continuation import collect_continuation

OBS = '2026-09-09T00:00:00Z'


def write(root, path, value):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value))


def source(tmp_path):
    uid, iid = 'u1', 'import1'
    base = 'universes/u1'
    path = base + '/imports/import1/manifest.json'
    entries = [dict(chapter_number=i, raw_sha256='a'*64, byte_count=12, source_uri='never-follow/'+str(i)) for i in range(1,4)]
    digest = hashlib.sha256(''.join(e['raw_sha256']+'  '+e['source_uri']+'\n' for e in entries).encode()).hexdigest()
    manifest = dict(schema_version='complete_novel_import_manifest_v1', universe_id=uid, import_id=iid, source_file_manifest=entries, source_manifest_hash=digest, source_chapter_count=3, chapter_count=2, selected_chapter_range=[1,2], total_characters=20, total_paragraphs=4, updated_at='2026-07-18T00:00:00Z', chapter_units=[dict(chapter_id='c'+str(i), order=i, source_hash='b'*64, character_count=10, paragraph_count=2) for i in (1,2)])
    write(tmp_path, path, manifest)
    write(tmp_path, base+'/universe.json', dict(schema_version='universe_record_v1', universe_id=uid, imports=[dict(import_id=iid,manifest_path=path,source_manifest_hash=digest)], source_refs=[dict(ref_id=iid,uri=path,source_manifest_hash=digest)]))
    req = dict(universe_id=uid, import_id=iid, manifest_path=path, source_manifest_hash=digest, requested_chapters=[1,2])
    for jid in ('job1','job2'):
        jp = base+'/runs/real_api_reverse/jobs/'+jid
        write(tmp_path, jp+'/job.json', dict(schema_version='real_api_reverse_job_v1', job_id=jid, universe_id=uid, request=req, requested_chapters=[1,2], committed_chapters=[1], status='stopped', attempt_ids=['a1'], current_attempt_id='a1'))
        write(tmp_path, jp+'/attempts/a1/attempt.json', dict(schema_version='real_api_reverse_attempt_v1', job_id=jid, attempt_id='a1', status='stopped', error='finish_reason_hard_stop:length', finished_at='2026-07-18T00:00:00Z'))
    return tmp_path


def collect(root):
    return collect_continuation(root, 'continuation-test', OBS, dict(data_root=str(root), universe_id='u1'))


def val(result, name):
    return [m['value'] for m in result['metrics'] if m['metric_id'].endswith('.'+name)]


def mutate(root, path, fn):
    full = root/path
    v = json.loads(full.read_text()); fn(v); write(root,path,v)


MANIFEST = 'universes/u1/imports/import1/manifest.json'
JOB = 'universes/u1/runs/real_api_reverse/jobs/job1/job.json'


def test_counts_union_and_semantic_stability(source):
    first = collect(source)
    assert val(first,'source_chapters') == [3]
    assert val(first,'imported_chapters') == [2]
    assert val(first,'characters') == [20]
    assert val(first,'committed_chapters_unique') == [1]
    assert val(first,'blocker_age_days') == [53,53]
    assert first == collect(source)
    mutate(source, MANIFEST, lambda x:x.update(title='body changed', description='untrusted prose'))
    assert first['source_version'] == collect(source)['source_version']


def test_field_isolation(source):
    mutate(source, MANIFEST, lambda x:x.update(total_characters=True))
    result = collect(source)
    assert val(result, 'characters') == [None]
    assert val(result, 'paragraphs') == [4]


def test_duplicate_commits(source):
    mutate(source, JOB, lambda x:x.update(committed_chapters=[1,1]))
    assert val(collect(source),'committed_chapters_unique') == [None]


def test_invalid_status(source):
    mutate(source, JOB, lambda x:x.update(status='bogus'))
    result = collect(source)
    assert val(result,'source_chapters') == [3]
    assert val(result,'job_status') == [None,1]


def test_invalid_job_identity(source):
    mutate(source, JOB, lambda x:x.update(universe_id='other'))
    assert val(collect(source),'committed_chapters_unique') == [None]


def test_binding_mismatch(source):
    mutate(source,MANIFEST,lambda x:x.update(source_manifest_hash='0'*64))
    assert val(collect(source),'source_chapters') == [None]


def test_symlink_rejected(source):
    target=source/MANIFEST
    other=source/'metadata.json';target.rename(other);target.symlink_to(other)
    assert val(collect(source),'source_chapters') == [None]


def test_path_escape_rejected(source):
    mutate(source,'universes/u1/universe.json',lambda x:x['imports'][0].update(manifest_path='../../outside.json'))
    assert val(collect(source),'source_chapters') == [None]


def test_missing_jobs_unknown(source):
    import shutil
    shutil.rmtree(source/'universes/u1/runs')
    assert val(collect(source),'jobs') == [None]


def test_negative_paragraphs_isolated(source):
    mutate(source, MANIFEST, lambda x:x['chapter_units'][0].update(paragraph_count=-1))
    result = collect(source)
    assert val(result,'paragraphs') == [None]
    assert val(result,'characters') == [20]


def test_attempt_identity(source):
    path = 'universes/u1/runs/real_api_reverse/jobs/job1/attempts/a1/attempt.json'
    mutate(source,path,lambda x:x.update(job_id='other'))
    result = collect(source)
    assert val(result,'attempt_status') == [None,1]
    assert val(result,'committed_chapters_unique') == [1]


def test_unknown_reason_not_exposed(source):
    path = 'universes/u1/runs/real_api_reverse/jobs/job1/attempts/a1/attempt.json'
    mutate(source,path,lambda x:x.update(error='private raw error body'))
    assert 'private raw error body' not in json.dumps(collect(source))


def test_blocker_issue_and_rooted_refs(source):
    result = collect(source)
    blockers = [i for i in result['issues'] if i['kind'] == 'business_blocker']
    assert len(blockers) == 2
    assert all(i['affected_items'] == 1 and i['started_at'] == '2026-07-18T00:00:00Z' for i in blockers)
    assert all(i['recovery_condition'] for i in blockers)
    assert all(row['source_ref'].startswith(str(source)+'/') for row in result['metrics'] + result['issues'])


def test_same_counts_source_rebinding(source):
    before = collect(source)
    manifest = json.loads((source/MANIFEST).read_text())
    manifest['source_file_manifest'][0]['raw_sha256'] = 'c'*64
    digest = hashlib.sha256(''.join(e['raw_sha256']+'  '+e['source_uri']+'\n' for e in manifest['source_file_manifest']).encode()).hexdigest()
    manifest['source_manifest_hash'] = digest
    write(source, MANIFEST, manifest)
    mutate(source,'universes/u1/universe.json',lambda x:[item.update(source_manifest_hash=digest) for key in ('imports','source_refs') for item in x[key]])
    for jid in ('job1','job2'):
        mutate(source,'universes/u1/runs/real_api_reverse/jobs/'+jid+'/job.json',lambda x:x['request'].update(source_manifest_hash=digest))
    after = collect(source)
    old = {(m['metric_id'],json.dumps(m['dimensions'],sort_keys=True)):m for m in before['metrics']}
    for metric in after['metrics']:
        prior = old[(metric['metric_id'],json.dumps(metric['dimensions'],sort_keys=True))]
        assert metric['value'] == prior['value']
        if 'import_id' in metric['dimensions']:
            assert metric['source_version'] != prior['source_version']


def test_queued_without_attempts_invalid_but_chapters_preserved(source):
    mutate(source,JOB,lambda x:x.update(status='queued',attempt_ids=[],current_attempt_id=None,committed_chapters=[]))
    result = collect(source)
    assert val(result,'attempt_status') == [None,1]
    assert val(result,'job_requested_chapters') == [2,2]
    assert val(result,'job_committed_chapters') == [0,1]
    assert len([i for i in result['issues'] if i['kind'] == 'business_blocker']) == 1


class ContinuationTests(unittest.TestCase):
    pass


def _case(function):
    def run(self):
        with tempfile.TemporaryDirectory() as folder:
            function(source(Path(folder)))
    return run


for _name, _function in list(globals().items()):
    if _name.startswith('test_'):
        setattr(ContinuationTests, _name, _case(_function))
