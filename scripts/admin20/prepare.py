#!/usr/bin/python3
"""Prepare one fixed boot-bound 20h bundle. Never authenticate or run root code."""
import ast
import hashlib
import io
import json
import os
import pathlib
import shlex
import stat
import tarfile
import uuid

HERE = pathlib.Path(__file__).resolve().parent
OLD = pathlib.Path('/var/lib/linux-admin-window/cursor20-20261008-v1')
ROOT = pathlib.Path('/var/lib/linux-admin-window/codex20-20261008-v1')
SOCKET_DIR = pathlib.Path('/run/linux-admin-window-codex20-20261008-v1')
LOCAL = pathlib.Path('/home/alalapi/Temp/personal-control-hub/media-apps-storage-20261007')
OUTPUT = LOCAL / 'admin20-inputs'
OLD_HASHES = {
 'server.py':'f5d02aba7a13036841783503ba21587c0935886afd99e944d1ca2153732a64e6',
 'client.py':'da352cf603ae08a547eb82ccd15b70853dfb862e6e9ae29732b302a61501d47f',
 'manifest.json':'a7df750fb5f0a09b8e12075a0f7ba5352e263258b1d8031c07a15f9a17d1dd73',
}
EXTRA = ('lan.packages.state','lan.packages.install','lan.proof.encrypt',
 'lan.proof.decrypt','lan.proof.wrong-name','lan.proof.expired','lan.proof.project',
 'lan.credential.seal','lan.credential.get','creator.runtime.state','creator.runtime.install')
FIXED_INPUTS = {
 'libtss2-fapi1t64_4.1.3-6_amd64.deb':(LOCAL/'libtss2-fapi1t64_4.1.3-6_amd64.deb','6d07a787a8afca1a82c702363ef1b717dbf6a8897f34cbdceb71c336521ba96b'),
 'tpm2-tools_5.7-1build1_amd64.deb':(LOCAL/'tpm2-tools_5.7-1build1_amd64.deb','00b508bd0adb28cadc445ce36e2439484ae910388d412e8ac4582dd95fe1fa24'),
 'electron-v44.6.0-linux-x64.zip':(pathlib.Path('/home/alalapi/.cache/electron/8ed3ed5a704bf2ea4aac1ece9f050d2f11422ac8430103e9efbf921b4ea38d8c/electron-v44.6.0-linux-x64.zip'),'20cafbe96e0ab8ad95cd31804acd683142b361f9e25f1595aecf67177d0a8915'),
}

def sha(data): return hashlib.sha256(data).hexdigest()

def protected(path):
 for p in (path,*path.parents):
  s=p.lstat()
  if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
   raise ValueError('UNTRUSTED_ROOT_PREIMAGE:'+str(p))
 if not path.is_file(): raise ValueError('NOT_REGULAR_FIXED_FILE')
 return path.read_bytes()

def once(text,old,new):
 if text.count(old)!=1: raise ValueError('PARENT_BACKEND_STRUCTURE_CHANGED:'+old[:70])
 return text.replace(old,new,1)

