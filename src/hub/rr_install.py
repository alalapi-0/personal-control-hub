"""Prepare/check an exact native package; installation effects are explicit."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .rr_binding import ORIGIN, KEY_PROVIDER, verify_program, private_json, denied
from .rr_payload import canonical_json
from .task_store import TaskStore

SERVICE='personal-control-hub.service'
DROPIN='70-pch-rr.conf'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_private(path, value):
    raw=canonical_json(value) if not isinstance(value,bytes) else value
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def process_identity(pid):
    path=Path('/proc')/str(pid)
    raw=(path/'stat').read_text().rsplit(')',1)[1].split()
    return {'pid':pid,'start_ticks':int(raw[19]),'cmdline_sha256':hashlib.sha256((path/'cmdline').read_bytes()).hexdigest()}


def service_pid():
    result=subprocess.run(['systemctl','--user','show',SERVICE,'--property=MainPID','--value'],capture_output=True,text=True,timeout=5,check=True)
    return int(result.stdout.strip())


def prepare(code_root,data_root,output,*,python,project_ids,gateway_uid=None,gateway_gid=None,devices=None,current_service_pid=None):
    """Only writes the new candidate directory, never the live code/config/DB."""
    code_root=Path(code_root).absolute();data_root=Path(data_root).absolute();output=Path(output).absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():raise denied()
    store=TaskStore(data_root);store.check_storage(readonly=True)
    output.mkdir(mode=0o700);package=output/'code';package.mkdir(mode=0o700)
    try:
        names=[]
        for path in (code_root/'src').rglob('*'):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
                names.append(path.relative_to(code_root))
        names += [Path(x) for x in ('scripts/hub_server.py','scripts/pch_rr.py','scripts/pch_rr_install.py','project.yaml')]
        files={}
        for name in sorted(names):
            source=code_root/name
            if any(p.is_symlink() for p in (source,*source.parents)):raise denied()
            raw=source.read_bytes();target=package/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            target.write_bytes(raw);target.chmod(0o644)
            files[str(name)]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        program=hashlib.sha256(canonical_json(files)).hexdigest()
        commit=subprocess.check_output(['git','-C',str(code_root),'rev-parse','HEAD'],text=True).strip()
        dirty=bool(subprocess.check_output(['git','-C',str(code_root),'status','--porcelain','--untracked-files=normal']).strip())
        manifest={'schema':1,'program_id':program,'files':files,'source':{'commit':commit,'dirty':dirty}}
        write_private(package/'rr-runtime-manifest.json',manifest)
        destination=data_root/'.venv/pch-refresh/versions'/program/'code'
        binding_path=data_root/'data/rr-adapter.json'
        binding={'schema':2,'project_id':'personal-control-hub','program_id':program,'code_root':str(destination),
                 'data_root':str(data_root),'browser_enabled':False,'agent_uid':os.getuid(),'gateway_uid':gateway_uid,
                 'gateway_gid':gateway_gid,'origin':ORIGIN,'devices':devices or {},'review_project_ids':project_ids,
                 'ledger_identity':[store.path.stat().st_dev,store.path.stat().st_ino],
                 'storage_registration_sha256':sha(store.registration_path),'key_provider_socket':str(KEY_PROVIDER)}
        write_private(package/'rr-installation.json',{'schema':1,'project_id':'personal-control-hub','program_id':program,
                      'code_root':str(destination),'data_root':str(data_root),'binding_path':str(binding_path)})
        write_private(output/'binding.json',binding)
        dropin=(Path.home()/'.config/systemd/user'/ (SERVICE+'.d')/DROPIN)
        if dropin.is_symlink():raise denied()
        before=dropin.read_bytes() if dropin.exists() else None
        if before is not None:write_private(output/'dropin.before',before)
        command=[str(Path(python).absolute()),str(destination/'scripts/hub_server.py'),'--root',str(data_root),
                 '--host','127.0.0.1','--port','8766','--owner-auth','systemd-credential','--rr-binding',str(binding_path)]
        if any(any(c in arg for c in '\n\r%$"\\') for arg in command):raise denied()
        unit=('[Service]\nExecStart=\nExecStart='+ ' '.join('"'+x+'"' for x in command)+'\n').encode()
        write_private(output/'dropin.after',unit)
        plan={'schema':1,'program_id':program,'package':str(package),'destination':str(destination),'data_root':str(data_root),
              'binding_path':str(binding_path),'dropin_path':str(dropin),'dropin_before_sha256':hashlib.sha256(before).hexdigest()if before is not None else None,
              'dropin_after_sha256':sha(output/'dropin.after'),'binding_sha256':sha(output/'binding.json'),
              'source':manifest['source'],'service':SERVICE,'baseline_process':process_identity(current_service_pid if current_service_pid is not None else service_pid()),
              'lifecycle':'New normal Web with optional sameprocess RR; ordinary executor OFF. Never replay finite-window grants. Rollback config is not restoration of priorPID.',
              'missing':([k for k,v in [('gateway_uid',gateway_uid),('gateway_gid',gateway_gid)] if v is None])+([] if devices else ['devices']),
              'owner_provider':'Preserve existing service credential directives; key received by anonymous sealed FD only.'}
        write_private(output/'plan.json',plan)
        return plan
    except BaseException:
        shutil.rmtree(output);raise


def check(candidate,*,require_ready=False):
    candidate=Path(candidate).absolute();plan=private_json(candidate/'plan.json')
    source=verify_program(plan['package'],plan['program_id'])
    if source != plan['source'] or sha(candidate/'binding.json')!=plan['binding_sha256'] or sha(candidate/'dropin.after')!=plan['dropin_after_sha256']:
        raise denied()
    binding=private_json(candidate/'binding.json')
    expected_destination=Path(plan['data_root'])/'.venv/pch-refresh/versions'/plan['program_id']/'code'
    if (plan['package']!=str(candidate/'code') or plan['destination']!=str(expected_destination)
            or plan['binding_path']!=str(Path(plan['data_root'])/'data/rr-adapter.json')
            or plan['dropin_path']!=str(Path.home()/'.config/systemd/user'/(SERVICE+'.d')/DROPIN)
            or binding['code_root']!=plan['destination'] or binding['data_root']!=plan['data_root']
            or binding['program_id']!=plan['program_id'] or binding['browser_enabled'] is not False):
        raise denied()
    store=TaskStore(plan['data_root']);store.check_storage(readonly=True)
    if (binding['ledger_identity'] != [store.path.stat().st_dev,store.path.stat().st_ino]
            or binding['storage_registration_sha256'] != sha(store.registration_path)):
        raise denied()
    check_installation(plan, plan['package'])
    if require_ready:
        from .rr_binding import browser_binding
        browser_binding({**binding,'browser_enabled':True})
        if plan['missing'] or not KEY_PROVIDER.exists():raise denied()
    return plan


def check_installation(plan, code_root):
    expected={'schema':1,'project_id':'personal-control-hub','program_id':plan['program_id'],
              'code_root':plan['destination'],'data_root':plan['data_root'],'binding_path':plan['binding_path']}
    if private_json(Path(code_root)/'rr-installation.json') != expected:
        raise denied()


def install(candidate):
    plan=check(candidate);destination=Path(plan['destination'])
    if destination.exists() or destination.is_symlink():raise denied()
    parent=destination.parent
    if parent.exists():raise denied()
    parent.mkdir(mode=0o700)
    try:
        shutil.copytree(plan['package'],destination)
        verify_program(destination,plan['program_id'])
        check_installation(plan,destination)
    except BaseException:
        shutil.rmtree(parent);raise
    return {'status':'installed_disabled','program_id':plan['program_id']}


def _service(action):
    result=subprocess.run(['systemctl','--user',action,SERVICE] if action!='daemon-reload' else ['systemctl','--user',action],
                          capture_output=True,timeout=30,check=False)
    if result.returncode:raise denied()


def enable(candidate):
    plan=check(candidate,require_ready=True);candidate=Path(candidate)
    if process_identity(plan['baseline_process']['pid'])!=plan['baseline_process']:raise denied()
    code=Path(plan['destination']);verify_program(code,plan['program_id']);check_installation(plan,code)
    path=Path(plan['dropin_path']);current=sha(path)if path.exists()else None
    if path.is_symlink() or current!=plan['dropin_before_sha256']:raise denied()
    binding_path=Path(plan['binding_path'])
    if binding_path.exists() or binding_path.is_symlink():raise denied()
    binding=private_json(candidate/'binding.json');binding['browser_enabled']=True
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    from .rr_cli import _directories
    _directories(path.parent);_directories(binding_path.parent)
    temporary=path.with_name('.rr-enable')
    write_private(temporary,candidate.joinpath('dropin.after').read_bytes())
    try:
        if (sha(path) if path.exists() else None)!=plan['dropin_before_sha256']:raise denied()
        write_private(binding_path,binding)
        try:
            if plan['dropin_before_sha256'] is None:
                os.link(temporary,path);temporary.unlink()
            else:os.replace(temporary,path)
        except BaseException:
            # Restore only the newly created exact binding preimage. A changed
            # object belongs to reconciliation, never an unconditional unlink.
            if private_json(binding_path)==binding:binding_path.unlink()
            raise
    finally:
        temporary.unlink(missing_ok=True)
    _service('daemon-reload');_service('restart')
    return {'status':'activation_requested','program_id':plan['program_id'],'requires':'Verify exact newPID/read-only UI/FD/Owner/data before adoption PASS.'}


def rollback(candidate):
    plan=check(candidate);candidate=Path(candidate);path=Path(plan['dropin_path']);binding_path=Path(plan['binding_path'])
    if path.is_symlink() or not path.is_file() or sha(path)!=plan['dropin_after_sha256']:raise denied()
    binding=private_json(binding_path)
    expected=private_json(candidate/'binding.json');expected['browser_enabled']=True
    if binding!=expected:raise denied()
    if plan['dropin_before_sha256'] is not None and sha(candidate/'dropin.before')!=plan['dropin_before_sha256']:raise denied()
    binding['browser_enabled']=False
    temporary=binding_path.with_name('.rr-disable')
    write_private(temporary,binding);os.replace(temporary,binding_path)
    if plan['dropin_before_sha256'] is None:path.unlink()
    else:
        temporary=path.with_name('.rr-rollback');write_private(temporary,candidate.joinpath('dropin.before').read_bytes());os.replace(temporary,path)
    _service('daemon-reload');_service('restart')
    return {'status':'rollback_requested','agent_binding':'retained for sameDB offline read/ack/result',
            'data':'RR feedback/attachments preserved; no schema rollback or grant replay',
            'requires':'Verify exact runtime and closed RRsocket; service command success is not adoption proof.'}
