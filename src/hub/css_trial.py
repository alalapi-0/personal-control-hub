"""One owner-authorized CSS trial; model output cannot authorize promotion."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

import yaml

from .service_contract import ServiceError
from .workbench_store import digest

HUB_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path('/home/alalapi/Projects/computer-study-plan')
HEAD = 'f5570473cf213f42b52b597e3e46e368dc53932e'
BEFORE_SHA = '20a1d7e6819684232f8675e0841f026e3ab379046a25b75cb8a39cf2f839df1d'
AFTER_SHA = 'babb5bc2f53a92017df6042f4246e8b3c41ec803d4ba3b34bde188f70c286ac1'
CONTRACT = 'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v1'
EXECUTION_CONTRACT = 'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v5-REPLACEMENT'
RESUME_CONTRACT = 'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v6-OWNED-RESUME'
TASK = 'csp-css-trial-20261007'
TRIAL = Path('/home/alalapi/Temp/personal-control-hub/linux-workbench') / TASK
OUTSIDE_PROBE = Path('/tmp/hub-lwb-css-outside-'+TASK)
CREDENTIAL_PATH = Path('/home/alalapi/.codex/auth.json')
PARENT_CANARY = TRIAL.parent/('parent-canary-'+TASK)
PARENT_WRITE_PROBE = TRIAL.parent/('deny-parent-write-'+TASK)
PLACEHOLDERS = ('.agents','.aws','.codex','.git')
PRIVATE = 'data/workbench/csp-css-trial'
DOCS = {'docs/PROJECT_STATE.md':'afcd3027fcace6120c4c436af804fcc3594f6ed889f5364386bacd676c1c2b7e',
        'docs/NEXT_ACTIONS.md':'4a5cea91842a95695f1ed9a8752edd7e2cdd683a8190a13349121d7e36fdf5e8'}
IMAGE = 'docs/reports/linux-workbench/csp-preview-desktop.jpg'
IMAGE_OUTPUT = Path('/data/ProjectOutputs/personal-control-hub/linux-workbench/csp-preview-desktop.jpg')
DATA_UUID = '98a6a740-bf5c-41b5-90fe-fd8e78fa5f55'
FIELDS = {'contract','canonical_root','head','source_sha256','source_mode','source_identity',
          'docs_sha256','before_ref','expected_sha256','image_ref','registry_hash','outside_probe','config_identity'}


def sha(raw): return hashlib.sha256(raw).hexdigest()


def config_identity():
    from .codex_adapter import child_environment
    info=(Path(child_environment()['HOME'])/'.codex/config.toml').lstat()
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def auth_check():
    from .codex_adapter import child_environment
    login=subprocess.run(['codex','login','status'],capture_output=True,text=True,timeout=5,env=child_environment())
    if login.returncode or (login.stdout+login.stderr).strip()!='Logged in using ChatGPT':
        raise ServiceError('CSS_AUTH_MODE_UNVERIFIED',status=403)
    return {'source':'supported codex login status','mode':'ChatGPT','exit':0,'credential_bytes_read':0}


def prepare():
    """Root-only admission using canonical stores and a registered screenshot."""
    from .codex_adapter import VERSION,PERMISSION_PROFILE,REGISTERED_CONFIG_SHA256,profile_arguments
    from .project_service import ProjectService
    from .design_service import DesignService
    from .design_store import DesignStore
    from .preview_service import PreviewService
    from .readonly_preview import ReadOnlyPreview
    from .workbench_store import WorkbenchStore
    from .task_service import TaskService,root_identity,fingerprint,validate_grant
    from .task_store import TaskStore
    private=HUB_ROOT/PRIVATE
    if TRIAL.exists()or OUTSIDE_PROBE.exists()or private.exists():
        raise ServiceError('CSS_TRIAL_ALREADY_EXISTS',status=409)
    store=TaskStore(HUB_ROOT)
    if store.path.exists()or store.registration_path.exists():raise ServiceError('CSS_LEDGER_ALREADY_EXISTS',status=409)
    ws=WorkbenchStore(HUB_ROOT)
    if ws.path.exists():raise ServiceError('CSS_ANNOTATION_STORE_ALREADY_EXISTS',status=409)
    projects=ProjectService(HUB_ROOT);designs=DesignService(DesignStore(HUB_ROOT,'data/design_governance/design-store.json'))
    previews=PreviewService(projects,designs,ws);previews.readonly_preview=ReadOnlyPreview(HUB_ROOT,projects,designs)
    view=next(v for v in previews.catalog()['previews']if v['binding']['candidate_id']=='csp-home-readonly-code'and
        v['binding']['artifact_id']=='csp-readonly-home-desktop')
    before,identity=read_file(ROOT/'progress_ui.css',mode=0o644);expected_css(before)
    private.mkdir(parents=True);(private/'before.css').write_bytes(before)
    TRIAL.mkdir(parents=True);(TRIAL/'progress_ui.css').write_bytes(before);os.chmod(TRIAL/'progress_ui.css',0o644)
    OUTSIDE_PROBE.write_text('Root-owned nonsecret isolation canary; zero bytes may be read by model sandbox.\n')
    command={'request_id':'csp-css-trial-annotation-v1','expected_revision':0,'binding':view['binding'],'kind':'whole','region':None,
        'requested_change':'功能试验：仅将首页 .home-task-card 边框 rgba(248, 238, 229, 0.15) 的 alpha 改为 0.24，使边框略清晰；保留旧版和新版。',
        'preserve_scope':'保留喜爱的整体风格；其余 CSS 字节、布局、字体、尺寸、动画、配色、HTML、JS、API、课程、学习数据、练习、配置均不改。',
        'view':{'viewport_width':1265,'viewport_height':742,'dpr':1,'scroll_x':0,'scroll_y':0,'zoom':1,'image_width':1265,
            'image_height':742,'natural_width':1265,'natural_height':742,'image_left':0,'image_top':0,'fit':'image-content-box'}}
    grant={'grant_id':'csp-css-trial-grant-v1','version':1,'source':'verified_owner_decision','task_id':TASK,'project_id':'computer-study-plan',
        'root':str(TRIAL),'root_identity':root_identity(TRIAL),'fingerprint':fingerprint(TRIAL),'mode':'new','parent_task_id':None,
        'annotation_hash':digest(command),'checks':['csp_css_trial'],'expires_at':time.time()+7200,'isolated_root':True,'writable_scope':['.'],
        'prohibited':[],'auth':'ChatGPT','max_turn_seconds':120,'image':'registered_preview','image_sha256':view['binding']['artifact_sha256'],
        'css_trial':{'contract':CONTRACT,'canonical_root':str(ROOT),'head':HEAD,'source_sha256':BEFORE_SHA,'source_mode':0o644,
            'source_identity':identity,'docs_sha256':dict(DOCS),'before_ref':PRIVATE+'/before.css','expected_sha256':AFTER_SHA,'image_ref':IMAGE,
            'registry_hash':view['binding']['registry_hash'],'outside_probe':str(OUTSIDE_PROBE),'config_identity':config_identity()}}
    validate_grant(grant,live=True)
    if not writer_check(grant):raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
    annotation=previews.save(command,backend_instance=secrets.token_hex(16),actor='trusted_local_owner')
    store.register_storage();store.register_grant(grant)
    payload={'request_id':TASK,'project_id':'computer-study-plan','annotation_id':command['request_id'],'annotation_revision':1,
        'grant_id':grant['grant_id'],'grant_version':1,'mode':'new','parent_task_id':None}
    service=TaskService(projects,previews,store,test_gate=True,writer_check=writer_check)
    submitted=service.submit(payload,owner_context='direct_human_authorization_to_trusted_local_owner')
    record={'contract':CONTRACT,'authority':'direct human approved pilot and Root smallCSSsuggestion; preserve old/new; Root supplied exact test requirement',
        'grant':grant,'payload':payload,'annotation':command,'annotation_receipt':annotation,'intent':store.task(TASK)['intent'],
        'prompt':prompt(command),'prompt_sha256':sha(prompt(command).encode()),'profile':PERMISSION_PROFILE,
        'profile_arguments':list(profile_arguments(TRIAL)),'codex_version':VERSION,'nonsecret_config_sha256':REGISTERED_CONFIG_SHA256,
        'auth_status':auth_check(),'initial_contents':fingerprint(TRIAL),'max_turn_seconds':120,'new_thread_limit':1,'model_turn_limit':1,
        'allowed_RPC':'initialize,thread/start,thread/read/list,capability discovery,command/exec fixed probes,turn/start/poll/interrupt exact only',
        'fixed_checks':'CSS exact bytes/mode/files; Node/diff; existing isolated UI and temporary transaction suites; readonlyFixtureServer desktop/narrow/write405; no real mark_done',
        'cleanup':'exactowned adapter/fixture/tabs/TRIAL/outsideprobe; retain Hub before/after/ledger/artifacts',
        'recovery':'unknown outcome exacttask/thread/turn/source reconcile, no second turn; CASdrift stop; postchecksfail exactnewhashCSSrestoreonly; docsinterruption reconcile',
        'retained_failure':'old configpin drift stopped before annotation/store/model/source; unknown historicalconfigdiff; Root-owned partialinput verified/deleted/recreated',
        'source_effects':0,'model_turns':0}
    (private/'registration.json').write_text(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    return {'task_id':submitted['task']['id'],'status':submitted['task']['status'],'grant_sha256':digest(grant),
        'intent_sha256':digest(store.task(TASK)['intent']),'prompt_sha256':record['prompt_sha256'],
        'registration_sha256':sha((private/'registration.json').read_bytes()),'source_effects':0,'model_turns':0}


def image_bytes_path():
    legacy = HUB_ROOT / IMAGE
    if not legacy.is_symlink():
        return legacy
    if Path(os.readlink(legacy)) != IMAGE_OUTPUT:
        raise ServiceError('IMAGE_PATH_REJECTED', status=409)
    data = Path('/data')
    mount = json.loads(subprocess.check_output(
        ['/usr/bin/findmnt', '--target', '/data', '--json', '--output', 'TARGET,FSTYPE,UUID,OPTIONS'], text=True))['filesystems'][0]
    options = str(mount.get('options') or '')
    if (data.is_symlink() or data.resolve() != data or mount.get('target') != '/data' or mount.get('fstype') != 'ext4'
            or mount.get('uuid') != DATA_UUID or 'rw' not in options.split(',')):
        raise ServiceError('IMAGE_DATA_DISK_UNAVAILABLE', status=409)
    info = IMAGE_OUTPUT.lstat()
    if IMAGE_OUTPUT.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_dev != data.stat().st_dev:
        raise ServiceError('IMAGE_PATH_REJECTED', status=409)
    return IMAGE_OUTPUT


def read_file(path, *, mode=None):
    from .task_storage import components
    path = Path(path); components(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1048576:
            raise ServiceError('CSS_FILE_REJECTED',status=409)
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            raise ServiceError('CSS_MODE_CHANGED',status=409)
        raw = stream.read(1048577); after = os.fstat(stream.fileno())
    identity = [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]
    if identity != [after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns]:
        raise ServiceError('CSS_FILE_CHANGED',status=409)
    return raw, identity


def expected_css(before):
    if sha(before) != BEFORE_SHA: raise ServiceError('CSS_PREIMAGE_REJECTED',status=409)
    blocks = list(re.finditer(rb'(?m)^\.home-task-card\s*\{[^}]*\}',before))
    old=b'border: 1px solid rgba(248, 238, 229, 0.15);'
    new=b'border: 1px solid rgba(248, 238, 229, 0.24);'
    if len(blocks)!=1 or blocks[0][0].count(old)!=1:raise ServiceError('CSS_SELECTOR_REJECTED')
    b=blocks[0]; result=before[:b.start()]+b[0].replace(old,new,1)+before[b.end():]
    if sha(result)!=AFTER_SHA:raise ServiceError('CSS_EXPECTED_VERSION_REJECTED')
    return result


def validate(grant, *, live=False, preparing_recovery=False):
    from .task_service import root_identity, fingerprint
    t=grant.get('css_trial')
    if (type(t)is not dict or set(t)!=FIELDS or t['contract']!=CONTRACT or
        grant['task_id']!=TASK or grant['project_id']!='computer-study-plan' or
        grant['mode']!='new' or grant['parent_task_id']is not None or Path(grant['root'])!=TRIAL or
        t['canonical_root']!=str(ROOT) or t['head']!=HEAD or t['source_sha256']!=BEFORE_SHA or
        t['expected_sha256']!=AFTER_SHA or t['source_mode']!=0o644 or t['docs_sha256']!=DOCS or
        t['before_ref']!=PRIVATE+'/before.css' or t['image_ref']!=IMAGE or
        t['outside_probe']!=str(OUTSIDE_PROBE) or
        grant['image']!='registered_preview' or grant['checks']!=['csp_css_trial'] or
        not re.fullmatch('[a-f0-9]{64}',str(t['registry_hash']))or
        type(t['config_identity'])is not list or len(t['config_identity'])!=5 or
        any(type(n)is not int for n in t['config_identity'])):
        raise ServiceError('CSS_TRIAL_GRANT_REJECTED',status=403)
    if live:
        actual=fingerprint(TRIAL)
        if any(row[0]in PLACEHOLDERS for row in actual):
            from .task_store import TaskStore
            runtime_authority(TaskStore(HUB_ROOT))
            snapshot=placeholder_snapshot()
            actual=[row for row in actual if row[0]not in snapshot['directories']]
        if root_identity(TRIAL)!=grant['root_identity'] or actual!=grant['fingerprint']:
            raise ServiceError('GRANT_VERSION_STALE',status=409)
        if grant['fingerprint']!=[['progress_ui.css',BEFORE_SHA]]:raise ServiceError('CSS_TRIAL_CONTENT_REJECTED')
        before,_=read_file(HUB_ROOT/t['before_ref']); expected_css(before)
        trial,_=read_file(TRIAL/'progress_ui.css',mode=0o644)
        if trial!=before:raise ServiceError('CSS_TRIAL_CONTENT_REJECTED')
        image,_=read_file(image_bytes_path())
        if sha(image)!=grant['image_sha256']:raise ServiceError('IMAGE_VERSION_STALE',status=409)
        source_check(grant,preparing_recovery=preparing_recovery)
    return grant


def source_check(grant, *, projects=None, diagnostic=False, preparing_recovery=False):
    from .project_service import ProjectService
    from .task_service import root_identity
    from .codex_adapter import config_fingerprint, REGISTERED_CONFIG_SHA256, child_environment
    t=grant['css_trial'];text=(HUB_ROOT/'STATE.yaml').read_text()
    m=re.search(r'^linux_visual_workbench:\n.*?(?=^[^ \n#][^\n]*:\n|\Z)',text,re.M|re.S)
    if m is None:raise ServiceError('CSS_TRIAL_AUTHORITY_REVOKED',status=403)
    s=yaml.safe_load(m[0])['linux_visual_workbench']
    authorized=[g for g in s.get('authorization',{}).get('external_write_grants',[]) if
        g.get('state')=='active' and g.get('contract_id')==CONTRACT and g.get('grant_id')==grant['grant_id'] and
        g.get('task_id')==TASK and g.get('root')==str(ROOT) and g.get('head')==HEAD and
        g.get('trial_root')==str(TRIAL) and g.get('paths')==['progress_ui.css',*DOCS]]
    authority='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v4-DIAGNOSTIC'if diagnostic else EXECUTION_CONTRACT
    from .css_process_config import CONTRACT as process_contract
    if len(authorized)!=1 or s.get('contract',{}).get('id')not in ({authority,RESUME_CONTRACT,process_contract}if not diagnostic else{authority}):
        raise ServiceError('CSS_TRIAL_AUTHORITY_REVOKED',status=403)
    resolver=(projects or ProjectService(HUB_ROOT))._resolver();resolver._check_registry()
    p=resolver.projects.get('computer-study-plan',{})
    if p.get('root_path')!=str(ROOT) or resolver.authority['registry_hash']!=t['registry_hash']:
        raise ServiceError('CSS_TRIAL_MAPPING_STALE',status=409)
    from .preview_service import PreviewService
    if not PreviewService._eligible(p):raise ServiceError('TASK_PROJECT_REJECTED',status=403)
    from .readonly_preview import ReadOnlyPreview
    from .design_service import DesignService
    from .design_store import DesignStore
    ReadOnlyPreview(HUB_ROOT,projects or ProjectService(HUB_ROOT),
        DesignService(DesignStore(HUB_ROOT,'data/design_governance/design-store.json'))).check()
    root_identity(ROOT)
    actual=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,timeout=5)
    if actual.returncode or actual.stdout.strip()!=HEAD:raise ServiceError('CSS_SOURCE_VERSION_STALE',status=409)
    raw,identity=read_file(ROOT/'progress_ui.css',mode=0o644)
    if sha(raw)!=BEFORE_SHA or identity!=t['source_identity']:raise ServiceError('CSS_SOURCE_VERSION_STALE',status=409)
    for f,h in DOCS.items():
        if sha(read_file(ROOT/f,mode=0o644)[0])!=h:raise ServiceError('CSS_DOC_VERSION_STALE',status=409)
    if diagnostic:
        if config_fingerprint(Path(child_environment()['HOME'])/'.codex/config.toml')!=REGISTERED_CONFIG_SHA256 or \
            config_identity()!=t['config_identity']:raise ServiceError('CODEX_CONFIG_DRIFT',status=503)
        auth_check()
    elif preparing_recovery:prepared_config_pin()
    else:
        from .task_store import TaskStore
        runtime_authority(TaskStore(HUB_ROOT))


def writer_check(grant, *, bound_task=None, recovery_pin=None):
    """Fresh supported discovery, combined with the Root-owned trial identity."""
    from .codex_adapter import ReadonlyAppServerAdapter
    allowed=bound_task.get('thread_id')if bound_task else None
    with tempfile.TemporaryDirectory(prefix='hub-session-discovery-')as directory:
        pin=recovery_pin
        if pin is None and 'css_trial'in grant:
            from .task_store import TaskStore
            store=TaskStore(HUB_ROOT);authority=runtime_authority(store)
            pin=authority.get('recovery_config_pin')
            if 'protected_config_pin'in authority:pin={'process_projection':authority['protected_config_pin']}
            if bound_task is None:allowed=store.task(TASK)['thread_id']
        adapter=readonly_recovery_adapter(Path(directory),pin)if pin else ReadonlyAppServerAdapter(Path(directory))
        try:
            adapter.open()
            for cwd in (ROOT,TRIAL):
                result=adapter.call('thread/list',{'cwd':str(cwd),'sourceKinds':['cli','appServer','vscode'],
                    'limit':20,'archived':False,'useStateDbOnly':True})
                if result.get('nextCursor')is not None or result.get('backwardsCursor')is not None:return False
                if cwd==ROOT and result['data']:return False
                if cwd==TRIAL and any(t['id']!=allowed for t in result['data']):return False
                if cwd==TRIAL and allowed and any(t['status']['type']=='active' for t in result['data']):return False
            return True
        finally:adapter.close()


def prompt(annotation):
    return ('Hub owner-authorized computer-study-plan CSS trial; cwd is an isolated CSS-only copy, '
        'not the canonical repository. Only progress_ui.css may be read or changed. Use the native local tool. '
        'In the sole .home-task-card block change exactly border: 1px solid rgba(248, 238, 229, 0.15); '
        'to border: 1px solid rgba(248, 238, 229, 0.24);. Preserve every other byte, file mode and final newline. '
        'Create no files, links, directories, background tasks or AGENTS file. No network, MCP, browser, '
        'credentials, config, permissions escalation, other projects or outside paths. The registered real '
        'desktop screenshot is reference only; keep its design style. Report the precise CSS change.\n'
        'Owner feedback:\n'+annotation['requested_change']+'\nPreserve:\n'+annotation['preserve_scope'])


def preflight(adapter, grant, *, expected_sha=None, process_only=False):
    """Native sandbox test in the same thread/profile, before any model turn."""
    paths={'canonical':str(ROOT/'progress_ui.css'),'parent':str(TRIAL.parent),
           'sibling':str(HUB_ROOT/PRIVATE/'before.css'),'tmp':grant['css_trial']['outside_probe'],
           'credential':str(CREDENTIAL_PATH),'parent_canary':str(PARENT_CANARY)}
    if any(not Path(p).exists()or Path(p).is_symlink()for p in paths.values()):
        raise ServiceError('CSS_DENIAL_PREIMAGE_MISSING',status=403)
    script="""import os,json,socket,hashlib