def compose_server(source):
 source=source.replace(str(OLD),str(ROOT)).replace('/run/linux-admin-window-cursor20-20261008-v1',str(SOCKET_DIR))
 source=once(source,'import time\n','import time\nimport sys\nimport importlib.util\n')
 line=next(x for x in source.splitlines() if x.startswith('OPS = '))
 old_ops=ast.literal_eval(line.split('=',1)[1].strip())
 allowed=tuple(x for x in old_ops if not x.startswith('service.'))+EXTRA
 source=once(source,line,'OPS = '+repr(allowed)+'\nscoped_ops = None')
 addition="""
_base_gate = gate
def gate(state, uid, request, now_epoch, now_boot, boot, machine, username):
    if isinstance(request, dict) and request.get('op') in EXTRA_OPS:
        reason = _base_gate(state, uid, {'op': 'probe'}, now_epoch, now_boot, boot, machine, username)
        return reason or scoped_ops.argument_gate(request)
    return _base_gate(state, uid, request, now_epoch, now_boot, boot, machine, username)
""".replace('EXTRA_OPS',repr(EXTRA))
 source=once(source,'\ndef repair_resume(',addition+'\ndef repair_resume(')
 source=once(source,"    op = request['op']\n    if op == 'status'","    op = request['op']\n    if op in "+repr(EXTRA)+":\n        return scoped_ops.operate(sys.modules[__name__], state, manifest, request)\n    if op == 'status'")
 begin=source.index("    if (ROOT / 'state.json').exists():\n",source.index('def main():'))
 end=source.index('    save(state)\n',begin)
 source=source[:begin]+"    if (ROOT / 'state.json').exists():\n        raise ValueError('USED_WINDOW_CANNOT_RESTART_OR_RENEW')\n"+source[end:]
 source=once(source,'    global _active, _in_transaction\n','    global _active, _in_transaction, scoped_ops\n')
 source=once(source,'    now, tick = time.time(), boottime()\n',"""    spec = importlib.util.spec_from_file_location('admin20_scoped_ops', ROOT / 'scoped_ops.py')
    scoped_ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(scoped_ops)
    code_check(manifest); hooks_check()
    now, tick = time.time(), boottime()
""")
 source=source.replace('len(data) <= 1024','len(data) <= 32768').replace('conn.recv(1025 - len(data))','conn.recv(32769 - len(data))').replace('len(data) > 1024','len(data) > 32768')
 source=once(source,"    if op.startswith('ufw.'): hooks_check()","    if op in ('firewall.allow', 'firewall.restore-last', 'linger.enable', 'linger.restore', 'data.mount') and min(state['expires_epoch'] - time.time(), state['expires_boottime'] - boottime()) < 600:\n        raise ValueError('INSUFFICIENT_WINDOW_FOR_BOUNDED_TRANSACTION')\n    if op.startswith('ufw.'): hooks_check()")
 # Subtract legacy system-service and repair-resume effects, rather than retaining dead writers.
 for first,last in [('UNIT = ','LINGER = '),('ENABLE_LINK = ','OWNER = '),('UNIT_TEXT = ','OPS = '),('def repair_resume(','def run('),('def unit_check(','def firewall_args(')]:
  a=source.index(first);b=source.index(last,a);source=source[:a]+source[b:]
 a=source.index("    if op == 'service.install':");b=source.index("    raise ValueError('OUTSIDE_SCOPE')",a)
 source=source[:a]+source[b:]
 source=source.replace("'unit_installed': False, 'unit_receipt': None","'creator_runtime_installed': None")
 source=source.replace("'lan-share-10h-01a1152a-'","'lan-share-admin20-'")
 compile(source,'sealed-server.py','exec')
 return source

def compose_client(source):
 source=source.replace(str(OLD),str(ROOT)).replace('/run/linux-admin-window-cursor20-20261008-v1',str(SOCKET_DIR))
 source=source.replace('sock.settimeout(120)','sock.settimeout(240)')
 source=once(source,"    value = {'op': sys.argv[1]}\n","    if sys.argv[1].startswith('lan.credential.'):\n        raise ValueError('CREDENTIALS_REQUIRE_LOCAL_IN_MEMORY_APPLICATION_SDK_NO_CLI')\n    value = {'op': sys.argv[1]}\n")
 source+='''\n
def credential_request(name, approval_reference, secret=None):
    """Local application API only; never print/store the request or response."""
    import base64
    if name not in ('lan-share-ca-key', 'lan-share-server-key'):
        raise ValueError('OUTSIDE_CREDENTIAL_NAME')
    value = {'op': 'lan.credential.get' if secret is None else 'lan.credential.seal',
             'name': name, 'approval_reference': approval_reference}
    if secret is not None:
        value['secret_b64'] = base64.b64encode(secret).decode()
    result = request(value)
    if result.get('status') != 'OK':
        raise ValueError('CREDENTIAL_CAPABILITY_DENIED')
    return base64.b64decode(result['secret_b64'], validate=True) if secret is None else result['encrypted_store']
'''
 compile(source,'sealed-client.py','exec');return source

