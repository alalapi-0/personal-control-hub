"""Real source/ledger/API integration around finite capture effects and recovery."""
import copy
import hashlib
import json
import struct
import sys
import threading
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_page_pipeline as source_pipeline
from hub.capture_worker import CaptureWorker, capture_catalog, read_capture_image, validate_capture_request, _digest
from hub.capture_store import CaptureStore, png_info
from hub.capture_browser import CaptureBrowserError
from hub.connection_records import RecordError
from hub.connection_sources import SourceResolver
from hub.local_service import HubHTTPServer
from hub.service_contract import ServiceError


def image():
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',320,240,8,6,0,0,0))
            +chunk(b'IDAT',zlib.compress((b'\x00'+bytes([12,34,56,255])*320)*240))+chunk(b'IEND',b''))


class CaptureWorkerTests(unittest.TestCase):
    setUp = source_pipeline.PagePipelineTests.setUp
    write = source_pipeline.PagePipelineTests.write
    publish = source_pipeline.PagePipelineTests.publish
    start = source_pipeline.PagePipelineTests.start
    call = source_pipeline.PagePipelineTests.call
    stop = source_pipeline.PagePipelineTests.stop

    def request(self, rid='capture-home', reason='manual'):
        dto=self.service.get_project('business'); page=dto['business']['page_inventory']['value']['domains'][0]['pages'][0]
        return {'schema_version':'1.0','request_id':rid,'project_id':'business','page_id':'home',
            'url':'http://127.0.0.1:8181/#home','policy':{'schema_version':'1.0','project_id':'business',
            'allowed_urls':['http://127.0.0.1:8181/#home','http://127.0.0.1:8181/version'],
            'allowed_read_paths':[],'max_redirects':0,'retention':'local_only'},'source_sha256':page['source_sha256'],
            'build_sha256':page['build_sha256'],'source_proof_sha256':dto['business']['page_inventory']['proof']['sha256'],
            'version_url':'http://127.0.0.1:8181/version','viewport':{'width':320,'height':240},'timeout_ms':1000,
            'ready_selector':None,'refresh_reason':reason,'expires_after_seconds':3600}

    def worker(self, effect=None, now=None):
        return CaptureWorker(self.service, capture=effect or (lambda *a,**k:{'png':image(),'runtime_verified':True,
            'elapsed_ms':1,'blocked_count':0}), clock=lambda:now or datetime.now(timezone.utc))

    def test_real_source_capture_receipt_dedup_and_no_source_mutation(self):
        self.publish(); before=self.ledger.path.read_bytes(); calls=[]
        def capture(*a,**kw):
            calls.append(a[0]);return {'png':image(),'runtime_verified':True,'elapsed_ms':1,'blocked_count':0}
        worker=self.worker(capture); request=self.request(); saved=worker.run(request)
        self.assertEqual(saved['record']['status'],'success');self.assertEqual(len(calls),1)
        receipt=worker.run(request);self.assertTrue(receipt['reused']);self.assertFalse(receipt['capture_performed'])
        changed=copy.deepcopy(request);changed['timeout_ms']=999
        with self.assertRaises(RecordError):worker.run(changed)
        reused=worker.run(self.request('new-version-check','version'))
        self.assertEqual(reused['reason'],'UNCHANGED_UNEXPIRED');self.assertEqual(len(calls),1)
        self.assertEqual(self.ledger.path.read_bytes(),before)
        catalog=capture_catalog(self.service.get_project('business'),worker.store)
        self.assertEqual(catalog['previews'][0]['version_state'],'current')
        self.assertEqual(worker.store.image(saved['record']),image())

    def test_failed_attempt_keeps_old_image_historical_manual_recovery(self):
        self.publish(); first=self.worker().run(self.request())['record']
        oldbytes=CaptureStore(self.fixture.hub).image(first)
        def fail(*a,**k):raise CaptureBrowserError('CAPTURE_TIMEOUT')
        self.worker(fail).run(self.request('timeout'))
        failed=capture_catalog(self.service.get_project('business'),CaptureStore(self.fixture.hub))
        self.assertEqual(failed['latest_attempt']['reason'],'CAPTURE_TIMEOUT')
        self.assertEqual(failed['previews'][0]['version_state'],'historical')
        self.assertEqual(CaptureStore(self.fixture.hub).image(first),oldbytes)
        self.worker().run(self.request('recovered'))
        self.assertEqual(capture_catalog(self.service.get_project('business'),CaptureStore(self.fixture.hub))['previews'][0]['version_state'],'current')
        self.assertEqual(len(CaptureStore(self.fixture.hub).records('business')),3)

    def test_during_capture_source_drift_rejected_and_source_failure_preserved(self):
        self.publish()
        def drift(*a,**k):
            changed=copy.deepcopy(self.value);changed['domains'][0]['pages'][0]['source_sha256']='b'*64
            self.write(changed);self.publish('changed-in-flight')
            return {'png':image(),'runtime_verified':True,'elapsed_ms':1,'blocked_count':0}
        result=self.worker(drift).run(self.request())
        self.assertEqual(result['record']['status'],'failed')
        self.assertIsNone(result['record']['artifact_ref'])
        self.assertEqual(self.service.get_project('business')['business']['page_inventory']['value']['domains'][0]['pages'][0]['source_sha256'],'b'*64)

    def test_project_page_version_policy_and_bounds_rejected_before_effect(self):
        self.publish();worker=self.worker(lambda *a,**k:self.fail('effect must not run'))
        request=self.request()
        for patch in [{'page_id':'absent'},{'source_sha256':'b'*64},{'source_proof_sha256':'c'*64},
                      {'timeout_ms':30001},{'viewport':{'width':10,'height':240}},
                      {'project_id':'wrong-project'},{'version_url':'http://127.0.0.1:8181/ungranted'}]:
            with self.subTest(patch=patch),self.assertRaises((RecordError,ServiceError)):
                worker.run({**copy.deepcopy(request),**patch})
        self.assertFalse((self.fixture.hub/'data/previews').exists())

    def test_version_expiry_unknown_runtime_and_freshness_are_distinct(self):
        self.publish();when=datetime.now(timezone.utc)
        self.worker(now=when).run(self.request())
        store=CaptureStore(self.fixture.hub);dto=self.service.get_project('business')
        self.assertEqual(capture_catalog(dto,store,now=when+timedelta(seconds=3601))['previews'][0]['version_state'],'expired')
        unknown=self.worker(lambda *a,**k:{'png':image(),'runtime_verified':False,'elapsed_ms':1,'blocked_count':0})
        unknown.run(self.request('unverified-runtime'))
        self.assertEqual(capture_catalog(dto,store)['previews'][0]['version_state'],'unknown_version')
        changed=copy.deepcopy(self.value);changed['domains'][0]['pages'][0]['source_sha256']='b'*64
        self.write(changed);self.publish('new-source')
        self.assertEqual(capture_catalog(self.service.get_project('business'),store)['previews'][0]['version_state'],'historical')
        self.publish('stale-source',age=7200)
        self.assertEqual(capture_catalog(self.service.get_project('business'),store)['previews'][0]['version_state'],'unknown_version')

    def test_query_and_image_http_no_capture_no_refresh_no_creating_missing_store(self):
        self.publish();self.start();before=self.ledger.path.read_bytes()
        with mock.patch.object(CaptureWorker,'run',side_effect=AssertionError('GET capture')), \
             mock.patch.object(SourceResolver,'refresh',side_effect=AssertionError('GET source')):
            status,_,body=self.call(path='/api/projects/business/captures');self.assertEqual(status,200)
            self.assertFalse(body['data']['capture_performed']);self.assertEqual(body['data']['reason'],'NO_CAPTURE_AVAILABLE')
            self.assertEqual(self.call(path='/api/projects/business/captures?url=http://127.0.0.1:1/')[0],400)
            self.assertEqual(self.call(path='/api/projects/business/captures/missing/image')[0],404)
        self.assertFalse((self.fixture.hub/'data/previews').exists());self.assertEqual(self.ledger.path.read_bytes(),before)
        record=self.worker().run(self.request())['record']
        response=read_capture_image(self.service,'business',record['request_id']);self.assertEqual(response.data,image())
        self.assertEqual(response.content_type,'image/png')
        self.assertEqual(self.ledger.path.read_bytes(),before)
        original=copy.deepcopy(self.fixture.projects[0]);self.fixture.projects[0]['connection_read_allowed']=False;self.fixture.save_registry()
        denied=capture_catalog(self.service.get_project('business'),CaptureStore(self.fixture.hub))
        self.assertEqual(denied['previews'],[])
        with self.assertRaises(ServiceError):read_capture_image(self.service,'business',record['request_id'])
        self.fixture.projects[0]=original;self.fixture.save_registry()

    def test_fresh_guest_bootstrap_keeps_owner_unconfigured_and_old_session_contract(self):
        self.publish();self.start()
        status,headers,body=self.call(path='/api/guest',authenticated=False)
        self.assertEqual(status,200);self.assertIn('csrf_token',body['data'])
        self.cookie=headers['Set-Cookie'].split(';',1)[0]
        status,_,body=self.call(path='/api/identity');self.assertEqual(status,200)
        self.assertFalse(body['data']['owner_authenticated']);self.assertFalse(body['data']['execution_enabled'])
        self.assertEqual(self.call(path='/api/session',authenticated=False)[0],200)

    def test_runtime_version_endpoint_is_plain_bounded_code_proof_without_session(self):
        self.publish();self.start()
        status,_,body=self.call(path='/api/preview-runtime',authenticated=False)
        self.assertEqual(status,200);self.assertEqual(set(body),{'schema_version','project_id','source_sha256','build_sha256'})
        import hub.local_service as service
        files={'src/hub/web/'+p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in service.WEB_ROOT.iterdir() if p.is_file()}
        self.assertEqual(body['source_sha256'],hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        self.assertEqual(body['project_id'],'personal-control-hub');self.assertIsNone(body['build_sha256'])
        self.assertEqual(self.call(path='/api/preview-runtime?project_id=other',authenticated=False)[0],400)

    def test_explicit_no_ui_and_empty_domain_do_not_infer_from_missing(self):
        self.publish();store=CaptureStore(self.fixture.hub);dto=self.service.get_project('business')
        self.assertEqual(capture_catalog(dto,store)['classification'],'unavailable')
        self.declaration['extensions']['fact_mapping']['has_ui']={'source_ref':'state','selector':{'path':['has_ui']}}
        self.declaration['extensions']['unknown_fields']['has_ui']='Not declared'
        (self.root/'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration,sort_keys=False))
        data=copy.deepcopy(self.fixture.business);data.update(page_inventory=self.value,has_ui=False)
        (self.root/'STATE.yaml').write_text(yaml.safe_dump(data,sort_keys=False));self.publish('literal-no-ui')
        no_ui=capture_catalog(self.service.get_project('business'),store)
        self.assertEqual(no_ui['classification'],'not_applicable');self.assertEqual(no_ui['reason'],'SOURCE_HAS_UI_FALSE')
        self.assertEqual(no_ui['previews'],[])
        empty=copy.deepcopy(self.value);empty['domains'][0].update(pages=[],known_page_count=0,completeness='complete');empty['previews']=[]
        data=copy.deepcopy(self.fixture.business);data.update(page_inventory=empty,has_ui=True)
        (self.root/'STATE.yaml').write_text(yaml.safe_dump(data,sort_keys=False));self.publish('literal-empty')
        result=capture_catalog(self.service.get_project('business'),store)
        self.assertEqual(result['classification'],'not_implemented')


if __name__=='__main__':unittest.main()
