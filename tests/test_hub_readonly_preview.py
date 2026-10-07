from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub import readonly_preview as rp
from hub.service_contract import ServiceError
from hub.workbench_store import digest
from hub.design_service import DesignService


class ReadOnlyPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.hub = Path(self.temp.name) / 'hub'; self.hub.mkdir()
        self.external = Path(self.temp.name) / 'owned-project'; self.external.mkdir()
        self.root_patch = mock.patch.object(rp, 'ROOT', self.external); self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        self.git = mock.patch.object(rp.subprocess, 'run', return_value=SimpleNamespace(stdout=(rp.HEAD+'\n').encode()))
        self.git.start(); self.addCleanup(self.git.stop)
        for name in rp.FILES:
            p = self.external / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('owned unit fixture '+name)
        self.manifest = {'schema':1,'project_id':rp.PROJECT,'head':rp.HEAD,'entry':'progress.html',
            'files':{f:hashlib.sha256((self.external/f).read_bytes()).hexdigest() for f in rp.FILES},
            'captures':{v:{'path':'docs/reports/linux-workbench/test-'+v+'.jpg',
                         'sha256':'a'*64,'width':600,'height':800} for v in ('desktop','mobile')},
            'observed_at':'2026-10-07T00:00:00Z'}
        mp = self.hub / rp.MANIFEST; mp.parent.mkdir(parents=True); mp.write_text(json.dumps(self.manifest))
        self.grant = {'project_id':rp.PROJECT,'root':str(self.external),'head':rp.HEAD,
            'state':'active','server':'tests/ui/fixture_server.py:FixtureServer(task_actions=False)'}
        self.state = {'linux_visual_workbench':{'authorization':{'readonly_preview_grants':[self.grant]}}}
        self.save_state()
        self.registry = SimpleNamespace(projects={rp.PROJECT:{'enabled':True,'root_path':str(self.external),
            'access_profile':'registered_project_read'}},_check_registry=lambda:None)
        self.projects = SimpleNamespace(_resolver=lambda:self.registry)
        baseline = {'kind':'baseline','id':rp.BASELINE,'revision':rp.SNAPSHOT_REVISION,'content_hash':'b'*64,
            'project_id':rp.PROJECT,'scope':{'pages':['home']},
            'source':{'kind':'repository','commit':rp.HEAD,'reference':rp.MANIFEST+'#sha256='+digest(self.manifest)}}
        candidate = {'kind':'candidate','id':rp.CANDIDATE,'revision':rp.SNAPSHOT_REVISION,'content_hash':'c'*64,
            'purpose':'read_only_snapshot','decision_eligible':False,'execution_allowed':False,
            'baseline_bindings':[{'baseline_id':rp.BASELINE,'baseline_revision':rp.SNAPSHOT_REVISION,'baseline_hash':'b'*64,
                                 'project_id':rp.PROJECT,'pages':['home']}]}
        self.registration = {'facts':[baseline,candidate]}
        self.designs = SimpleNamespace(_read=lambda:self.registration,
            _assert_current_candidate=DesignService._assert_current_candidate)
        self.preview = rp.ReadOnlyPreview(self.hub,self.projects,self.designs)

    def save_state(self):
        (self.hub/'STATE.yaml').write_text(yaml.safe_dump(self.state))

    def rejected(self, code):
        with self.assertRaises(ServiceError) as error: self.preview.check()
        self.assertEqual(error.exception.code,code)

    def test_exact_source_is_readonly_and_stopped_without_start_effect(self):
        before = {f:(self.external/f).read_bytes() for f in rp.FILES}
        self.preview.check(); d=self.preview.describe()
        self.assertFalse(d['available']); self.assertFalse(d['execution_allowed'])
        self.assertIsNone(d['frame_url']); self.assertEqual(d['manifest_hash'],digest(self.manifest))
        self.assertEqual(d['manifest_observed_at'],self.manifest['observed_at'])
        self.assertNotIn('captured_at',d)
        self.assertEqual(before,{f:(self.external/f).read_bytes() for f in rp.FILES})

    def test_revocation_fences_existing_adapter(self):
        self.grant['state']='revoked';self.save_state();self.rejected('REAL_PREVIEW_GRANT_REVOKED')

    def test_grant_cannot_choose_another_root_head_or_action_mode(self):
        for key,value in [('root','/untrusted'),('head','b'*40),('server','FixtureServer(task_actions=True)')]:
            original=self.grant[key];self.grant[key]=value;self.save_state()
            self.rejected('REAL_PREVIEW_GRANT_REVOKED');self.grant[key]=original

    def test_removed_cloud_disabled_and_root_change_are_rejected(self):
        p=self.registry.projects[rp.PROJECT]
        for key,value in [('current_state_status','removed_local'),('root_kind','cloud'),('enabled',False),('root_path','/other')]:
            original=dict(p);p[key]=value;self.rejected('REAL_PREVIEW_PROJECT_REJECTED');p.clear();p.update(original)

    def test_head_and_named_file_drift_fail_closed(self):
        rp.subprocess.run.return_value=SimpleNamespace(stdout=b'wrong\n');self.rejected('REAL_PREVIEW_VERSION_STALE')
        rp.subprocess.run.return_value=SimpleNamespace(stdout=(rp.HEAD+'\n').encode())
        (self.external/'progress_ui.css').write_text('changed');self.rejected('REAL_PREVIEW_VERSION_STALE')

    def test_same_content_symlink_is_rejected(self):
        p=self.external/'progress_ui.css';copy=self.external/'unregistered';copy.write_bytes(p.read_bytes())
        p.unlink();p.symlink_to(copy);self.rejected('REAL_PREVIEW_VERSION_STALE')

    def test_registration_change_does_not_rebind_existing_adapter(self):
        self.manifest['entry']='../../etc/passwd';(self.hub/rp.MANIFEST).write_text(json.dumps(self.manifest))
        self.rejected('REAL_PREVIEW_VERSION_STALE')
        with self.assertRaises(ServiceError):rp.ReadOnlyPreview(self.hub,self.projects,self.designs)

    def test_new_process_cannot_rebind_changed_sources_to_old_registered_images(self):
        (self.external/'progress_ui.css').write_text('different CSS, same HEAD')
        self.manifest['files']['progress_ui.css']=hashlib.sha256((self.external/'progress_ui.css').read_bytes()).hexdigest()
        (self.hub/rp.MANIFEST).write_text(json.dumps(self.manifest))
        # The new process pins the new manifest; the independent immutable
        # baseline still pins the original manifest and must reject it.
        fresh=rp.ReadOnlyPreview(self.hub,self.projects,self.designs)
        with self.assertRaises(ServiceError) as error:fresh.check()
        self.assertEqual(error.exception.code,'REAL_PREVIEW_VERSION_STALE')
        with mock.patch.object(fresh,'start',wraps=fresh.start) as start:
            with self.assertRaises(ServiceError):start()
        self.assertIsNone(fresh.fixture)

    def test_snapshot_registration_and_capture_shape_are_required(self):
        for field,value in [('purpose','design'),('decision_eligible',True),('execution_allowed',True)]:
            candidate=self.registration['facts'][1];before=candidate[field];candidate[field]=value
            self.rejected('REAL_PREVIEW_VERSION_STALE');candidate[field]=before
        candidate['revision']=1
        self.rejected('REAL_PREVIEW_VERSION_STALE')
        candidate['revision']=rp.SNAPSHOT_REVISION
        for captures in [[],{}, {'desktop':None,'mobile':{}},
                         {'desktop':{'path':'x','sha256':'a'*64,'width':True,'height':1},'mobile':{}}]:
            changed={**self.manifest,'captures':captures};(self.hub/rp.MANIFEST).write_text(json.dumps(changed))
            with self.assertRaises(ServiceError):rp.ReadOnlyPreview(self.hub,self.projects,self.designs)

    def test_extra_and_missing_asset_cannot_be_admitted(self):
        for name in ['../../etc/passwd','arbitrary-file']:
            self.manifest['files'][name]='a'*64;(self.hub/rp.MANIFEST).write_text(json.dumps(self.manifest))
            with self.assertRaises(ServiceError):rp.ReadOnlyPreview(self.hub,self.projects,self.designs)
            del self.manifest['files'][name]


if __name__=='__main__': unittest.main()
