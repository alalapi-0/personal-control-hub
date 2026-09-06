"""TC16 exact production refresh, source boundary instrumentation and idempotent replay."""
from pathlib import Path
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch
import builtins
import hashlib
import io
import json
import os
import sys
import yaml

ROOT=Path(__file__).resolve().parents[4]
UNIT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from hub.connection_manager_cli import main, load_bundles, result_validator
from hub.connection_refresh import RefreshLedger
from hub.connection_sources import SourceResolver

REQUEST='tc16-real-20-20260906'
HEAD={'sequence':108,'hash':'246acf8f66a340a2a00ff4616b41b27a7beed87c0aacc1fafe6660d158e706ab'}
TARGETS=['storage_governance','personal-control-hub','universal-player','ai-anime-short-factory','ai-music-foundry','novel-continuation-agent','story-faceless-utopia','light-novel','wechat-article-scheduler','resilient-personal-network','pixel-world-asset-forge','desktop-jav-tools','zarathustra-adaptation','computer-study-plan','desktop-tool','audio-clone','cognitive-asset-library','pycharm-agent-workspace','youtube-hq-downloader','mpv-clip-workbench']
EXCEPTIONS={'manga-localizer','desktop-magnet','pycharm-misc-project','desktop-downloads-scripts'}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def run():
    registry=yaml.safe_load((ROOT/'data/registry/external_projects.yaml').read_text())['projects']
    assert [p['id'] for p in registry if p.get('enabled') and p.get('summary_enabled') and not p.get('hub_connection_exception')]==TARGETS
    assert {p['id'] for p in registry if p.get('hub_connection_exception')}==EXCEPTIONS
    manga=os.path.normpath(next(p['root_path'] for p in registry if p['id']=='manga-localizer'))
    counters={'manga_path_operations':0,'resolver_project_ids':[]}
    def guard(function):
        def checked(path,*args,**kwargs):
            if isinstance(path,(str,bytes,os.PathLike)):
                value=os.fsdecode(os.fspath(path))
                absolute=os.path.normpath(value if os.path.isabs(value) else str(ROOT/value))
                if absolute==manga or absolute.startswith(manga+os.sep):
                    counters['manga_path_operations']+=1
                    raise AssertionError('Manga filesystem boundary reached')
            return function(path,*args,**kwargs)
        return checked
    original_resolve=SourceResolver.refresh
    def resolve(resolver,project_id,*args,**kwargs):
        assert project_id in TARGETS
        counters['resolver_project_ids'].append(project_id)
        return original_resolve(resolver,project_id,*args,**kwargs)
    bundles,validators=load_bundles(ROOT, [f'data/design_governance/authority-bundle-v{i}.json' for i in range(1,5)])
    authority=validators[bundles[-1]['source_plan']['content_hash']].authority
    ledger=RefreshLedger(ROOT,result_validator=result_validator(validators),read_only=True)
    before=ledger.history(current_authority=authority)
    assert before['head']==HEAD, 'Unexpected ledger head: query ownership/history before any retry'
    assert not any(x.get('request_id')==REQUEST for x in before['requests'])
    args=['refresh','--request-id',REQUEST,'--expected-sequence',str(HEAD['sequence']),'--expected-hash',HEAD['hash']]
    for project in TARGETS:args+=['--project',project]
    def invoke():
        output=io.StringIO()
        with redirect_stdout(output):code=main(args,root=ROOT)
        value=json.loads(output.getvalue())
        return code,value
    with ExitStack() as stack:
        for name in ['open','stat','lstat','resolve','read_bytes','read_text','iterdir','glob','rglob']:
            stack.enter_context(patch.object(Path,name,guard(getattr(Path,name))))
        for name in ['open','stat','lstat','scandir','listdir','readlink']:
            stack.enter_context(patch.object(os,name,guard(getattr(os,name))))
        stack.enter_context(patch.object(builtins,'open',guard(builtins.open)))
        stack.enter_context(patch.object(SourceResolver,'refresh',resolve))
        code,result=invoke()
        (UNIT/'refresh.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        assert code==0, f'Actual refresh exit {code}; exact request/result preserved for forward diagnosis'
        after=ledger.history(current_authority=authority)
        for key in ['requests','events','results']:
            prior={canonical(x) for x in before[key]}
            assert prior <= {canonical(x) for x in after[key]}, f'Old {key} changed'
        assert counters['resolver_project_ids']==TARGETS
        code,replay=invoke()
        final=ledger.history(current_authority=authority)
        assert code==0 and final==after, 'Replay changed ledger'
        assert counters['resolver_project_ids']==TARGETS, 'Replay reread a source'
        assert counters['manga_path_operations']==0
    evidence={'status':'PASS','request_id':REQUEST,'command':['python3','scripts/hub_refresh.py',*args],
              'pre_head':before['head'],'post_head':after['head'],
              'pre_counts':{k:len(before[k]) for k in ['requests','events','results']},
              'post_counts':{k:len(after[k]) for k in ['requests','events','results']},
              'prior_history_preserved':True,'replay_no_new_effects_or_reads':True,'counters':counters,
              'pre_history_sha256':hashlib.sha256(canonical(before).encode()).hexdigest(),
              'post_history_sha256':hashlib.sha256(canonical(after).encode()).hexdigest(),
              'ledger_sha256':hashlib.sha256((ROOT/'data/design_governance/connection_refresh.sqlite3').read_bytes()).hexdigest()}
    (UNIT/'refresh-validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(evidence,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
