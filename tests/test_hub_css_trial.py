"""Disposable security samples; mock results are never native evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_trial as c
from hub.task_service import TaskService,validate_grant,root_identity,fingerprint
from hub.task_store import TaskStore
from hub.task_worker import TaskWorker
from hub.workbench_store import digest
from hub.service_contract import ServiceError
from test_hub_task_runtime import FakeAdapter
import test_hub_workbench as annotation_fixture


class CssTrialTests(unittest.TestCase):
    def config_fixture(self):
        from hub import codex_adapter as adapter
        home=Path(self.tmp.name)/'nonsecret-config-home';(home/'.codex').mkdir(parents=True)
        path=home/'.codex/config.toml';path.write_text('# nonsecret config fixture\n')
        value={k:{} if t=='dict'else 'fixture'for k,t in c.CONFIG_TOP_TYPES.items()}
        value.update(model='gpt-6.1-sol',model_reasoning_effort='xhigh',approval_policy='never',sandbox_mode='danger-full-access',
            features={'memories':True},agents={'default_subagent_reasoning_effort':'xhigh','interrupt_message':False,
            'max_concurrent_threads_per_session':7})
        value['mcp_servers']={name:{k:{} if t=='dict'else [] if t=='list'else 10 if t=='int'else 'fixture'
            for k,t in row.items()}for name,row in c.CONFIG_MCP_TYPES.items()}
        env=mock.patch.object(adapter,'child_environment',return_value={'HOME':str(home)})
        parse=mock.patch.object(c.tomllib,'loads',return_value=value)
        env.start();parse.start();self.addCleanup(env.stop);self.addCleanup(parse.stop)
        return path,value

    def test_task_bound_config_pin_rejects_unknown_keys_and_symlinks(self):
        path,value=self.config_fixture();observation=c.config_observation()
        self.assertEqual(observation['projection']['credential_bytes_read'],0)
        self.assertNotIn('body',observation)
        value['permissions']={'unapproved':{}}
        with self.assertRaises(ServiceError):c.config_observation()
        del value['permissions'];value['features']['hooks']=True
        with self.assertRaises(ServiceError):c.config_observation()
        del value['features']['hooks'];target=path.with_suffix('.original');path.rename(target);path.symlink_to(target.name)
        with self.assertRaises(OSError):c.config_observation()

    def test_task_bound_config_pin_never_follows_a_new_version(self):
        path,_=self.config_fixture();pin=c.prepared_config_pin()
        self.assertEqual(c.verify_config_pin(pin),pin['sha256'])
        path.write_text('# changed nonsecret config fixture\n')
        with self.assertRaises(ServiceError):c.verify_config_pin(pin)
        with mock.patch.object(c,'config_observation',side_effect=[{'sha256':'first'},{'sha256':'second'}]):
            with self.assertRaises(ServiceError)as error:c.prepared_config_pin()
        self.assertEqual(error.exception.code,'CSS_ACTIVE_CONFIG_CHURN')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        for key,value in [('auth_check',{'source':'mock','mode':'ChatGPT'}),('config_identity',[1,2,3,4,5])]:
            patch=mock.patch.object(c,key,return_value=value);patch.start();self.addCleanup(patch.stop)
        base=Path(self.tmp.name);self.hub=base/'hub';self.source=base/'source';self.trial=base/'trial'
        for p in (self.hub,self.source,self.trial):p.mkdir()
        self.before=b'.home-task-card {\n border: 1px solid rgba(248, 238, 229, 0.15);\n}\n.other { color: red; }\n'
        self.after=self.before.replace(b'0.15',b'0.24',1)
        for key,value in [('HUB_ROOT',self.hub),('ROOT',self.source),('TRIAL',self.trial),('OUTSIDE_PROBE',base/'outside'),
                          ('CREDENTIAL_PATH',base/'nonsecret-auth-canary'),('PARENT_CANARY',base/'parent-canary'),('PARENT_WRITE_PROBE',base/'denied-parent-create'),('BEFORE_SHA',c.sha(self.before)),('AFTER_SHA',c.sha(self.after))]:
            patch=mock.patch.object(c,key,value);patch.start();self.addCleanup(patch.stop)
        c.OUTSIDE_PROBE.write_text('nonsecret');c.CREDENTIAL_PATH.write_text('nonsecret');c.PARENT_CANARY.write_text('nonsecret')
        self.private=self.hub/c.PRIVATE;self.private.mkdir(parents=True)
        (self.private/'before.css').write_bytes(self.before)
        (self.source/'progress_ui.css').write_bytes(self.before);(self.trial/'progress_ui.css').write_bytes(self.before)
        os.chmod(self.source/'progress_ui.css',0o644);os.chmod(self.trial/'progress_ui.css',0o644)
        image=self.hub/c.IMAGE;image.parent.mkdir(parents=True);image.write_bytes(b'unit reference')
        for f in c.DOCS:
            p=self.source/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'protected docs')
        t={'contract':c.CONTRACT,'canonical_root':str(self.source),'head':c.HEAD,'source_sha256':c.BEFORE_SHA,'source_mode':0o644,
            'source_identity':c.read_file(self.source/'progress_ui.css')[1],'docs_sha256':dict(c.DOCS),
            'before_ref':c.PRIVATE+'/before.css','expected_sha256':c.AFTER_SHA,'image_ref':c.IMAGE,'registry_hash':'a'*64,
            'outside_probe':str(c.OUTSIDE_PROBE),'config_identity':c.config_identity()}
        helper=annotation_fixture.WorkbenchTests();helper.setUp();self.addCleanup(helper.doCleanups)
        self.command=helper.command(kind='whole',region=None);self.command['binding']['project_id']='computer-study-plan'
        self.command['binding']['registry_hash']='a'*64;self.command['binding']['artifact_sha256']=c.sha(image.read_bytes())
        self.grant={'grant_id':'csp-css-trial-grant-v1','version':1,'source':'verified_owner_decision','task_id':c.TASK,'project_id':'computer-study-plan',
            'root':str(self.trial),'root_identity':root_identity(self.trial),'fingerprint':fingerprint(self.trial),
            'annotation_hash':digest(self.command),'checks':['csp_css_trial'],'expires_at':time.time()+3600,'isolated_root':True,
            'writable_scope':['.'],'prohibited':[],'auth':'ChatGPT','max_turn_seconds':120,'image':'registered_preview',
            'image_sha256':c.sha(image.read_bytes()),'mode':'new','parent_task_id':None,'css_trial':t}
        self.project={'enabled':True,'root_path':str(self.source),'access_profile':'registered_project_read'}
        resolver=SimpleNamespace(projects={'computer-study-plan':self.project},authority={'registry_hash':'a'*64},_check_registry=lambda:None)
        self.projects=SimpleNamespace(_resolver=lambda:resolver)
        self.annotations=SimpleNamespace(receipt=lambda ident:{'receipt':{'revision':1,'command':self.command}},validate_references=lambda *a,**k:None)
        self.store=TaskStore(self.hub);self.store.register_storage();self.store.register_grant(self.grant)
        self.payload={'request_id':c.TASK,'project_id':'computer-study-plan','annotation_id':'trial-annotation','annotation_revision':1,
            'grant_id':self.grant['grant_id'],'grant_version':1,'mode':'new','parent_task_id':None}
        self.service=TaskService(self.projects,self.annotations,self.store,test_gate=True,writer_check=lambda g:True)
        self.source_check=mock.patch.object(c,'source_check');self.source_check.start();self.addCleanup(self.source_check.stop)

    def test_exact_delta_rejects_extra_bytes_files_mode_and_links(self):
        validate_grant(self.grant,live=True)
        (self.trial/'progress_ui.css').write_bytes(self.after);self.assertTrue(c.trial_result(self.grant)['trial_ready'])
        for raw in (self.after+b'\n',self.before,self.after.replace(b'red',b'blue')):
            (self.trial/'progress_ui.css').write_bytes(raw)
            with self.assertRaises(ServiceError):c.trial_result(self.grant)
        (self.trial/'progress_ui.css').write_bytes(self.after)
        extra=self.trial/'extra';extra.write_text('unrequested')
        with self.assertRaises(ServiceError):c.trial_result(self.grant)
        extra.unlink();os.chmod(self.trial/'progress_ui.css',0o600)
        with self.assertRaises(ServiceError):c.trial_result(self.grant)
        os.chmod(self.trial/'progress_ui.css',0o644)
        extra.symlink_to(self.source/'progress_ui.css')
        with self.assertRaises(ServiceError):c.trial_result(self.grant)

    def test_mapping_and_intent_cannot_offer_arbitrary_root_or_resume(self):
        self.service.submit(self.payload);task=self.store.task(c.TASK)
        self.assertEqual(task['intent']['before_sample'],'');self.assertEqual(task['intent']['prompt_sha256'],c.sha(c.prompt(self.command).encode()))
        self.assertTrue(self.service.submit(self.payload)['replayed'])
        for change in [{'root':str(self.source)},{'image':'fixture.png'},{'mode':'idle_continue','parent_task_id':'other'}]:
            with self.assertRaises(ServiceError):validate_grant({**self.grant,**change})
        self.project['root_path']=str(self.hub)
        # Changed request cannot silently replay the existing task.
        with self.assertRaises(ServiceError):self.service.submit({**self.payload,'annotation_revision':2})

    def proof_adapter(self,denied=True):
        outer=self
        class Adapter(FakeAdapter):
            active_profile='hub_lwb_fixture_v1'
            def check(self,argv):
                proof={'trial_sha256':c.sha((outer.trial/'progress_ui.css').read_bytes()),'read_bytes':0,'network_errno':1,'parent_enumeration_errno':13,'parent_create_errno':30,'denials':
                    {k+'_'+m:2 if denied else 0 for k in ('canonical','parent','sibling','tmp','credential','parent_canary')for m in ('read','write')if(k,m)!=('parent','write')}}
                return {'exitCode':0,'stdout':json.dumps(proof),'stderr':''}
            def call(self,method,params):
                return {'thread':{'id':params['threadId'],'cwd':str(outer.trial),'status':{'type':'idle'},
                    'turns':[{'id':'css-turn','status':'completed'}]if getattr(self,'did_turn',False)else[]}}
            def verify_capabilities(self,thread):return {'mock':True}
            def start(self,thread,text,image):
                type(self).effects+=1;(outer.trial/'progress_ui.css').write_bytes(outer.after)
                self.did_turn=True
                self.events=[{'method':'turn/started','params':{'threadId':thread,'turn':{'id':'css-turn','status':'inProgress'}}},
                    {'method':'turn/completed','params':{'threadId':thread,'turn':{'id':'css-turn','status':'completed'}}}]
                return 'css-turn'
        Adapter.effects=0
        return Adapter

    def test_exact_live_trial_profile_denies_parent_without_changing_fixture_profiles(self):
        from hub.codex_adapter import profile_arguments
        live=Path('/home/alalapi/Temp/personal-control-hub/linux-workbench/csp-css-trial-20261007')
        text=' '.join(profile_arguments(live))
        self.assertIn('"'+str(live.parent)+'"="deny"',text)
        self.assertIn('"'+str(live)+'"="write"',text)
        self.assertNotIn('"'+str(self.trial.parent)+'"="deny"',' '.join(profile_arguments(self.trial)))

    def test_denial_diagnostic_retains_only_fixed_fields_and_no_native_secret(self):
        adapter=self.proof_adapter(False)(self.trial)
        with self.assertRaises(ServiceError)as caught:c.preflight(adapter,self.grant)
        self.assertEqual(caught.exception.details['proof']['denials']['credential_read'],0)
        secret='synthetic-sensitive-value-for-test'
        adapter.check=lambda argv:{'exitCode':1,'stdout':secret,'stderr':'PermissionError: '+secret}
        with self.assertRaises(ServiceError)as caught:c.preflight(adapter,self.grant)
        self.assertNotIn(secret,json.dumps(caught.exception.details))
        self.assertEqual(caught.exception.details['stdout_class'],'invalid_json')
        self.assertEqual(caught.exception.details['stderr_classes'],['PermissionError'])
        self.assertIsNone(caught.exception.details['proof'])

    def test_preflight_denial_zero_model_then_handoff_durable_no_blind_replay(self):
        self.service.submit(self.payload);adapter=self.proof_adapter(False)
        TaskWorker(self.store,adapter_factory=adapter,writer_check=lambda g:True).run_one()
        self.assertEqual(adapter.effects,0);self.assertFalse(self.store.task(c.TASK)['result'].get('trial_ready',False))

    def failed_preflight_with_owner(self, *, live=False):
        self.service.submit(self.payload);owner='fixture-old-owner';self.store.claim(owner)
        identity={'pid':os.getpid()if live else 1000000000,
            'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]if live else'0'}
        self.store.event(c.TASK,'dispatch-intent-'+owner,{'worker':{'owner':owner,**identity}})
        self.store.event(c.TASK,'stdio-child-'+owner,identity)
        self.store.update(c.TASK,owner,thread_id='fixture-old-thread',status='requires_reconcile',result={'error_class':'CSS_ISOLATION_UNVERIFIED'})
        with self.store._connection(True)as db:db.execute('UPDATE tasks SET lease_until=? WHERE id=?',(time.time()-60,c.TASK))
        task=self.store.task(c.TASK)
        return {'contract':c.EXECUTION_CONTRACT,'task_hash':digest(task),'observed_at':time.time(),
            'worker':identity,'child':identity,'actor':'trusted_local_owner'}

    def replacement_authority(self):
        task=self.store.task(c.TASK)
        return {'contract':c.EXECUTION_CONTRACT,'task_id':c.TASK,'task_hash':digest(task),
            'intent_hash':digest(task['intent']),'request_hash':digest(task['payload']),'grant_hash':task['intent']['grant_hash'],
            'actor':'trusted_local_owner','version':1,'replacement_limit':1,'model_turn_limit':1,
            'expires_at':time.time()+60,'old_thread_id':task['thread_id']}

    def test_closed_terminal_fence_rejects_live_identity_and_preserves_row(self):
        proof=self.failed_preflight_with_owner(live=True);before=self.store.task(c.TASK)
        with self.assertRaises(ServiceError)as caught:self.store.clear_closed_terminal_owner(c.TASK,proof)
        self.assertEqual(caught.exception.code,'CLOSED_OWNER_STILL_ALIVE')
        self.assertEqual(before,self.store.task(c.TASK))

    def test_two_step_recovery_preserves_original_failure_and_consumes_once(self):
        proof=self.failed_preflight_with_owner();old=self.store.task(c.TASK)
        receipt=self.store.clear_closed_terminal_owner(c.TASK,proof)
        self.assertEqual(receipt,self.store.clear_closed_terminal_owner(c.TASK,proof))
        cleared=self.store.task(c.TASK)
        self.assertEqual({k:v for k,v in old.items()if k not in {'owner','lease_until'}},
                         {k:v for k,v in cleared.items()if k not in {'owner','lease_until'}})
        authority=self.replacement_authority();receipt=self.store.authorize_css_replacement(c.TASK,authority)
        task=self.store.task(c.TASK);self.assertEqual(task['status'],'queued');self.assertIsNone(task['thread_id'])
        self.assertEqual(receipt['prior_thread']['result'],old['result']);self.assertEqual(task['intent'],old['intent'])
        with self.assertRaises(ServiceError):self.store.authorize_css_replacement(c.TASK,authority)

    def test_recovery_cas_cannot_erase_committed_cancel_or_drift(self):
        proof=self.failed_preflight_with_owner();changed={**proof,'task_hash':'a'*64}
        with self.assertRaises(ServiceError):self.store.clear_closed_terminal_owner(c.TASK,changed)
        self.store.clear_closed_terminal_owner(c.TASK,proof);authority=self.replacement_authority()
        # Simulate a committed competing cancel flag; terminal public cancel is a no-op.
        with self.store._connection(True)as db:db.execute('UPDATE tasks SET cancel=1 WHERE id=?',(c.TASK,))
        with self.assertRaises(ServiceError):self.store.authorize_css_replacement(c.TASK,authority)
        task=self.store.task(c.TASK);self.assertTrue(task['cancel']);self.assertEqual(task['thread_id'],'fixture-old-thread')
        self.assertFalse(any(e['key']=='css-recovery-authority-v5'for e in self.store.events(c.TASK)))

    def test_owned_resume_queue_keeps_exact_thread_failure_and_intent(self):
        proof=self.failed_preflight_with_owner();proof['contract']=c.RESUME_CONTRACT
        self.store.clear_closed_terminal_owner(c.TASK,proof);old=self.store.task(c.TASK)
        authority={'contract':c.RESUME_CONTRACT,'actor':'trusted_local_owner','resume_limit':1,'model_turn_limit':1,
            'thread_limit':2,'task_hash':digest(old),'intent_hash':digest(old['intent']),'thread_id':old['thread_id']}
        receipt=self.store.authorize_css_owned_resume(c.TASK,authority);new=self.store.task(c.TASK)
        self.assertEqual(new['status'],'queued');self.assertEqual(new['thread_id'],old['thread_id'])
        self.assertEqual(new['intent'],old['intent']);self.assertEqual(new['result'],old['result'])
        self.assertEqual(receipt['prior_result'],old['result'])
        with self.assertRaises(ServiceError):self.store.authorize_css_owned_resume(c.TASK,authority)

    def test_owned_resume_cannot_queue_after_turn_intent(self):
        proof=self.failed_preflight_with_owner();proof['contract']=c.RESUME_CONTRACT
        self.store.clear_closed_terminal_owner(c.TASK,proof);old=self.store.task(c.TASK)
        self.store.event(c.TASK,'css-unique-model-turn-v5',{'model_turn_limit':1})
        with self.assertRaises(ServiceError):self.store.authorize_css_owned_resume(c.TASK,{
            'contract':c.RESUME_CONTRACT,'actor':'trusted_local_owner','resume_limit':1,'model_turn_limit':1,'thread_limit':2,
            'task_hash':digest(old),'intent_hash':digest(old['intent']),'thread_id':old['thread_id']})
        self.assertEqual(self.store.task(c.TASK)['status'],'requires_reconcile')

    def process_resume_fixture(self):
        from hub.css_process_config import CONTRACT
        proof=self.failed_preflight_with_owner();proof['contract']=CONTRACT
        self.store.clear_closed_terminal_owner(c.TASK,proof);old=self.store.task(c.TASK)
        prior={'contract':c.RESUME_CONTRACT,'thread_id':old['thread_id']}
        self.store.event(c.TASK,'css-owned-resume-authority-v6',{'authority':prior})
        self.store.event(c.TASK,'css-owned-load-resume-v6',{'thread_id':old['thread_id']})
        return {'contract':CONTRACT,'actor':'trusted_local_owner','resume_limit':2,'model_turn_limit':1,'thread_limit':2,
            'task_hash':digest(old),'intent_hash':digest(old['intent']),'thread_id':old['thread_id'],
            'prior_authority_hash':digest(prior),'expires_at':time.time()+60}

    def test_final_process_resume_preserves_prior_result_grant_and_consumption(self):
        authority=self.process_resume_fixture();old=self.store.task(c.TASK)
        receipt=self.store.authorize_css_process_resume(c.TASK,authority);new=self.store.task(c.TASK)
        self.assertEqual({k:v for k,v in old.items()if k!='status'},{k:v for k,v in new.items()if k!='status'})
        self.assertEqual(new['status'],'queued');self.assertEqual(receipt['prior_result'],old['result'])
        self.assertEqual(self.store.grant(self.grant['grant_id']),self.grant)
        with self.assertRaises(ServiceError):self.store.authorize_css_process_resume(c.TASK,authority)

    def test_final_process_resume_rejects_missing_first_resume_or_turn_or_expiry(self):
        authority=self.process_resume_fixture()
        for change in ({'prior_authority_hash':'a'*64},{'expires_at':time.time()-1},{'expires_at':self.grant['expires_at']+1}):
            with self.assertRaises(ServiceError):self.store.authorize_css_process_resume(c.TASK,{**authority,**change})
        self.store.event(c.TASK,'turn-intent-existing',{'thread_id':authority['thread_id']})
        with self.assertRaises(ServiceError):self.store.authorize_css_process_resume(c.TASK,authority)
        self.assertEqual(self.store.task(c.TASK)['status'],'requires_reconcile')

    def test_native_placeholders_require_exact_empty_set_and_identity(self):
        for name in c.PLACEHOLDERS:(self.trial/name).mkdir()
        snapshot=c.placeholder_snapshot();self.assertEqual(set(snapshot['directories']),set(c.PLACEHOLDERS))
        (self.trial/'.aws'/'unrequested').write_text('extra')
        with self.assertRaises(ServiceError):c.placeholder_snapshot()
        (self.trial/'.aws'/'unrequested').unlink();(self.trial/'.aws').rmdir()
        with self.assertRaises(ServiceError):c.placeholder_snapshot()

    def test_success_waits_for_trusted_checks_and_cancel_cannot_publish(self):
        self.service.submit(self.payload);adapter=self.proof_adapter()
        TaskWorker(self.store,adapter_factory=adapter,writer_check=lambda g:True).run_one()
        t=self.store.task(c.TASK);self.assertEqual(t['status'],'validating');self.assertIsNone(t['owner']);self.assertIsNone(t['lease_until'])
        self.assertEqual(adapter.effects,1);self.assertFalse(t['result']['source_promoted'])
        self.assertFalse(TaskWorker(self.store,adapter_factory=adapter,writer_check=lambda g:True).run_one())
        promoted=c.promote(self.store,t,writer_check=lambda g:True)
        self.assertEqual((self.source/'progress_ui.css').read_bytes(),self.after)
        with self.assertRaises(ServiceError):c.promote(self.store,t,writer_check=lambda g:True)
        self.store.cancel(c.TASK)
        with self.assertRaises(ServiceError):self.store.finalize_local_validation(c.TASK,digest(t['result']),{**promoted,'validation_scope':'canonical_project'})
        self.assertNotEqual(self.store.task(c.TASK)['status'],'checks_complete')

    def test_cas_drift_and_existing_temporary_file_preserved(self):
        p=self.source/'progress_ui.css';before,identity=c.read_file(p)
        p.write_bytes(b'other owner edit')
        with self.assertRaises(ServiceError):c.atomic_replace(p,before,identity,self.after)
        self.assertEqual(p.read_bytes(),b'other owner edit')
        tmp=p.with_name('.'+p.name+'.hub-css-trial.tmp');tmp.write_bytes(b'protected existing temporary')
        with self.assertRaises(FileExistsError):c.atomic_replace(p,before,identity,self.after)
        self.assertEqual(tmp.read_bytes(),b'protected existing temporary')

    def test_cancel_committed_before_promotion_prevents_source_effect(self):
        self.service.submit(self.payload);adapter=self.proof_adapter()
        TaskWorker(self.store,adapter_factory=adapter,writer_check=lambda g:True).run_one()
        t=self.store.task(c.TASK)
        self.store.event(c.TASK,'css-promotion-intent',{'sample':True})
        self.store.cancel(c.TASK);called=[]
        with self.assertRaises(ServiceError):self.store.perform_local_promotion(c.TASK,digest(t['result']),lambda:called.append(True))
        self.assertEqual(called,[]);self.assertEqual((self.source/'progress_ui.css').read_bytes(),self.before)

    def test_trusted_complete_stays_same_task_and_no_second_turn(self):
        self.service.submit(self.payload);adapter=self.proof_adapter()
        TaskWorker(self.store,adapter_factory=adapter,writer_check=lambda g:True,only_task_id=c.TASK).run_one()
        t=self.store.task(c.TASK);promoted=c.promote(self.store,t,writer_check=lambda g:True)
        self.store.finalize_local_validation(c.TASK,digest(t['result']),{**promoted,'validation_scope':'canonical_project'})
        self.assertEqual(self.store.task(c.TASK)['status'],'checks_complete')
        self.assertTrue(self.service.submit(self.payload)['replayed']);self.assertEqual(adapter.effects,1)


if __name__=='__main__':unittest.main()