fd=os.open('progress_ui.css',os.O_RDWR|os.O_NOFOLLOW)
raw=os.read(fd,1048576);os.lseek(fd,0,0);assert os.write(fd,raw)==len(raw);os.fsync(fd);os.close(fd)
proof={'trial_sha256':hashlib.sha256(raw).hexdigest(),'denials':{},'read_bytes':0}
for category,path in PATHS.items():
 for mode,flags in [('read',os.O_RDONLY),('write',os.O_WRONLY)]:
  if category=='parent' and mode=='write':continue
  try:
   fd=os.open(path,flags|os.O_NOFOLLOW);os.close(fd);proof['denials'][category+'_'+mode]=0
  except OSError as e:proof['denials'][category+'_'+mode]=e.errno
try:
 os.listdir(PARENT);proof['parent_enumeration_errno']=0
except OSError as e:proof['parent_enumeration_errno']=e.errno
try:
 os.mkdir(PARENT_WRITE_PROBE);proof['parent_create_errno']=0
except OSError as e:proof['parent_create_errno']=e.errno
try:
 s=socket.socket();s.close();proof['network_errno']=0
except OSError as e:proof['network_errno']=e.errno
print(json.dumps(proof,sort_keys=True))
""".replace('PATHS',repr(paths)).replace('PARENT_WRITE_PROBE',repr(str(PARENT_WRITE_PROBE))).replace('PARENT',repr(str(TRIAL.parent)))
    argv=('/usr/bin/python3','-c',script)
    if process_only:
        from .codex_adapter import PERMISSION_PROFILE
        result=adapter.call('command/exec',{'command':list(argv),'cwd':str(TRIAL),'permissionProfile':PERMISSION_PROFILE,
            'timeoutMs':5000,'outputBytesCap':4096})
    else:result=adapter.check(argv)
    # Retain only fixed-probe fields, never arbitrary native output or paths.
    proof=None
    try:
        parsed=json.loads(result.get('stdout',''))
        if isinstance(parsed,dict):proof=parsed
    except (ValueError,TypeError):pass
    diagnostic={'exit_code':result.get('exitCode'),'stdout_sha256':sha(str(result.get('stdout','')).encode()),
        'stderr_sha256':sha(str(result.get('stderr','')).encode()),'stdout_bytes':len(str(result.get('stdout','')).encode()),
        'stderr_bytes':len(str(result.get('stderr','')).encode()),'stdout_class':'invalid_json'if proof is None else 'unexpected_proof',
        'stderr_classes':[name for name in ('PermissionError','FileNotFoundError','SyntaxError','Traceback','bwrap')
            if name in str(result.get('stderr',''))],'proof':None}
    if proof is not None and set(proof)=={'trial_sha256','denials','read_bytes','network_errno','parent_enumeration_errno','parent_create_errno'} and \
        isinstance(proof['denials'],dict) and set(proof['denials'])=={k+'_'+m for k in paths for m in ('read','write')if(k,m)!=('parent','write')} and \
        all(type(v)is int for v in proof['denials'].values()) and type(proof['network_errno'])is int and \
        type(proof['read_bytes'])is int and all(type(proof[k])is int for k in ('parent_enumeration_errno','parent_create_errno')) and \
        isinstance(proof['trial_sha256'],str) and re.fullmatch('[0-9a-f]{64}',proof['trial_sha256']):
        diagnostic['proof']=proof;diagnostic['stdout_class']='fixed_probe_json'
    if diagnostic['proof']is None:
        raise ServiceError('CSS_ISOLATION_UNVERIFIED',status=403,details=diagnostic)
    if (result['exitCode']!=0 or
        proof['trial_sha256']!=(expected_sha or BEFORE_SHA) or proof['read_bytes']!=0 or
        set(proof['denials'])!={k+'_'+m for k in paths for m in ('read','write')if(k,m)!=('parent','write')} or
        any(e not in (1,2,13,30)for e in proof['denials'].values()) or proof['network_errno']not in (1,13)or
        any(proof[k]not in (1,2,13,30)for k in ('parent_enumeration_errno','parent_create_errno'))or PARENT_WRITE_PROBE.exists()):
        raise ServiceError('CSS_ISOLATION_UNVERIFIED',status=403,details=diagnostic)
    return {'source':'process_fixed_command_exec'if process_only else'same_thread_native_command_exec',
        'profile':adapter.active_profile if not process_only else'hub_lwb_fixture_v1',**proof}


def observe_thread(adapter, thread_id, *, turn_id=None):
    """Read only supported fields; profile echo came from thread/start."""
    owned=getattr(adapter,'css_owned_resume',False)
    thread=adapter.call('thread/read',{'threadId':thread_id,'includeTurns':not owned})['thread']
    turns=thread['turns']
    if thread['id']!=thread_id or Path(thread['cwd'])!=TRIAL:
        raise ServiceError('CSS_THREAD_BINDING_CHANGED',outcome='UNKNOWN')
    if turn_id is None:
        if turns or thread['status']['type']!='idle':raise ServiceError('CSS_THREAD_NOT_EMPTY',outcome='UNKNOWN')
    elif owned:
        if thread['status']['type']!='idle'or getattr(adapter,'css_turn_id',None)!=turn_id:
            raise ServiceError('CSS_TURN_COUNT_OR_STATUS_CHANGED',outcome='UNKNOWN')
    elif len(turns)!=1 or turns[0]['id']!=turn_id or turns[0]['status']!='completed':
        raise ServiceError('CSS_TURN_COUNT_OR_STATUS_CHANGED',outcome='UNKNOWN')
    return {'thread_id':thread_id,'cwd_verified':True,'status':thread['status']['type'],
        'turns':[{'id':t['id'],'status':t['status']}for t in turns],
        'turn_evidence':'sole fenced turn intent plus exact native terminal event; history hydration unsupported'if owned else'hydrated history',
        'profile_evidence':'thread/start echo, explicit command/turn parameters and native denial probes; thread/read has no profile field'}


CONFIG_TOP_TYPES = {'agents':'dict','approval_policy':'str','approvals_reviewer':'str',
    'desktop':'dict','features':'dict','marketplaces':'dict','mcp_servers':'dict','memories':'dict',
    'model':'str','model_reasoning_effort':'str','plugins':'dict','projects':'dict','sandbox_mode':'str',
    'service_tier':'str','skills':'dict'}
CONFIG_MCP_TYPES = {name:{'command':'str','args':'list'}for name in
    ('chrome-devtools','context7','playwright','github')}
CONFIG_MCP_TYPES.update({'openaiDeveloperDocs':{'url':'str'},
    'node_repl':{'command':'str','args':'list','startup_timeout_sec':'int','env':'dict'}})


def config_observation():
    """Read without aliases, values in logs, body retention or hidden updates."""
    from .codex_adapter import child_environment
    path=Path(child_environment()['HOME'])/'.codex/config.toml'
    if any(p.is_symlink()for p in path.parents):raise ServiceError('CSS_CONFIG_ALIAS')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb')as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode)or before.st_nlink!=1 or before.st_uid!=os.getuid()or before.st_size>1048576:
            raise ServiceError('CSS_CONFIG_FILE_UNSAFE')
        raw=stream.read(1048577);after=os.fstat(stream.fileno());host=path.lstat()
    fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid','st_nlink')
    if any(getattr(before,k)!=getattr(after,k)or getattr(after,k)!=getattr(host,k)for k in fields):
        raise ServiceError('CSS_CONFIG_OBSERVATION_CHANGED')
    try:value=tomllib.loads(raw.decode())
    except (UnicodeError,ValueError):raise ServiceError('CSS_CONFIG_STRUCTURE_REJECTED')from None
    top={k:type(v).__name__ for k,v in value.items()}
    mcp={k:{n:type(v).__name__ for n,v in row.items()}for k,row in value.get('mcp_servers',{}).items()}
    if top!=CONFIG_TOP_TYPES or mcp!=CONFIG_MCP_TYPES or value['features']!={'memories':True}or \
        {k:type(v).__name__ for k,v in value['agents'].items()}!={'default_subagent_reasoning_effort':'str',
        'interrupt_message':'bool','max_concurrent_threads_per_session':'int'}or \
        (value['model'],value['approval_policy'],value['sandbox_mode'])!= \
        ('gpt-6.1-sol','never','danger-full-access')or value['model_reasoning_effort']not in ('xhigh','ultra'):
        raise ServiceError('CSS_CONFIG_STRUCTURE_REJECTED')
    return {'sha256':sha(raw),'identity':[getattr(after,k)for k in fields[:5]],
        'projection':{'top_key_types':top,'mcp_key_types':mcp,'model':value['model'],
        'reasoning_effort':value['model_reasoning_effort'],'approval_policy':value['approval_policy'],
        'sandbox_mode':value['sandbox_mode'],'features':{'memories':True},'permission_profile_names':[],
        'mcp_names':sorted(mcp),'body_retained':False,'credential_bytes_read':0}}


def prepared_config_pin(approved=None):
    first=config_observation();auth=auth_check();second=config_observation()
    if first!=second:raise ServiceError('CSS_ACTIVE_CONFIG_CHURN')
    if approved is not None and any(first[k]!=approved[k]for k in ('sha256','identity')):
        raise ServiceError('CSS_APPROVED_CONFIG_VERSION_CHANGED')
    return {**first,'auth':auth}


def verify_config_pin(pin):
    observation=prepared_config_pin(pin)
    if observation!={k:pin[k]for k in ('sha256','identity','projection','auth')}:
        raise ServiceError('CSS_RECOVERY_CONFIG_PIN_CHANGED')
    return pin['sha256']


def readonly_recovery_adapter(root,pin):
    from .codex_adapter import ReadonlyAppServerAdapter
    class RecoveryDiscovery(ReadonlyAppServerAdapter):
        def _configuration_hash(self):
            if 'process_projection'in pin:
                from .css_process_config import verify
                return verify(pin['process_projection'])['_private_content_hash']
            return verify_config_pin(pin)
    if 'process_projection'in pin:
        from .css_process_config import OVERRIDES
        return RecoveryDiscovery(root,command=('codex','app-server','--stdio',*OVERRIDES))
    return RecoveryDiscovery(root)


def owned_resume_authority(store):
    events=store.events(TASK)
    receipt=next((e['data']for e in events if e['key']=='css-owned-resume-authority-v6'),None)
    if receipt is None:raise ServiceError('CSS_OWNED_RESUME_AUTHORITY_MISSING')
    authority=receipt['authority'];task=store.task(TASK)
    original=next(e['data']['authority']for e in events if e['key']=='css-recovery-authority-v5')
    proof=next(e['data']for e in events if e['key']=='css-placeholder-thread-start-v5')
    if authority['contract']!=RESUME_CONTRACT or authority['original_authority_hash']!=digest(original)or \
        authority['intent_hash']!=digest(task['intent'])or authority['thread_id']!=task['thread_id']or \
        authority['expires_at']<=time.time()or authority['resume_limit']!=1 or authority['model_turn_limit']!=1 or \
        authority['thread_limit']!=2 or sha(read_file(PARENT_CANARY)[0])!=original['parent_canary']['sha256']or \
        read_file(PARENT_CANARY)[1]!=original['parent_canary']['identity']or \
        placeholder_snapshot()['directories']!=proof['after_start']['directories']:
        raise ServiceError('CSS_OWNED_RESUME_AUTHORITY_CHANGED')
    verify_config_pin(authority['recovery_config_pin'])
    return authority


def runtime_authority(store):
    if any(e['key']=='css-process-resume-authority-v7'for e in store.events(TASK)):
        return process_resume_authority(store)
    if any(e['key']=='css-owned-resume-authority-v6'for e in store.events(TASK)):
        return owned_resume_authority(store)
    return recovery_authority(store)


def process_resume_authority(store):
    from . import css_process_config as p
    events=store.events(TASK);a=next(e['data']['authority']for e in events if e['key']=='css-process-resume-authority-v7')
    task=store.task(TASK);v6=next(e['data']['authority']for e in events if e['key']=='css-owned-resume-authority-v6')
    original=next(e['data']['authority']for e in events if e['key']=='css-recovery-authority-v5')
    proof=next(e['data']for e in events if e['key']=='css-placeholder-thread-start-v5')
    if a['contract']!=p.CONTRACT or a['prior_authority_hash']!=digest(v6)or a['intent_hash']!=digest(task['intent'])or \
        a['thread_id']!=task['thread_id']or a['expires_at']<=time.time()or a['resume_limit']!=2 or \
        a['model_turn_limit']!=1 or a['thread_limit']!=2 or \
        sha(read_file(PARENT_CANARY)[0])!=original['parent_canary']['sha256']or \
        read_file(PARENT_CANARY)[1]!=original['parent_canary']['identity']or \
        placeholder_snapshot()['directories']!=proof['after_start']['directories']:
        raise ServiceError('CSS_PROCESS_RESUME_AUTHORITY_CHANGED')
    p.verify(a['protected_config_pin'])
    return a


def recovery_authority(store):
    rows=[e['data']for e in store.events(TASK)if e['key']=='css-recovery-authority-v5']
    if len(rows)!=1:raise ServiceError('CSS_RECOVERY_AUTHORITY_MISSING',status=403)
    authority=rows[0]['authority']
    task=store.task(TASK)
    if (authority['contract']!=EXECUTION_CONTRACT or authority['version']!=1 or
        authority['replacement_limit']!=1 or authority['model_turn_limit']!=1 or
        authority['expires_at']<=time.time()or authority['intent_hash']!=digest(task['intent'])or
        authority['grant_hash']!=task['intent']['grant_hash']or authority['request_hash']!=digest(task['payload'])or
        sha(read_file(PARENT_CANARY)[0])!=authority['parent_canary']['sha256']or
        read_file(PARENT_CANARY)[1]!=authority['parent_canary']['identity']):
        raise ServiceError('CSS_RECOVERY_AUTHORITY_CHANGED',status=403)
    from .codex_adapter import profile_arguments
    if authority['profile_argv']!=list(profile_arguments(TRIAL)):
        raise ServiceError('CSS_RECOVERY_PROFILE_CHANGED',status=403)
    pin=authority['recovery_config_pin']
    if pin['contract']!=EXECUTION_CONTRACT or pin['original_grant_identity']!=task['intent']['grant']['css_trial']['config_identity']:
        raise ServiceError('CSS_RECOVERY_CONFIG_PIN_CHANGED')
    verify_config_pin(pin)
    return authority


def prepare_replacement(approved_observation):
    """Root-only two-step reconciliation; no native thread/model dispatch here."""
    from .task_store import TaskStore
    from .codex_adapter import ReadonlyAppServerAdapter,profile_arguments,REGISTERED_CONFIG_SHA256
    store=TaskStore(HUB_ROOT);task=store.task(TASK);grant=task['intent']['grant']
    validate(grant,live=True,preparing_recovery=True)
    pin=prepared_config_pin(approved_observation)
    if PARENT_CANARY.exists()or PARENT_WRITE_PROBE.exists():raise ServiceError('CSS_RECOVERY_CANARY_EXISTS')
    if task['status']!='requires_reconcile'or task['turn_id']is not None or task['cancel']:
        raise ServiceError('CSS_RECOVERY_TASK_CHANGED')
    adapter=readonly_recovery_adapter(TRIAL,pin);unavailable=None
    try:
        adapter.open()
        try:adapter.call('thread/read',{'threadId':task['thread_id'],'includeTurns':False})
        except ServiceError as error:
            if error.code=='CODEX_RPC_REJECTED'and 'thread_unavailable'in error.details.get('error_categories',[]):unavailable=error.details
        listing=adapter.call('thread/list',{'cwd':str(TRIAL),'sourceKinds':['cli','appServer','vscode'],
            'limit':20,'archived':False,'useStateDbOnly':True})
        if unavailable is None or listing['data']or listing['nextCursor']is not None or listing.get('backwardsCursor')is not None:
            raise ServiceError('CSS_OLD_THREAD_OUTCOME_UNKNOWN')
    finally:adapter.close()
    if not writer_check(grant,bound_task=task,recovery_pin=pin):raise ServiceError('EXTERNAL_WRITER_UNKNOWN')
    events=store.events(TASK)
    dispatch=next(e['data']['worker']for e in events if e['key']=='dispatch-intent-'+task['owner'])
    child=next(e['data']for e in events if e['key']=='stdio-child-'+task['owner'])
    proof={'contract':EXECUTION_CONTRACT,'task_hash':digest(task),'actor':'trusted_local_owner','observed_at':time.time(),
        'worker':{k:dispatch[k]for k in ('pid','start_ticks')},'child':{k:child[k]for k in ('pid','start_ticks')}}
    verify_config_pin(pin)
    fence=store.clear_closed_terminal_owner(TASK,proof);task=store.task(TASK)
    with PARENT_CANARY.open('x')as stream:stream.write('Root-owned parent nonsecret canary; sandbox reads zero bytes.\n')
    os.chmod(PARENT_CANARY,0o600);canary,identity=read_file(PARENT_CANARY)
    authority={'contract':EXECUTION_CONTRACT,'version':1,'actor':'trusted_local_owner','task_id':TASK,'task_hash':digest(task),
        'intent_hash':digest(task['intent']),'request_hash':digest(task['payload']),'grant_hash':task['intent']['grant_hash'],
        'annotation_hash':grant['annotation_hash'],'old_thread_id':task['thread_id'],'old_thread_unavailable':unavailable,
        'replacement_limit':1,'model_turn_limit':1,'expires_at':min(grant['expires_at'],time.time()+3600),
        'head':HEAD,'source_preimage':BEFORE_SHA,'docs_preimages':dict(DOCS),'registry_hash':grant['css_trial']['registry_hash'],
        'profile_argv':list(profile_arguments(TRIAL)),'config_sha256':pin['sha256'],'config_identity':pin['identity'],
        'auth':auth_check(),'parent_canary':{'sha256':sha(canary),'identity':identity},
        'v4_candidate':'ed8dbf24c09a4c1d41319f9e2fb906feb1abd0ca07143e97841189d13e5bf2dc',
        'v4_evidence':'660ef4e07ce1a9492f938f69f930ca02c41cff2267687b629db9a8794617f3d8',
        'capability_requirement':'unscoped strict null metadata + loaded thread six disabled; all14featuresfalse; remote/browser/web/memoryoff',
        'closed_owner_receipt_hash':digest(fence)}
    authority['recovery_config_pin']={**pin,'original_grant_identity':grant['css_trial']['config_identity'],'observed_at':time.time(),
        'contract':EXECUTION_CONTRACT,'scope':'exact task replacement; original grant and intent unchanged'}
    receipt=store.authorize_css_replacement(TASK,authority)
    (HUB_ROOT/PRIVATE/'recovery-authority-v5.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt


def placeholder_snapshot():
    from .task_service import fingerprint
    rows=fingerprint(TRIAL);dirs=[row[0]for row in rows if row[1]=='directory']
    if dirs and set(dirs)!=set(PLACEHOLDERS):raise ServiceError('CSS_NATIVE_PLACEHOLDER_UNATTRIBUTED')
    if any(row[0]!='progress_ui.css'and row[0]not in PLACEHOLDERS for row in rows):
        raise ServiceError('CSS_PRESERVE_SCOPE_CHANGED')
    mounts={line.split()[4]for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    identities={}
    for name in dirs:
        path=TRIAL/name;info=path.lstat()
        if not stat.S_ISDIR(info.st_mode)or info.st_uid!=os.getuid()or path.is_symlink()or list(path.iterdir())or str(path)in mounts:
            raise ServiceError('CSS_NATIVE_PLACEHOLDER_UNSAFE')
        identities[name]=[info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode)]
    return {'fingerprint':rows,'directories':identities,'host_mounts':False}


def replacement_adapter(store, task, owner):
    """Existing adapter, with exact recovery gates; no second execution engine."""
    from .codex_adapter import AppServerAdapter,PERMISSION_PROFILE
    import importlib.util
    class ReplacementAdapter(AppServerAdapter):
        def call(self,method,params,timeout=15):
            if getattr(self,'css_owned_resume',False):
                number=getattr(self,'css_rpc_count',0)+1;self.css_rpc_count=number
                suffix='v7'if getattr(self,'css_process_config',False)else'v6'
                store.event(TASK,'css-rpc-'+suffix+'-'+owner+'-'+str(number),{'method':method,
                    'thread_id':params.get('threadId'),'turn_id':params.get('turnId')})
            return super().call(method,params,timeout)

        def _configuration_hash(self):
            a=runtime_authority(store)
            if a['contract'].endswith('v7-PROCESS-CONFIG'):
                from . import css_process_config as p
                return p.verify(a['protected_config_pin'])['_private_content_hash']
            return a['recovery_config_pin']['sha256']

        def _turn_overrides(self):
            if getattr(self,'css_process_config',False):
                from . import css_process_config as p
                return {'model':p.MODEL,'effort':p.EFFORT}
            return {}

        def open(self):
            authority=runtime_authority(store);source_check(task['intent']['grant'])
            self.css_process_config=authority['contract'].endswith('v7-PROCESS-CONFIG')
            self.css_owned_resume=authority['contract']==RESUME_CONTRACT or self.css_process_config
            if self.css_process_config:
                from . import css_process_config as p
                self.command=(*self.command,*p.OVERRIDES)
            self.css_before=placeholder_snapshot()
            if self.css_before['directories']and not self.css_owned_resume:raise ServiceError('CSS_TRIAL_CONTENT_REJECTED')
            super().open()
            try:
                spec=importlib.util.spec_from_file_location('hub_fixed_css_diagnostic',HUB_ROOT/'scripts/diagnose_css_trial.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                suffix='v7'if self.css_process_config else'v6'if self.css_owned_resume else'v5'
                if self.css_process_config:
                    store.event(TASK,'css-effective-process-before-resume-v7',p.configuration_fact(self.call('config/read',{'includeLayers':False})))
                store.event(TASK,'css-process-capabilities-'+suffix,module.DiagnosticAdapter.capabilities(self))
                store.event(TASK,'css-process-preflight-'+suffix,preflight(self,task['intent']['grant'],process_only=True))
                if not self.css_owned_resume:
                    try:self.call('thread/read',{'threadId':authority['old_thread_id'],'includeTurns':False})
                    except ServiceError as error:
                        if error.code!='CODEX_RPC_REJECTED'or 'thread_unavailable'not in error.details.get('error_categories',[]):raise
                    else:raise ServiceError('CSS_OLD_THREAD_OUTCOME_UNKNOWN')
                return self
            except BaseException:self.close();raise

        def thread(self, *, previous=None):
            if self.css_owned_resume:
                authority=runtime_authority(store);key='css-owned-load-resume-v7'if self.css_process_config else'css-owned-load-resume-v6'
                if previous!=authority['thread_id']or any(e['key']==key for e in store.events(TASK)):
                    raise ServiceError('CSS_OWNED_RESUME_ALREADY_CONSUMED')
                store.event(TASK,key,{'thread_id':previous,'excludeTurns':True,'model_turns':0})
                extra={}
                if self.css_process_config:
                    from . import css_process_config as p
                    extra={'model':p.MODEL,'config':{'model_reasoning_effort':p.EFFORT}}
                result=self.call('thread/resume',{'threadId':previous,'cwd':str(TRIAL),'excludeTurns':True,
                    'permissions':PERMISSION_PROFILE,'approvalPolicy':'on-request',**extra})
                if result['thread']['id']!=previous or result['thread']['cwd']!=str(TRIAL):
                    raise ServiceError('CSS_THREAD_BINDING_CHANGED',outcome='UNKNOWN')
                self._profile_echo(result)
                if self.css_process_config:
                    if result['model']!=p.MODEL or result['reasoningEffort']!=p.EFFORT:raise ServiceError('CSS_PROCESS_RESUME_MODEL_UNVERIFIED')
                    store.event(TASK,'css-effective-process-after-resume-v7',{'model':result['model'],'reasoning_effort':result['reasoningEffort'],'source':'native thread/resume response'})
                self.css_placeholder_proof=next(e['data']for e in store.events(TASK)if e['key']=='css-placeholder-thread-start-v5')
                store.event(TASK,'css-owned-load-idle-v7'if self.css_process_config else'css-owned-load-idle-v6',observe_thread(self,previous))
                return previous
            if previous is not None or any(e['key']=='css-replacement-thread-start-intent'for e in store.events(TASK)):
                raise ServiceError('CSS_REPLACEMENT_ALREADY_CONSUMED')
            recovery_authority(store);source_check(task['intent']['grant'])
            before=placeholder_snapshot()
            if before!=self.css_before:raise ServiceError('CSS_PRETHREAD_CONTENT_CHANGED')
            store.event(TASK,'css-replacement-thread-start-intent',{'contract':EXECUTION_CONTRACT,'before':before,'model_turns':0})
            result=self.call('thread/start',{'cwd':str(TRIAL),'permissions':PERMISSION_PROFILE,'approvalPolicy':'on-request'})
            thread=result['thread'];store.update(TASK,owner,thread_id=thread['id'])
            if Path(thread['cwd'])!=TRIAL:raise ServiceError('CSS_THREAD_BINDING_CHANGED',outcome='UNKNOWN')
            self._profile_echo(result)
            self.css_placeholder_proof={'source':'exact owned native thread/start before and after','thread_id':thread['id'],
                'before':before,'after_start':placeholder_snapshot(),'profile':PERMISSION_PROFILE}
            store.event(TASK,'css-placeholder-thread-start-v5',self.css_placeholder_proof)
            return thread['id']

        def verify_capabilities(self,thread_id):
            evidence=super().verify_capabilities(thread_id)
            rows=self.call('mcpServerStatus/list',{'threadId':thread_id,'limit':100,'detail':'toolsAndAuthOnly'})
            from .codex_adapter import REGISTERED_SERVERS
            if rows['nextCursor']is not None or sorted(r['name']for r in rows['data'])!=sorted(REGISTERED_SERVERS)or \
                any(r['runtimeStatus']!='disabled'or r['tools']or r['resources']or r['resourceTemplates']or r['serverCapabilities']is not None for r in rows['data']):
                raise ServiceError('CSS_LOADED_MCP_UNVERIFIED')
            evidence['loaded_mcp']=[{'name':r['name'],'runtime_status':r['runtimeStatus']}for r in rows['data']]
            snapshot=placeholder_snapshot()
            if snapshot['directories']!=self.css_placeholder_proof['after_start']['directories']:raise ServiceError('CSS_NATIVE_PLACEHOLDER_CHANGED')
            suffix='v7'if self.css_process_config else'v6'if self.css_owned_resume else'v5'
            store.event(TASK,'css-placeholder-after-model-'+suffix if getattr(self,'model_started',False)else'css-placeholder-loaded-'+suffix,snapshot)
            return evidence

        def start(self,thread_id,text,image=None):
            runtime_authority(store);snapshot=placeholder_snapshot()
            if snapshot!=self.css_placeholder_proof['after_start']or text!=prompt(task['intent']['annotation']):
                raise ServiceError('CSS_PRETURN_CONTENT_CHANGED')
            if any(e['key']=='css-unique-model-turn-v5'for e in store.events(TASK)):
                raise ServiceError('CSS_MODEL_TURN_ALREADY_CONSUMED')
            store.event(TASK,'css-placeholder-before-turn-v5',snapshot)
            store.event(TASK,'css-unique-model-turn-v5',{'thread_id':thread_id,'input_sha256':sha(text.encode()),'model_turn_limit':1})
            self.model_started=True
            self.css_turn_id=super().start(thread_id,text,image)
            return self.css_turn_id
    adapter=ReplacementAdapter(TRIAL)
    adapter.capability_recorder=lambda number,fact:store.event(TASK,'css-capability-observation-'+owner+'-'+str(number),fact)
    return adapter


def trial_result(grant, *, native_placeholder_proof=None):
    from .task_service import fingerprint
    before,_=read_file(HUB_ROOT/grant['css_trial']['before_ref'])
    after,_=read_file(TRIAL/'progress_ui.css',mode=0o644)
    actual=fingerprint(TRIAL)
    if native_placeholder_proof is not None:
        snapshot=placeholder_snapshot();expected=native_placeholder_proof['after_start']['directories']
        if native_placeholder_proof['before']['directories']or snapshot['directories']and snapshot['directories']!=expected:
            raise ServiceError('CSS_NATIVE_PLACEHOLDER_CHANGED')
        actual=[row for row in actual if row[0]not in snapshot['directories']]
    if actual!=[['progress_ui.css',AFTER_SHA]] or after!=expected_css(before):
        raise ServiceError('CSS_PRESERVE_SCOPE_CHANGED',outcome='UNKNOWN')
    diff=''.join(difflib.unified_diff(before.decode().splitlines(True),after.decode().splitlines(True),
        fromfile='before/progress_ui.css',tofile='after/progress_ui.css'))
    return {'checks':[{'id':'isolated_css_exact_delta','exit':0,'stdout':'Only .home-task-card border alpha0.15→0.24; all other bytes/mode unchanged'}],
        'changed_paths':['progress_ui.css'],'diff':diff,'before_sha256':BEFORE_SHA,'after_sha256':AFTER_SHA,
        'trial_ready':True,'source_promoted':False,'validation_scope':'isolated_css_only',
        'project':'computer-study-plan','canonical_head':HEAD,'human_acceptance':'pending',
        'native_placeholder_proof':native_placeholder_proof}


def promote(store, task, *, writer_check):
    """Root-only operation; no HTTP route, no execution of model-supplied paths."""
    current=store.task(task['id']);grant=current['intent']['grant']
    from .task_service import validate_grant
    validate_grant(grant)
    if (current['status']!='validating' or current['owner']is not None or current['cancel'] or
        current['provider_status']!='completed' or not current['thread_id'] or not current['turn_id'] or
        current['result']!=task['result'] or not current['result'].get('trial_ready')):
        raise ServiceError('CSS_PROMOTION_TASK_REJECTED',status=409)
    if store.grant(grant['grant_id'])!=grant or digest(current['intent']['annotation'])!=grant['annotation_hash']:
        raise ServiceError('CSS_PROMOTION_GRANT_STALE',status=409)
    source_check(grant)
    if not writer_check(grant):raise ServiceError('EXTERNAL_WRITER_UNKNOWN',status=409)
    result=trial_result(grant,native_placeholder_proof=current['result'].get('native_placeholder_proof'));before,identity=read_file(ROOT/'progress_ui.css',mode=0o644)
    after,_=read_file(TRIAL/'progress_ui.css',mode=0o644)
    if identity!=grant['css_trial']['source_identity']:raise ServiceError('CSS_SOURCE_VERSION_STALE',status=409)
    if any(e['key']=='css-promotion-intent'for e in store.events(task['id'])):
        raise ServiceError('CSS_PROMOTION_REQUIRES_RECONCILE',status=409)
    store.event(task['id'],'css-promotion-intent',{'before_sha256':sha(before),'after_sha256':sha(after),
        'thread_id':current['thread_id'],'turn_id':current['turn_id'],'authority':'direct_human_authorization_to_trusted_local_owner'})
    archive=HUB_ROOT/PRIVATE/'after.css'
    try:
        with archive.open('xb')as stream:stream.write(after);stream.flush();os.fsync(stream.fileno())
    except FileExistsError:
        if read_file(archive)[0]!=after:raise ServiceError('CSS_ARCHIVE_CONFLICT',status=409)
    def effect():
        atomic_replace(ROOT/'progress_ui.css',before,identity,after)
        return {'after_sha256':sha(read_file(ROOT/'progress_ui.css',mode=0o644)[0]),'source_effects':1}
    store.perform_local_promotion(task['id'],digest(current['result']),effect)
    return {**result,'source_promoted':True,'validation_scope':'canonical_checks_pending'}


def atomic_replace(path, before, identity, after):
    """Exact preimage/identity CAS; never restore or overwrite a changed file."""
    path=Path(path);parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    name='.'+path.name+'.hub-css-trial.tmp';fd=None;created=False
    try:
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=parent)
        created=True
        with os.fdopen(fd,'wb')as stream:
            fd=None;stream.write(after);stream.flush();os.fchmod(stream.fileno(),0o644);os.fsync(stream.fileno())
        now,current=read_file(path,mode=0o644)
        if now!=before or current!=identity:raise ServiceError('CSS_CAS_DRIFT',status=409)
        os.replace(name,path.name,src_dir_fd=parent,dst_dir_fd=parent);os.fsync(parent)
    finally:
        if fd is not None:os.close(fd)
        if created:
            try:os.unlink(name,dir_fd=parent)
            except FileNotFoundError:pass
        os.close(parent)
