import copy
import json
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_trial as c
from hub import css_process_config as p
from hub.service_contract import ServiceError


class ProcessConfigTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);trial=self.root/'trial';trial.mkdir();(trial/'.codex').mkdir()
        from hub.codex_adapter import child_environment
        environment={**child_environment(),'HOME':str(self.root)}
        for patch in (mock.patch.object(c,'TRIAL',trial),
            mock.patch('hub.codex_adapter.child_environment',return_value=environment)):
            patch.start();self.addCleanup(patch.stop)

    def fixture(self):
        v={k:{} if t=='dict'else 'fixture'for k,t in c.CONFIG_TOP_TYPES.items()}
        v.update(model=p.MODEL,model_reasoning_effort='xhigh',approval_policy='never',sandbox_mode='danger-full-access')
        v['mcp_servers']={name:{k:{} if t=='dict'else [] if t=='list'else 10 if t=='int'else 'private-command'
            for k,t in row.items()}for name,row in c.CONFIG_MCP_TYPES.items()}
        return v

    def semantic_fixture(self):
        v=self.fixture();v.update(service_tier='priority',approvals_reviewer='user')
        v['desktop']={k:True if t is bool else 14 if t is int else [] if t is list else next(iter(p.DESKTOP_ENUMS[k]))
            for k,t in p.DESKTOP_TYPES.items()}
        v['features']={'memories':True};v['memories']={'generate_memories':True,'use_memories':True}
        v['agents']={'default_subagent_reasoning_effort':'medium','interrupt_message':True,'max_concurrent_threads_per_session':6}
        v['plugins']={'fixture':{'enabled':True}}
        v['marketplaces']={'fixture':{'source_type':'local','source':'/private/marketplace'}}
        v['projects']={'/fixture/project':{'trust_level':'trusted'}}
        v['skills']={'config':[{'path':'/private/skill/SKILL.md','enabled':False}]}
        return v

    def test_semantic_coverage_is_exhaustive_and_versioned(self):
        fact=p.semantic_projection(self.semantic_fixture())
        self.assertEqual(set(fact['coverage']),set(c.CONFIG_TOP_TYPES))
        self.assertEqual(len(fact['coverage']),15)
        self.assertTrue(all(p.SOURCE_VERSION in r['source'] and r['anchor'] for r in fact['coverage'].values()))
        self.assertEqual(fact['launch']['disabled_features'],list(p.FROZEN_DISABLES))
        self.assertEqual(fact['launch']['disabled_mcp'],fact['mcp_names'])
        self.assertEqual(fact['launch']['new_model_turns'],0)
        self.assertEqual(fact['launch']['remote_start'],'DisabledEphemeral')
        self.assertEqual(fact['launch']['profile_arguments'],list(__import__('hub.codex_adapter',fromlist=['profile_arguments']).profile_arguments(c.TRIAL,read_only=True)))
        self.assertFalse(fact['launch']['can_write_cwd']);self.assertTrue(fact['launch']['cold_resume_only'])

    def test_unknown_effects_in_every_subtree_are_closed(self):
        changes={
            'model':lambda v:v.update(model={}),
            'model_reasoning_effort':lambda v:v.update(model_reasoning_effort=[]),
            'service_tier':lambda v:v.update(service_tier='unclassified'),
            'approval_policy':lambda v:v.update(approval_policy='always'),
            'approvals_reviewer':lambda v:v.update(approvals_reviewer='guardian_subagent'),
            'sandbox_mode':lambda v:v.update(sandbox_mode='workspace-write'),
            'desktop':lambda v:v['desktop'].update(new_effect='secret'),
            'marketplaces':lambda v:v['marketplaces']['fixture'].update(command='secret'),
            'plugins':lambda v:v['plugins']['fixture'].update(mcp_servers={}),
            'mcp_servers':lambda v:v['mcp_servers']['node_repl'].update(new_effect='secret'),
            'features':lambda v:v['features'].update(remote_plugin=True),
            'memories':lambda v:v['memories'].update(version='v2'),
            'projects':lambda v:v['projects']['/fixture/project'].update(command='secret'),
            'skills':lambda v:v['skills']['config'][0].update(enabled=True),
            'agents':lambda v:v['agents'].update(researcher={'config_file':'secret'}),
        }
        self.assertEqual(set(changes),set(p.SEMANTIC_COVERAGE))
        for name,change in changes.items():
            with self.subTest(field=name):
                v=self.semantic_fixture();change(v)
                with self.assertRaises(ServiceError):p.semantic_projection(v)
        v=self.semantic_fixture();v['hooks']={}
        with self.assertRaises(ServiceError):p.semantic_projection(v)

    def test_semantic_secrets_and_selectors_have_no_value_hash(self):
        v=self.semantic_fixture();v['mcp_servers']['node_repl']['env']={'AUTH_TOKEN':'secret-one'}
        before=p.semantic_projection(v)
        v['mcp_servers']['node_repl']['env']['AUTH_TOKEN']='secret-two'
        v['mcp_servers']['node_repl']['env']={'secret-key':'secret-two'}
        v['mcp_servers']['github']['command']='secret-command'
        v['mcp_servers']['openaiDeveloperDocs']['url']='https://secret.test/?token=secret-two'
        v['marketplaces']['fixture']['source']='/secret-two/marketplace'
        v['skills']['config'][0]['path']='/secret-two/SKILL.md'
        v['projects']={'/secret-two/project':{'trust_level':'trusted'}}
        self.assertEqual(before,p.semantic_projection(v))
        self.assertNotIn('secret',json.dumps(before).replace('secret_values_or_hashes_retained',''))
        self.assertFalse(before['unrelated_selector_values_or_hashes_retained'])
        self.assertTrue(before['active_project_selectors_bound'])
        self.assertFalse(before['secret_values_or_hashes_retained'])

    def test_semantic_known_values_and_types_are_checked(self):
        mutations=[lambda v:v['desktop'].update(sansFontSize=True),
            lambda v:v['desktop'].update(localeOverride='unknown'),
            lambda v:v['desktop'].update(**{'enabled-reasoning-efforts':['unknown']}),
            lambda v:v['marketplaces']['fixture'].update(source_type='git'),
            lambda v:v['marketplaces']['fixture'].update(source='/root/../escape'),
            lambda v:v['projects']['/fixture/project'].update(trust_level='unknown'),
            lambda v:v['skills']['config'][0].update(path='relative'),
            lambda v:v['memories'].update(use_memories=1),
            lambda v:v['agents'].update(max_concurrent_threads_per_session=0),
            lambda v:v['mcp_servers']['node_repl'].update(env={'AUTH_TOKEN':{}}),
            lambda v:v['mcp_servers']['github'].update(args=[{}])]
        for index,change in enumerate(mutations):
            with self.subTest(index=index):
                v=self.semantic_fixture();change(v)
                with self.assertRaises(ServiceError):p.semantic_projection(v)
        v=self.semantic_fixture();before=p.semantic_projection(v)
        v['model']='other';v['model_reasoning_effort']='ultra'
        self.assertEqual(before,p.semantic_projection(v))
        v['desktop']['sansFontSize']=20
        self.assertEqual(before,p.semantic_projection(v))
        v['projects']['/another/project']={'trust_level':'untrusted'}
        v['plugins']['another']={'enabled':False}
        v['marketplaces']['another']={'source_type':'local','source':'/another/marketplace'}
        v['skills']['config'].append({'path':'/another/SKILL.md','enabled':False})
        v['features']['memories']=False;v['memories']['use_memories']=False
        self.assertEqual(before,p.semantic_projection(v))

    def test_semantic_disables_must_match_exact_runtime(self):
        v=self.semantic_fixture()
        with mock.patch('hub.codex_adapter.DISABLED_FEATURES',p.FROZEN_DISABLES[:-1]+('unclassified',)):
            with self.assertRaises(ServiceError):p.semantic_projection(v)
        with mock.patch.dict(p.SEMANTIC_COVERAGE,{'unknown':('inert','unknown','unknown')}):
            with self.assertRaises(ServiceError):p.semantic_projection(v)

    def test_active_selector_changes_pin_and_trusted_local_layer_is_rejected(self):
        v=self.semantic_fixture();before=p.semantic_projection(v)
        v['projects'][str(c.TRIAL)]={'trust_level':'untrusted'}
        after=p.semantic_projection(v);self.assertNotEqual(before['protected_sha256'],after['protected_sha256'])
        context=after['sensitive_structure']['projects']['bound_active_context']
        self.assertEqual(context['trust']['selected'],{'selector':str(c.TRIAL),'trust_level':'untrusted'})
        self.assertEqual(context['project_layers'][0]['disabled_reason'],'explicit_untrusted')
        v['projects'][str(c.TRIAL)]['trust_level']='trusted'
        with self.assertRaises(ServiceError):p.semantic_projection(v)

    def test_lookup_precedence_binds_canonical_original_project_and_repo_keys(self):
        closure={'cwd_keys':['/canonical','/original'],'project_root_keys':['/project'],'repo_root_keys':['/repo']}
        projects={k:{'trust_level':'trusted'if k=='/repo'else'untrusted'}for k in ['/canonical','/original','/project','/repo','/unrelated']}
        for key in ['/canonical','/original','/project','/repo']:
            fact=p.active_trust(projects,closure);self.assertEqual(fact['selected']['selector'],key)
            del projects[key]
        self.assertIsNone(p.active_trust(projects,closure)['selected']['selector'])

    def test_unverified_repository_local_config_and_auxiliary_layers_fail_closed(self):
        v=self.semantic_fixture();git=c.TRIAL/'.git';git.mkdir();head=git/'HEAD'
        head.write_text('ref: refs/heads/main\n')
        with self.assertRaises(ServiceError):p.semantic_projection(v)
        head.unlink();self.assertIsNone(p.semantic_projection(v)['sensitive_structure']['projects']['bound_active_context']['closure']['repo_root'])
        config=c.TRIAL/'.codex/config.toml';config.write_text('[features]\nhooks=true\n')
        with self.assertRaises(ServiceError):p.semantic_projection(v)
        config.unlink();hooks=c.TRIAL/'.codex/hooks';hooks.mkdir()
        with self.assertRaises(ServiceError):p.semantic_projection(v)
        hooks.rmdir();provider=self.root/'.codex';provider.mkdir();managed=provider/'managed_config.toml'
        managed.write_text('fixture')
        with self.assertRaises(ServiceError):p.semantic_projection(v)
        managed.unlink();alias=self.root/'alias';alias.symlink_to(c.TRIAL,target_is_directory=True)
        with mock.patch.object(c,'TRIAL',alias),self.assertRaises(ServiceError):p.semantic_projection(v)

    def test_readonly_profile_differs_from_normal_writer_without_expanding_scope(self):
        from hub.codex_adapter import AppServerAdapter,profile_arguments,PERMISSION_PROFILE,EVIDENCE_PERMISSION_PROFILE
        reader=AppServerAdapter(c.TRIAL,read_only=True);writer=AppServerAdapter(c.TRIAL)
        self.assertEqual(reader.permission_profile,EVIDENCE_PERMISSION_PROFILE)
        self.assertEqual(writer.permission_profile,PERMISSION_PROFILE)
        self.assertIn(json.dumps(str(c.TRIAL))+'="read"',profile_arguments(c.TRIAL,read_only=True)[3])
        self.assertIn(json.dumps(str(c.TRIAL))+'="write"',profile_arguments(c.TRIAL)[3])
        for args in (profile_arguments(c.TRIAL),profile_arguments(c.TRIAL,read_only=True)):
            self.assertIn('":root"="deny"',args[3]);self.assertIn('":minimal"="read"',args[3])
            self.assertIn('":tmpdir"="deny"',args[3]);self.assertTrue(args[5].endswith('.network.enabled=false'))
        with self.assertRaises(ServiceError):AppServerAdapter(c.TRIAL,read_only='yes')

    def test_global_config_identity_contents_and_all_projects_are_preserved_exactly(self):
        before={'identity':[1,2,3,4,5],'_private_content_hash':'private-global','_private_projects_hash':'private-projects'}
        facts=p.preservation_fact(before,copy.deepcopy(before));self.assertTrue(all(facts.values()))
        for key,value in [('identity',[1,2,3,4,6]),('_private_content_hash','changed'),('_private_projects_hash','changed')]:
            after=dict(before,**{key:value});facts=p.preservation_fact(before,after)
            self.assertFalse(all(facts.values()));self.assertNotIn('private',json.dumps(facts))
        with self.assertRaises(ServiceError):p.preservation_fact({},before)

    def test_ephemeral_environment_only_for_evidence_processes(self):
        from hub.codex_adapter import AppServerAdapter,child_environment
        from hub.css_capability_recovery import adapter
        basic=child_environment();self.assertNotIn(p.REMOTE_DISABLED_ENV,basic)
        expected={**basic,p.REMOTE_DISABLED_ENV:'1'}
        self.assertEqual(p.process_environment(),expected)
        self.assertEqual(AppServerAdapter(c.TRIAL)._child_environment(),basic)
        self.assertEqual(p.preflight_adapter({})._child_environment(),expected)
        self.assertEqual(adapter({},Path('/fixture'),{})._child_environment(),expected)
        with mock.patch.object(AppServerAdapter,'open')as opened:
            with self.assertRaises(ServiceError):p.preflight_adapter({'semantic_version':11}).open()
        opened.assert_not_called()

    def test_semantic_pin_verification_requests_new_projection(self):
        pin=p.semantic_projection(self.semantic_fixture());observation={'projection':pin}
        with mock.patch.object(p,'observe',return_value=observation)as observe,mock.patch.object(c,'auth_check'):
            self.assertEqual(p.verify(pin),observation)
        self.assertEqual(observe.call_args_list,[mock.call(semantic=True),mock.call(semantic=True)])
        with mock.patch.object(p,'observe',return_value={'projection':{}}),mock.patch.object(c,'auth_check'):
            with self.assertRaises(ServiceError):p.verify(pin)

    def test_only_two_default_values_may_change(self):
        v=self.fixture();before=p.protected_projection(v)
        v['model']='another-default';v['model_reasoning_effort']='ultra'
        self.assertEqual(before,p.protected_projection(v))
        v['approval_policy']='always'
        self.assertNotEqual(before,p.protected_projection(v))
        v['extra_effect']=True
        with self.assertRaises(ServiceError):p.protected_projection(v)

    def test_sensitive_values_have_no_values_or_value_fingerprints(self):
        v=self.fixture();v['mcp_servers']['node_repl']['env']={'AUTH_TOKEN':'first-private-value'}
        before=p.protected_projection(v);v['mcp_servers']['node_repl']['env']['AUTH_TOKEN']='second-private-value'
        self.assertEqual(before,p.protected_projection(v))
        self.assertNotIn('private',json.dumps(before));self.assertFalse(before['secret_values_or_hashes_retained'])
        v['mcp_servers']['unknown']={}
        with self.assertRaises(ServiceError):p.protected_projection(v)

    def test_native_snapshot_must_contain_frozen_process_values(self):
        self.assertEqual(p.configuration_fact({'config':{'model':p.MODEL,'model_reasoning_effort':p.EFFORT,
            'credentials':'private'}})['reasoning_effort'],p.EFFORT)
        with self.assertRaises(ServiceError):p.configuration_fact({'config':{'model':p.MODEL,'model_reasoning_effort':'ultra'}})
        self.assertNotIn('private',json.dumps(p.configuration_fact({'config':{'model':p.MODEL,'model_reasoning_effort':p.EFFORT}})))

    def test_marketplace_nonsecret_values_frozen_sensitive_children_redacted(self):
        v=self.fixture();v['marketplaces']={'official':{'enabled':True,'url':'https://example.test/one','headers':{'AUTH':'first'}}}
        before=p.protected_projection(v)
        v['marketplaces']['official']['headers']['AUTH']='second'
        self.assertEqual(before,p.protected_projection(v))
        v['marketplaces']['official']['enabled']=False
        self.assertNotEqual(before,p.protected_projection(v))
        v['marketplaces']['official']['enabled']=True
        v['marketplaces']['official']['url']='https://example.test/two'
        self.assertNotEqual(before,p.protected_projection(v))

    def test_actual_adapter_launch_resume_and_turn_freeze_same_values(self):
        from hub.codex_adapter import AppServerAdapter,PERMISSION_PROFILE
        events=[];proof={'after_start':{'directories':{}},'before':{'directories':{}}}
        store=SimpleNamespace(event=lambda ident,key,data:events.append({'key':key,'data':data}),
            events=lambda ident:[{'key':'css-placeholder-thread-start-v5','data':proof},*events])
        task={'intent':{'grant':{},'annotation':{}}};a=c.replacement_adapter(store,task,'fixture-owner')
        authority={'contract':p.CONTRACT,'thread_id':'fixture-existing-thread','protected_config_pin':{}}
        with mock.patch.object(c,'runtime_authority',return_value=authority),mock.patch.object(c,'source_check'),\
             mock.patch.object(c,'placeholder_snapshot',return_value={'directories':{}}),\
             mock.patch.object(AppServerAdapter,'open',side_effect=ServiceError('TEST_LAUNCH_BOUNDARY')):
            with self.assertRaises(ServiceError):a.open()
        self.assertEqual(a.command,('codex','app-server','--stdio',*p.OVERRIDES))
        calls=[]
        def call(method,params,timeout=15):
            calls.append((method,params))
            if method=='thread/resume':return {'thread':{'id':'fixture-existing-thread','cwd':str(c.TRIAL)},
                'model':p.MODEL,'reasoningEffort':p.EFFORT,'activePermissionProfile':{'id':PERMISSION_PROFILE,'extends':None}}
            return {'turn':{'id':'fixture-turn'}}
        a.call=call
        with mock.patch.object(c,'runtime_authority',return_value=authority),mock.patch.object(c,'observe_thread',return_value={'status':'idle'}):
            self.assertEqual(a.thread(previous='fixture-existing-thread'),'fixture-existing-thread')
        params=calls[0][1]
        self.assertTrue(params['excludeTurns']);self.assertEqual(params['model'],p.MODEL)
        self.assertEqual(params['config'],{'model_reasoning_effort':p.EFFORT})
        a.capability_thread='fixture-existing-thread'
        with mock.patch.object(c,'runtime_authority',return_value=authority),\
             mock.patch.object(c,'placeholder_snapshot',return_value=proof['after_start']),\
             mock.patch.object(c,'prompt',return_value='fixed prompt'),\
             mock.patch.object(a,'_configuration_hash',return_value='nonsecret-fixture'),\
             mock.patch('hub.codex_adapter.config_fingerprint',return_value='nonsecret-fixture'):
            self.assertEqual(a.start('fixture-existing-thread','fixed prompt'),'fixture-turn')
        self.assertEqual(calls[-1][0],'turn/start')
        self.assertEqual({k:calls[-1][1][k]for k in ('model','effort')},{'model':p.MODEL,'effort':p.EFFORT})
        self.assertEqual(sum(e['key']=='css-unique-model-turn-v5'for e in events),1)
