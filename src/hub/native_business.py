"""Root-registered native text exchange; real sources are never model mounts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

from .codex_adapter import AppServerAdapter, child_environment, profile_arguments
from .connections import parse_owned_hub_state
from .native_config_registration import load_registration, TASK
from .service_contract import ServiceError
from .workbench_store import digest

PROFILE='hub_story_exchange_v1'


def exchange_profile(root):
    root=Path(root)
    entries={':root':'deny',':minimal':'read',':tmpdir':'deny',':slash_tmp':'deny',
             str(root):'read',str(root/'output'):'write'}
    table='{'+','.join(json.dumps(key)+'='+json.dumps(value)for key,value in entries.items())+'}'
    return ('-c','default_permissions='+json.dumps(PROFILE),
            '-c','permissions.'+PROFILE+'.filesystem='+table,
            '-c','permissions.'+PROFILE+'.network.enabled=false')


def auth_mode():
    result=subprocess.run(['codex','login','status'],capture_output=True,text=True,timeout=5,env=child_environment())
    if result.returncode or (result.stdout+result.stderr).strip()!='Logged in using ChatGPT':
        raise ServiceError('BUSINESS_AUTH_MODE_UNVERIFIED',status=403)
    return {'source':'supported codex login status','mode':'ChatGPT','credential_bytes_read':0}


class NativeBusinessAdapter(AppServerAdapter):
    def __init__(self,root,*,hub_root,grant):
        self.hub_root=Path(hub_root);self.business_grant=grant
        self.owned_thread=None;self.owned_turn=None;self.owned_resume=False
        super().__init__(root)
        self.permission_profile=PROFILE
        self._registration()

    def _registration(self):
        from .story_exchange import validate
        validate(self.business_grant,live=True)
        identity=load_registration(self.hub_root)
        try:
            fd=os.open(self.hub_root/'STATE.yaml',os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb')as stream:
                before=os.fstat(stream.fileno());raw=stream.read(1048577);after=os.fstat(stream.fileno())
            stamp=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
            if not stat.S_ISREG(before.st_mode)or before.st_nlink!=1 or before.st_uid!=os.getuid()or len(raw)>1048576 or stamp(before)!=stamp(after):
                raise ValueError('state identity')
            state=parse_owned_hub_state(raw)['pch_refactor']['r54_implementation']
            wrapper=state['native_execution_registration']
            self.recovery_authority=state.get('native_execution_recovery')
            if type(wrapper)is not dict or set(wrapper)!={'body','grant_sha256'}:raise ValueError('registration')
            body=wrapper['body'];grant=self.business_grant;scope=grant['story_exchange']
            expected={'purpose':'story_exchange','goal_task_id':TASK,'task_id':grant['task_id'],
                'project_id':grant['project_id'],'root':str(self.root),'root_identity':grant['root_identity'],
                'canonical_root':scope['canonical_root'],'registry_hash':scope['registry_hash'],
                'specification_sha256':scope['specification_sha256'],'request_sha256':scope['request_sha256'],
                'config_identity_sha256':identity.semantic_sha256,'expires_at':grant['expires_at']}
            if body!=expected or digest(body)!=scope['registration_sha256']or wrapper['grant_sha256']!=digest(grant):
                raise ValueError('scope')
            return identity
        except ServiceError:raise
        except (OSError,ValueError,KeyError,TypeError):
            raise ServiceError('BUSINESS_NATIVE_REGISTRATION_REJECTED',status=503)from None

    def _configuration_hash(self):return self._registration().config_sha256

    def _profile_arguments(self):return exchange_profile(self.root)

    def _launch(self):
        args,names=super()._launch();old=profile_arguments(self.root)
        if tuple(args[-len(old):])!=old:raise ServiceError('CODEX_PROFILE_REJECTED',status=503)
        return tuple(args[:-len(old)])+self._profile_arguments(),names

    def _child_environment(self):
        return {**super()._child_environment(),'CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED':'1'}

    def open(self):
        self.auth_evidence=auth_mode()
        return super().open()

    def thread(self,*,previous=None):
        if self.owned_thread is not None:
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        self._registration()
        if previous is not None and (type(self.recovery_authority)is not dict or self.recovery_authority.get('thread_id')!=previous):
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        self.owned_thread='dispatching'
        if previous is None:self.owned_thread=super().thread()
        else:self.owned_thread=self._resume_interrupted(previous)
        return self.owned_thread

    def _resume_interrupted(self,previous):
        from .task_store import TaskStore
        value=self.recovery_authority;grant=self.business_grant;store=TaskStore(self.hub_root)
        fields={'purpose','task_id','grant_sha256','thread_id','interrupted_turn_id','reconcile_event_key','reason','effort','additional_turn_limit'}
        recovered=type(value)is dict and value.get('reconcile_event_key')=='business-preturn-reconciled-v1'
        amended=recovered or type(value)is dict and value.get('reconcile_event_key')=='business-runtime-amendment-v1'
        reason='owner_runtime600_preturn_recovery'if recovered else'owner_runtime600_after_two_timeouts'if amended else'input_overread'
        consumption='business-preturn-resume-intent'if recovered else'business-runtime-amendment-resume-intent'if amended else'business-resume-intent'
        if (type(value)is not dict or set(value)!=fields or value['purpose']!='story_exchange_interrupted_resume'
            or value['task_id']!=grant['task_id']or value['grant_sha256']!=digest(grant)or value['thread_id']!=previous
            or value['reason']!=reason or value['effort']!='medium'or value['additional_turn_limit']!=1):
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        task=store.task(grant['task_id']);events=store.events(grant['task_id'])
        event=next((e for e in events if e['key']==value['reconcile_event_key']),None)
        if (not task or task['intent']['grant']!=grant or task['thread_id']!=previous or task['turn_id']is not None
            or not event or event['data']['original_thread_id']!=previous
            or event['data']['original_turn_id']!=value['interrupted_turn_id']
            or event['data']['action']!='same_owned_thread_new_turn_after_verified_interruption'
            or any(e['key']==consumption for e in events)):
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        contract='PCH-STORY-PRETURN-RECOVERY-v1'if recovered else'PCH-STORY-RUNTIME-600-OWNER-v1'
        if amended and (grant['max_turn_seconds']!=600 or
                event['data'].get('authority',{}).get('owner_decision_sha256')!=digest('允许全部改为10 分钟上限')or
                event['data'].get('authority',{}).get('contract')!=contract):
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        store.event(grant['task_id'],consumption,{'authority_sha256':digest(value),'thread_id':previous})
        thread=self.call('thread/read',{'threadId':previous,'includeTurns':True})['thread']
        if (thread['id']!=previous or Path(thread['cwd'])!=self.root or not thread['turns']
            or thread['turns'][-1]['id']!=value['interrupted_turn_id']or thread['turns'][-1]['status']!='interrupted'):
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        if recovered and digest(thread['turns'])!=event['data'].get('observation',{}).get('turn_history_sha256'):
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        result=self.call('thread/resume',{'threadId':previous,'cwd':str(self.root),'permissions':PROFILE,'approvalPolicy':'on-request'})
        if result['thread']['id']!=previous or Path(result['thread']['cwd'])!=self.root:
            raise ServiceError('CODEX_THREAD_BINDING_REJECTED',status=409)
        self._profile_echo(result);self.owned_resume=True
        resumed_key='business-preturn-resumed'if recovered else'business-runtime-amendment-resumed'if amended else'business-resumed'
        store.event(grant['task_id'],resumed_key,{'thread_id':previous,'prior_turn_id':value['interrupted_turn_id'],'additional_turn_limit':1})
        return previous

    def _turn_overrides(self):return {'effort':'medium'}if self.owned_resume else{}

    def recovery_text(self,text):
        if not self.owned_resume:raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        return (text+'\n上次已完整读取输入，因反复读取大段重复内容在120秒上限中断，尚未产生结果。'
            '这次保留已读上下文，不再cat整份导出/Schema/历史；必要时只用JSON选择精确字段。'
            '现在直接用本地Python按既有模板生成三个完整的新故事候选并写output/result.json；'
            '保留所有固定身份与依赖，填完整文字与实际时间。质量和3项分母保持原合同，完成后简短报告。')

    def start(self,thread_id,text,image=None):
        if not self.owned_thread or self.owned_thread=='dispatching' or thread_id!=self.owned_thread or self.owned_turn is not None or image is not None:
            raise ServiceError('BUSINESS_NATIVE_REPLAY_REJECTED',status=409)
        self._registration();self.owned_turn='dispatching'
        self.owned_turn=super().start(thread_id,text,None)
        return self.owned_turn

    def interrupt(self,thread_id,turn_id):
        if not self.owned_thread or not self.owned_turn or thread_id!=self.owned_thread or turn_id!=self.owned_turn or turn_id=='dispatching':
            raise ServiceError('BUSINESS_NATIVE_BINDING_REJECTED',status=409)
        return super().interrupt(thread_id,turn_id)

    def verify_capabilities(self,thread_id):
        proof=super().verify_capabilities(thread_id)
        effective=self.call('config/read',{'cwd':str(self.root),'includeLayers':False})['config']
        profile=effective.get('permissions',{}).get(PROFILE,{})
        filesystem=profile.get('filesystem')
        if type(filesystem)is dict and filesystem.get('glob_scan_max_depth')is None:
            filesystem={k:v for k,v in filesystem.items()if k!='glob_scan_max_depth'}
        expected={':root':'deny',':minimal':'read',':tmpdir':'deny',':slash_tmp':'deny',
                  str(self.root):'read',str(self.root/'output'):'write'}
        if (filesystem!=expected or effective.get('default_permissions')!=PROFILE or profile.get('extends')is not None
                or profile.get('network',{}).get('enabled')is not False or effective.get('web_search')!='disabled'):
            raise ServiceError('BUSINESS_NATIVE_PROFILE_REJECTED',status=503)
        return {**proof,'effective_filesystem_verified':True}

    def isolation_proof(self):
        """Zero-model native probes read zero bytes outside the registered stage."""
        source=self.business_grant['story_exchange']['canonical_root']
        paths=[str(Path(source)/'workbench/creative_assistance/workspace.json'),str(self.hub_root/'STATE.yaml'),
               str(Path(child_environment()['HOME'])/'.codex/auth.json')]
        code=('import os,json,socket\n'
              'proof={"readable_inputs":0,"outside":[],"output_write":False,"network_errno":None}\n'
              'for p in '+repr([str(self.root/p['path'])for p in self.business_grant['story_exchange']['input_files']])+':\n'
              ' f=os.open(p,os.O_RDONLY);os.close(f);proof["readable_inputs"]+=1\n'
              'for p in '+repr(paths)+':\n'
              ' row=[]\n'
              ' for mode in (os.O_RDONLY,os.O_WRONLY):\n'
              '  try: f=os.open(p,mode);os.close(f);row.append(None)\n'
              '  except OSError as e: row.append(e.errno)\n'
              ' proof["outside"].append(row)\n'
              'p='+repr(str(self.root/'output/.isolation-probe'))+'\n'
              'f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(f);os.unlink(p);proof["output_write"]=True\n'
              'try: s=socket.socket();s.close()\n'
              'except OSError as e: proof["network_errno"]=e.errno\n'
              'print(json.dumps(proof))\n')
        result=self.check(('/usr/bin/python3','-B','-c',code))
        try:
            proof=json.loads(result['stdout'])
            valid=(result['exitCode']==0 and proof['readable_inputs']==5 and proof['output_write']is True
                and len(proof['outside'])==3 and all(len(row)==2 and all(e in {1,2,13,30}for e in row)for row in proof['outside'])
                and proof['network_errno']in {1,13})
        except (ValueError,KeyError,TypeError):valid=False
        if not valid:raise ServiceError('BUSINESS_NATIVE_ISOLATION_UNVERIFIED',status=503)
        return {**proof,'model_turns':0,'outside_bytes_read':0,'source':'native command/exec under business profile'}
