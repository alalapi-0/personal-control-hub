"""Verify committed TC16 refresh; --replay only repeats its identical request."""
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch
import argparse
import builtins
import hashlib
import http.cookiejar
import io
import json
import os
import runpy
import signal
import subprocess
import sys
import tempfile
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from hub.connection_manager_cli import main as cli, load_bundles, result_validator
from hub.connection_refresh import RefreshLedger
from hub.connection_sources import SourceResolver
from hub.design_store import DesignStore
from hub.design_service import DesignService
from hub.project_service import ProjectService

EXECUTED = runpy.run_path(str(UNIT / 'refresh_once.py'))
TARGETS, EXCEPTIONS = EXECUTED['TARGETS'], EXECUTED['EXCEPTIONS']
REQUEST, PRE_HEAD = EXECUTED['REQUEST'], EXECUTED['HEAD']
POST_HEAD = {'sequence':130, 'hash':'f61c071a68d0a22a9e1dc3924a8ad179e5e929465da153c2aeb6049e4ee3dfc1'}
LEDGER = 'data/design_governance/connection_refresh.sqlite3'
def sha(raw): return hashlib.sha256(raw).hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def invoke(args):
    out = io.StringIO()
    with redirect_stdout(out): code = cli(args, root=ROOT)
    assert code == 0
    return json.loads(out.getvalue())

