"""Public fixtures and negative boundaries; no native commands or auth queries."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_config_precheck as q
from hub.css_metadata_effects import MetadataEffects
from hub.service_contract import ServiceError


class FullMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=q.c.HUB_ROOT/q.c.PRIVATE
        cls.proof=json.loads((folder/q.PROTOCOL_SOURCE).read_text())
        cls.exports=json.loads((q.c.HUB_ROOT/cls.proof['schema_bundle']['file']).read_text())['json_schema']

    def rows(self):
        return [dict(t,enabled=t['name']=='code_mode_host')for t in self.proof['templates']]

    def test_source_order_entire_catalog_and_canonical_pagination(self):
        rows=self.rows();templates=self.proof['templates']
        flags,end=q.feature_page({'data':rows[:100],'nextCursor':'100'},templates,0)
        tail,end=q.feature_page({'data':rows[100:],'nextCursor':None},templates,end)
        self.assertEqual(end,152);self.assertEqual(len(flags|tail),152)
        self.assertIs((flags|tail)['code_mode_host'],True)
        for cursor in ('0100','101','0','private-cursor',None,100):
            with self.subTest(cursor=cursor),self.assertRaises(ServiceError):
                q.feature_page({'data':rows[:100],'nextCursor':cursor},templates,0)
        for cursor in ('152','100','private-cursor',0):
            with self.assertRaises(ServiceError):q.feature_page({'data':rows[100:],'nextCursor':cursor},templates,100)

    def test_unknown_missing_extra_duplicate_reordered_type_and_nullable_fields_reject(self):
        page={'data':self.rows()[:100],'nextCursor':'100'}
        changes=[lambda x:x['data'].pop(),lambda x:x['data'].append(copy.deepcopy(x['data'][0])),
            lambda x:x['data'].__setitem__(1,copy.deepcopy(x['data'][0])),lambda x:x['data'].reverse(),
            lambda x:x['data'][0].update(name='private-unknown'),lambda x:x['data'][0].update(enabled=0),
            lambda x:x['data'][0].update(defaultEnabled=0),lambda x:x['data'][0].update(stage='future'),
            lambda x:x['data'][0].pop('displayName'),lambda x:x['data'][0].update(private='secret'),
            lambda x:x.update(private='secret'),lambda x:x.pop('nextCursor')]
        for change in changes:
            value=copy.deepcopy(page);change(value)
            with self.subTest(change=change),self.assertRaises(ServiceError)as error:
                q.feature_page(value,self.proof['templates'],0)
            self.assertNotIn('private',error.exception.code)

    def fake_adapter(self):
        a=SimpleNamespace(snapshots=0,feature_active=False,feature_complete=False,initialized_accepted=True,
            initialized_sent=True,consumed={'initialize'},cursor=None,pages=0)
        rows=self.rows()
        def call(method,params):
            self.assertEqual(method,'experimentalFeature/list');self.assertEqual(params['threadId'],None)
            self.assertEqual(params['limit'],100);self.assertEqual(params['cursor'],a.cursor)
            start=int(a.cursor or 0);end=min(start+100,152);a.pages+=1
            a.cursor=str(end)if end<152 else None;a.feature_complete=a.cursor is None
            return {'data':copy.deepcopy(rows[start:end]),'nextCursor':a.cursor}
        a.call=call
        return a

    def test_two_actual_snapshots_and_all_enabled_flags_stable(self):
        a=self.fake_adapter()
        with mock.patch.object(q,'protocol_proof',return_value=self.proof):
            before=q.feature_snapshot(a)
            a.consumed|={'config/read','mcpServerStatus/list'}
            after=q.feature_snapshot(a)
            self.assertEqual(before,after);self.assertEqual((a.snapshots,a.pages),(2,4))
            with self.assertRaises(ServiceError):q.feature_snapshot(a)
        self.assertFalse(a.feature_active)

    def test_missing_initialize_wrong_phase_or_unsafe_feature_blocks_snapshot(self):
        with mock.patch.object(q,'protocol_proof',return_value=self.proof):
            for field,value in [('initialized_accepted',False),('initialized_sent',False),
                ('consumed',{'initialize','config/read'}),('feature_active',True)]:
                a=self.fake_adapter();setattr(a,field,value)
                with self.assertRaises(ServiceError):q.feature_snapshot(a)
            a=self.fake_adapter();old=a.call
            def unsafe(method,params):
                result=old(method,params)
                for row in result['data']:
                    if row['name']=='hooks':row['enabled']=True
                return result
            a.call=unsafe
            with self.assertRaises(ServiceError):q.feature_snapshot(a)
            self.assertFalse(a.feature_active)

    def test_schema_generator_exact440_export_identity_and_cleanup(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);(root/q.c.PRIVATE).mkdir(parents=True)
            a=SimpleNamespace(schema_temp_cleaned=False);effects=MetadataEffects();calls=[]
            def generated(argv,**kwargs):
                calls.append(argv[:3]);self.assertEqual(kwargs['env'],q.p.process_environment())
                self.assertEqual(kwargs['cwd'],root)
                if argv==['codex','--version']:return SimpleNamespace(returncode=0,stdout=b'codex-cli 0.159.3\n',stderr=b'')
                self.assertEqual(kwargs['timeout'],10)
                for name,body in self.exports.items():
                    path=Path(argv[-1])/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            with mock.patch.object(q.c,'HUB_ROOT',root),mock.patch.object(q,'protocol_proof',return_value=self.proof),\
                mock.patch.object(q.subprocess,'run',side_effect=generated):q.load_native_schemas(a,effects)
            self.assertEqual(len(a.schemas),440);self.assertEqual(len(calls),2);self.assertTrue(a.schema_temp_cleaned)
            self.assertFalse(Path(effects.schema_directory).exists())
            self.assertEqual(a.schema_hashes['ConfigReadResponse'],'4ec77e1a3eed746037149799c3b6bb8fe7b4b593e5930fc9f410e6282de9df50')

    def test_schema_drift_unexpected_output_link_and_missing_export_fail_and_clean(self):
        for kind in ('drift','link','missing','stdout'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory()as directory:
                root=Path(directory);(root/q.c.PRIVATE).mkdir(parents=True);a=SimpleNamespace();effects=MetadataEffects()
                def generated(argv,**kwargs):
                    if argv==['codex','--version']:return SimpleNamespace(returncode=0,stdout=b'codex-cli 0.159.3',stderr=b'')
                    target=Path(argv[-1]);(target/'unknown.json').write_text('secret')
                    if kind=='link':(target/'unknown.json').unlink();(target/'unknown.json').symlink_to('/etc/passwd')
                    if kind=='missing':(target/'unknown.json').unlink()
                    return SimpleNamespace(returncode=0,stdout=b'private prompt'if kind=='stdout'else b'',stderr=b'')
                with mock.patch.object(q.c,'HUB_ROOT',root),mock.patch.object(q,'protocol_proof',return_value=self.proof),\
                    mock.patch.object(q.subprocess,'run',side_effect=generated),self.assertRaises(ServiceError)as error:
                    q.load_native_schemas(a,effects)
                self.assertTrue(a.schema_temp_cleaned);self.assertFalse(Path(effects.schema_directory).exists())
                self.assertNotIn('private',error.exception.code)

    def test_receipt_is_immutable_and_intermediate_observation_never_queries_auth(self):
        receipt=q.AuthReceipt('ChatGPT',0)
        with self.assertRaises(AttributeError):receipt.mode='API'
        value={'projection':{},'identity':[1], '_private_content_hash':'private','_private_projects_hash':'private'}
        with mock.patch.object(q.p,'observe',return_value=value),mock.patch.object(q.c,'auth_check')as auth:
            q.observed_verified({},receipt)
            for invalid in (None,{},q.AuthReceipt('API',0),q.AuthReceipt('ChatGPT',1),q.AuthReceipt('ChatGPT',False)):
                with self.assertRaises(ServiceError):q.observed_verified({},invalid)
        auth.assert_not_called()

    def test_unproven_protocol_blocks_admission_before_readonly_native_guards(self):
        with mock.patch.object(q,'desktop_source_snapshot',return_value=('source','binary')),\
            mock.patch.object(q,'protocol_source_snapshot',return_value=None),\
            mock.patch.object(q,'fixed_inputs_current')as fixed,mock.patch.object(q.c,'auth_check')as auth,\
            mock.patch.object(q.subprocess,'Popen')as spawn:
            with self.assertRaises(ServiceError)as error:q.admit({})
        self.assertEqual(error.exception.code,'PUBLIC_PROTOCOL_UNPROVEN')
        fixed.assert_not_called();auth.assert_not_called();spawn.assert_not_called()


class MetadataEffectTests(unittest.TestCase):
    def audit(self):
        a=MetadataEffects();a.source_receipt=('public_fixture',);a.source_check=lambda:a.source_receipt
        return a

    def event(self,a,argv,cwd=None,env=None):
        callers={'login':('css_trial.py','auth_check'),'--version':('css_config_precheck.py','load_native_schemas'),
            'app-server':('css_config_precheck.py','load_native_schemas'),
            '--json':('task_storage.py','mount_identity'),'rev-parse':('css_capability_recovery.py','mapping_check')}
        with mock.patch.object(a,'caller',return_value=callers.get(argv[1])):
            return a.event('subprocess.Popen',(argv[0],argv,cwd,env))

    def test_exact_auth_environment_counts_and_no_raw_evidence(self):
        a=self.audit()
        from hub.codex_adapter import child_environment
        env=child_environment()
        self.event(a,['codex','login','status'],env=env);self.event(a,['codex','login','status'],env=env)
        with self.assertRaises(ServiceError):self.event(a,['codex','login','status'],env=env)
        text=json.dumps(a.fact());self.assertNotIn(env['HOME'],text);self.assertNotIn('PATH',text)
        self.assertEqual(a.counts['auth_mode'],2)

    def test_unknown_commands_cwd_environment_sockets_and_counts_deny_before_start(self):
        from hub.codex_adapter import child_environment
        failures=[(['codex','exec'],None,child_environment()),
            (['codex','--version'],'/other',q.p.process_environment()),
            (['codex','--version'],str(q.c.HUB_ROOT),child_environment()),
            (['git','status'],str(q.c.ROOT),None)]
        for argv,cwd,env in failures:
            a=self.audit()
            with self.assertRaises(ServiceError):self.event(a,argv,cwd,env)
            self.assertEqual(sum(a.counts.values()),0)
        for event in ('socket.__new__','socket.connect','socket.getaddrinfo','os.system','os.exec','os.posix_spawn','os.fork','os.forkpty'):
            a=self.audit()
            with self.assertRaises(ServiceError):a.event(event,())
            self.assertFalse(a.fact()['effect_limits_satisfied'])
        a=self.audit();argv=['findmnt','--json','--target',str(q.c.HUB_ROOT),'--output','TARGET,SOURCE,FSTYPE,UUID']
        for _ in range(5):self.event(a,argv)
        with self.assertRaises(ServiceError):self.event(a,argv)
        self.assertEqual(a.counts['mount'],5)

    def test_schema_target_is_new_bound_path_and_inherited_env_drift_is_rejected(self):
        a=self.audit();argv=['codex','app-server','generate-json-schema','--experimental','--out','/unknown']
        with self.assertRaises(ServiceError):self.event(a,argv,str(q.c.HUB_ROOT),q.p.process_environment())
        a=self.audit();a.schema_directory='/owned/new';argv[-1]='/owned/new'
        self.event(a,argv,str(q.c.HUB_ROOT),q.p.process_environment());self.assertEqual(a.counts['schema'],1)
        a=self.audit();a.parent_environment={'private':'secret'}
        with self.assertRaises(ServiceError):self.event(a,['git','rev-parse','HEAD'],str(q.c.ROOT))
        self.assertNotIn('secret',json.dumps(a.fact()))

    def test_same_git_argv_from_unowned_trial_verify_is_rejected(self):
        a=self.audit()
        with mock.patch.object(a,'caller',return_value=('css_trial.py','verify')),self.assertRaises(ServiceError):
            a.event('subprocess.Popen',('git',['git','rev-parse','HEAD'],str(q.c.ROOT),None))
        self.assertEqual(a.counts['source_head'],0)

    def test_real_auth_call_site_is_verified_with_fake_private_output(self):
        a=self.audit()
        def private_run(argv,**kwargs):
            a.event('subprocess.Popen',(argv[0],argv,kwargs.get('cwd'),kwargs['env']))
            return SimpleNamespace(returncode=0,stdout='Logged in using ChatGPT',stderr='')
        with mock.patch.object(q.c.subprocess,'run',side_effect=private_run):
            self.assertEqual(q.c.auth_check()['mode'],'ChatGPT')
        self.assertEqual(a.counts['auth_mode'],1)

    def test_missing_or_changed_public_source_blocks_every_codex_spawn(self):
        from hub.codex_adapter import child_environment
        for checker in (None,lambda:('changed',)):
            a=self.audit();a.source_check=checker
            with self.assertRaises(ServiceError):self.event(a,['codex','login','status'],env=child_environment())
            self.assertEqual(a.counts['auth_mode'],0)
