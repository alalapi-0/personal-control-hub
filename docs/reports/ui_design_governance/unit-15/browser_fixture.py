"""Disposable browser fixture using the actual Hub service and source adapters."""
from pathlib import Path
import copy
import hashlib
import json
import sys
import threading

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
from test_hub_service_integration import LocalServiceIntegrationTests
from hub.design_cli import _artifact
from hub.design_records import with_content_hash

fixture=LocalServiceIntegrationTests()
fixture.setUp()
try:
    artifacts=[]
    for view in ('desktop','mobile'):
        original=ROOT/f'docs/reports/ui_design_governance/unit-14/previews/P5--overview--{view}.png'
        path=fixture.material/f'candidate-{view}.png'
        path.write_bytes(original.read_bytes())
        artifact=_artifact(f'fixture-overview-{view}',str(path.relative_to(fixture.root)),hashlib.sha256(path.read_bytes()).hexdigest())
        fixture.store.append_fact(artifact,expected_revision=fixture.store.read()['revision'],request_id='fixture-image-'+view)
        artifacts.append(artifact)
    candidate=copy.deepcopy(fixture.candidate)
    candidate['revision']=2
    candidate['artifact_bindings']=[{'artifact_id':a['id'],'sha256':a['sha256']} for a in artifacts]
    candidate['visual']['differences']=['示例：暮紫杏光', '浏览器隔离演练，操作不影响真实决定']
    fixture.store.append_fact(with_content_hash(candidate),expected_revision=fixture.store.read()['revision'],request_id='fixture-browser-candidate-v2')
    alternative=copy.deepcopy(candidate)
    alternative['id']='fixture-alternative'
    alternative['revision']=1
    alternative['visual']['differences']=['示例：另一候选', '同范围候选，验证决定不会误标']
    fixture.store.append_fact(with_content_hash(alternative),expected_revision=fixture.store.read()['revision'],request_id='fixture-browser-alternative')
    marker=ROOT/'docs/reports/ui_design_governance/unit-15/fixture-runtime.json'
    marker.write_text(json.dumps({'origin':fixture.server.origin,'root':str(fixture.root),'store':str(fixture.store.path),'classification':'synthetic_fixture'},indent=2)+'\n')
    print(fixture.server.origin,flush=True)
    while True:
        threading.Event().wait(30)
except KeyboardInterrupt:
    pass
finally:
    fixture.doCleanups()