def verify(replay=False):
    baseline = json.loads((UNIT/'baseline.json').read_text())
    changed = [p for p, h in baseline['protected'].items() if sha((ROOT/p).read_bytes()) != h]
    assert not changed, changed
    bundles, validators = load_bundles(ROOT, [f'data/design_governance/authority-bundle-v{i}.json' for i in range(1,5)])
    authority = validators[bundles[-1]['source_plan']['content_hash']].authority
    validate = result_validator(validators)
    ledger = RefreshLedger(ROOT, result_validator=validate, read_only=True)
    after = ledger.history(current_authority=authority)
    assert after['head'] == POST_HEAD
    raw = subprocess.check_output(['git','show',f"{baseline['head']}:{LEDGER}"], cwd=ROOT)
    assert sha(raw) == baseline['owned_preimages'][LEDGER]
    with tempfile.TemporaryDirectory(prefix='tc16-preimage-', dir=UNIT) as temp:
        path = Path(temp)/'pre.sqlite3'
        path.write_bytes(raw)
        before = RefreshLedger(ROOT, path, result_validator=validate, read_only=True).history(current_authority=authority)
    assert before['head'] == PRE_HEAD
    for key in ['requests','events','results']:
        assert {canonical(x) for x in before[key]} <= {canonical(x) for x in after[key]}, key
    req = [x for x in after['requests'] if x['request_id'] == REQUEST]
    assert len(req) == 1 and req[0]['status'] == 'FINISHED'
    assert req[0]['project_ids'] == req[0]['completed_project_ids'] == sorted(TARGETS)
    assert req[0]['remaining_project_ids'] == [] and req[0]['authority'] == authority
    rows = [x for x in after['results'] if x['request_id'] == REQUEST]
    assert len(rows) == 20 and {x['project_id'] for x in rows} == set(TARGETS)
    assert len(after['events']) == 130 and len(after['results']) == 118
    for row in rows:
        assert row['success'] and validate(row['result']) == row['result']
        assert row['result']['authority'] == authority
    recorded = json.loads((UNIT/'refresh.json').read_text())
    assert recorded['resolver_errors'] == {} and recorded['request']['status'] == 'FINISHED'
    assert sorted(recorded['appended_project_ids']) == sorted(TARGETS)
    projection = ledger.rebuild(current_authority=authority)
    assert recorded['projection'] == ledger.rebuild() # Raw refresh receipt is authority-unscoped; current CLI below binds v4.
    manga = os.path.normpath(next(p['root_path'] for p in yaml.safe_load((ROOT/'data/registry/external_projects.yaml').read_text())['projects'] if p['id']=='manga-localizer'))
    counters = {'manga_path_operations':0, 'resolver_calls':0}
    def guard(fn):
        def checked(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                value = os.fsdecode(os.fspath(path))
                absolute = os.path.normpath(value if os.path.isabs(value) else str(ROOT/value))
                if absolute == manga or absolute.startswith(manga+os.sep):
                    counters['manga_path_operations'] += 1
                    raise AssertionError('Manga boundary reached')
            return fn(path,*args,**kwargs)
        return checked
    def no_resolve(*args,**kwargs):
        counters['resolver_calls'] += 1
        raise AssertionError('Verification or replay must not read external sources')
    with ExitStack() as stack:
        for name in ['open','stat','lstat','resolve','read_bytes','read_text','iterdir','glob','rglob']:
            stack.enter_context(patch.object(Path,name,guard(getattr(Path,name))))
        for name in ['open','stat','lstat','scandir','listdir','readlink']:
            stack.enter_context(patch.object(os,name,guard(getattr(os,name))))
        stack.enter_context(patch.object(builtins,'open',guard(builtins.open)))
        stack.enter_context(patch.object(SourceResolver,'refresh',no_resolve))
        if replay:
            args=['refresh','--request-id',REQUEST,'--expected-sequence',str(PRE_HEAD['sequence']),'--expected-hash',PRE_HEAD['hash']]
            for project in TARGETS: args += ['--project',project]
            repeated = invoke(args)
            assert repeated['appended_project_ids'] == [] and repeated['resolver_errors'] == {}
            assert ledger.history(current_authority=authority) == after
        rebuilt = invoke(['rebuild'])
        assert rebuilt.pop('current_authority')['state'] == 'matched' and rebuilt == projection
        history = invoke(['history'])
        assert history.pop('current_authority')['state'] == 'matched' and history == after
        snapshot = DesignService(DesignStore(ROOT,'data/design_governance/design-store.json')).snapshot()
        assert snapshot['store_revision'] == 8 and len(snapshot['history']) == 1
        service = ProjectService(ROOT)
        projects = service.list_projects(design_snapshot=snapshot)
        assert projects['total'] == 24 and projects['head'] == POST_HEAD
        byid = {p['project_id']:p for p in projects['projects']}
        assert set(byid) == set(TARGETS) | EXCEPTIONS
        for pid,p in byid.items():
            assert p['provenance']['ledger_head'] == POST_HEAD
            assert service.get_project(pid, design_snapshot=snapshot) == p
            if pid in TARGETS:
                assert p['operational']['latest_attempt']['request_id'] == REQUEST
                assert p['operational']['latest_attempt']['success']
                assert p['freshness']['state'] == 'fresh' and not p['freshness']['authority_drift']
            else:
                assert p['declared']['hub_connection_exception']
                assert p['business']['normalized_status'] == 'unknown'
        light = byid['light-novel']
        assert light['business']['normalized_status'] == 'unknown' and light['business']['next_action'] is None
        assert light['source']['availability'] == 'unknown' and light['operational']['facts']
        assert [e['code'] for e in light['errors']] == ['SOURCE_INVALID']
    process = subprocess.Popen([sys.executable,'scripts/hub_server.py','--port','0'], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline().strip()
        assert line.startswith('Personal Control Hub: http://127.0.0.1:')
        origin = line.split(': ',1)[1].rstrip('/')
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def get(path):
            with opener.open(origin+path,timeout=20) as response: return json.loads(response.read())['data']
        get('/api/session') # Session credentials never logged or persisted.
        assert get('/api/projects') == projects
        for pid,p in byid.items(): assert get('/api/projects/'+pid) == p
        assert get('/api/designs') == snapshot
    finally:
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=10)
    assert process.returncode == 0
    assert ledger.history(current_authority=authority) == after
    assert not [p for p,h in baseline['protected'].items() if sha((ROOT/p).read_bytes()) != h]
    coverage = [{ 'project_id':pid,'outcome':'CURRENT_AUTHORITY_REFRESH' if pid in TARGETS else 'AUTHORIZED_EXCEPTION',
                  'freshness':p['freshness']['state'],'source_availability':p['source']['availability'],
                  'business_status':p['business']['normalized_status'],'error_codes':[e['code'] for e in p['errors']],
                  'result_hash':p['provenance']['latest_result_hash'],'dto_sha256':sha(canonical(p).encode())} for pid,p in sorted(byid.items())]
    return {'status':'PASS','pre_head':PRE_HEAD,'post_head':POST_HEAD,'request_id':REQUEST,
            'pre_counts':{k:len(before[k]) for k in ['requests','events','results']},
            'post_counts':{k:len(after[k]) for k in ['requests','events','results']},
            'old_history_preserved':True,'replay_executed':replay,'replay_no_new_effects_or_reads':True if replay else None,
            'instrumentation_scope':'In-process CLI/service/projection/replay; HTTP child process separately read-only API checks',
            'counters':counters,'cli_service_http_24_list_and_details_agree':True,'restart_readback':True,
            'protected_files':len(baseline['protected']),'protected_changed':[],
            'post_history_sha256':sha(canonical(after).encode()),'ledger_sha256':sha((ROOT/LEDGER).read_bytes()),
            'coverage':coverage,
            'original_execution_diagnosis':{'fingerprint':'refresh_once.py postcommit resolver-order assertion; production refresh successful',
                'cause':'Refresh processes sorted project IDs; harness compared registry input order.',
                'original_counters':'Not persisted before assertion; no fabricated counter telemetry.',
                'original_guard_evidence':'Recorded successful request/results under raising Manga-path guard and exact target membership assertions.',
                'recovery':'Verified immutable Git preimage and exact committed request before identical replay; no new request.'}}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--replay',action='store_true')
    print(json.dumps(verify(parser.parse_args().replay),ensure_ascii=False,indent=2))