def prepare():
 if os.getuid()!=1000 or os.geteuid()==0: raise ValueError('PREPARE_AS_ORDINARY_OWNER_ONLY')
 if ROOT.exists() or ROOT.is_symlink(): raise ValueError('EXISTING_NEW_NAMESPACE_PRESERVED')
 OUTPUT.mkdir(mode=0o700,exist_ok=True)
 if (OUTPUT/'activate.sh').exists(): raise ValueError('PREPARED_WINDOW_ALREADY_EXISTS_NO_SILENT_REPLAN')
 originals={name:protected(OLD/name) for name in OLD_HASHES}
 if any(sha(originals[name])!=expected for name,expected in OLD_HASHES.items()): raise ValueError('PARENT_BACKEND_IDENTITY_CHANGED')
 boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 manifest=json.loads(originals['manifest.json']);window='codex-shared-admin20-'+str(uuid.uuid4())
 manifest.update(version=3,contract='CODEX-LAN-CREATOR-SHARED-ADMIN-20H-v1',boot_id=boot,current_window_id=window,
  shared_client=str(ROOT/'client.py'),task_scope=['LAN File Share','creator_workbench','media_apps_storage_goal'],assessment_threads=[],
  authorization='Human requests a new20hour shared window, principally LAN File Share and creator_workbench. DIRECT Root preparation, no roles. Activation has no package/network/application effects. Original operation-specific owner authority and acceptance gates still apply; no arbitrary future root commands.')
 manifest['home']['user_confirmation_required_before_arm']='Original LAN effect owner must retain exact approved home trust, preimage, candidate and effect review; authentication alone does not authorize firewall effects.'
 manifest['packages']=[{'name':'libtss2-fapi1t64' if name.startswith('libtss2-') else 'tpm2-tools','version':'4.1.3-6' if name.startswith('libtss2-') else '5.7-1build1','file':name,'sha256':v[1]} for name,v in FIXED_INPUTS.items() if name.endswith('.deb')]
 manifest['creator']={'runtime_root':'/opt/creator-workbench-electron-44.6.0','archive':'electron-v44.6.0-linux-x64.zip','sha256':FIXED_INPUTS['electron-v44.6.0-linux-x64.zip'][1],
  'sandbox_sha256':'a4f6dfd7325ddd55f94ddc0c487d22726d40ef099ded6475d5dffe236e277896','official_checksum_source':'https://github.com/electron/electron/releases/download/v44.6.0/SHASUMS256.txt'}
 manifest['credential_scope']={'names':['lan-share-ca-key','lan-share-server-key'],'provider':'TPM2 only, no host/auto/null fallback; five nonsecret qualification results required',
  'transport':'Local in-memory application SDK only; CLI blocks credential operations','production':'Original LAN source/credential acceptance and effect authority remain; no identity creation/rotation or deployment at activation'}
 manifest['persistence']='alalapi linger only; ordinary owner manages user lan-file-share.service; system service operations excluded'
 manifest['uncovered']=['Manual phone/Mac trust, account login, extension load and creative decisions','Unknown packages/runtimes/credential names/rotation/deletion, arbitrary commands/paths/system units',
  'Global DNS/VPN/routes, public deployment/provider accounts, reboot/logout/format/deletion','Paused/unapproved business effects; activation does not resume them','Nonempty/untrusted unmounted DATA target or wrong disk']
 for name in ['/usr/bin/dpkg','/usr/bin/dpkg-query','/usr/bin/systemd-creds']:
  manifest['protected_code'][name]=sha(protected(pathlib.Path(name)))
 for name,expected in manifest['protected_code'].items():
  if sha(protected(pathlib.Path(name)))!=expected: raise ValueError('PARENT_SYSTEM_CODE_CHANGED:'+name)
 for name,(path,expected) in FIXED_INPUTS.items():
  if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=expected: raise ValueError('FIXED_INPUT_CHANGED:'+name)
 payloads={'server.py':compose_server(originals['server.py'].decode()).encode(),'client.py':compose_client(originals['client.py'].decode()).encode(),
  'scoped_ops.py':(HERE/'scoped_ops.py').read_bytes(),'manifest.json':(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()}
 bundle=OUTPUT/'payload.tar'
 with tarfile.open(bundle,'w') as t:
  for name,data in payloads.items():
   item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o444;t.addfile(item,io.BytesIO(data))
 os.chmod(bundle,0o600)
 cfg={'root':str(ROOT),'socket_dir':str(SOCKET_DIR),'window_id':window,'unit':'codex-admin20-'+window.rsplit('-',1)[-1],
  'boot_id':boot,'machine_id':manifest['machine_id'],'hostname':manifest['hostname'],'old_hooks':str(OLD/'hooks.json'),
  'bundle':str(bundle),'bundle_sha256':sha(bundle.read_bytes()),'payloads':{k:sha(v) for k,v in payloads.items()},
  'inputs':{k:{'path':str(v[0]),'sha256':v[1]} for k,v in FIXED_INPUTS.items()}}
 bootstrap=(HERE/'bootstrap.py').read_text().replace('CONFIG = None','CONFIG = '+repr(cfg));compile(bootstrap,'fixed-root-bootstrap','exec')
 activation='#!/bin/bash\nset -euo pipefail\nexec /usr/bin/sudo -- /usr/bin/python3.14 -I -c '+shlex.quote(bootstrap)+'\n'
 (OUTPUT/'activate.sh').write_text(activation);os.chmod(OUTPUT/'activate.sh',0o700)
 (OUTPUT/'prepared.json').write_text(json.dumps(cfg,sort_keys=True,indent=2)+'\n');os.chmod(OUTPUT/'prepared.json',0o600)
 for name in ['server.py','client.py','manifest.json']:
  (OUTPUT/name).write_bytes(payloads[name]);os.chmod(OUTPUT/name,0o600)
 print(json.dumps({'status':'PREPARED_NOT_AUTHENTICATED','hours':20,'window_id':window,'activate':['/usr/bin/bash',str(OUTPUT/'activate.sh')],
  'client':str(ROOT/'client.py'),'bundle_sha256':cfg['bundle_sha256']},sort_keys=True))

if __name__=='__main__': prepare()
