"""No root effects: exercise the exact generated gate and unsafe input boundaries."""
import copy
import importlib.util
import io
import pathlib
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module); return module

prepare = load('window_prepare', HERE / 'prepare.py')
scoped = load('window_scoped', HERE / 'scoped_ops.py')
bootstrap = load('window_bootstrap', HERE / 'bootstrap.py')
source = prepare.compose_server(prepare.protected(prepare.OLD / 'server.py').decode())
server = types.ModuleType('sealed_server_test');sys.modules[server.__name__] = server
exec(compile(source, 'sealed-server.py', 'exec'), server.__dict__)
server.scoped_ops = scoped
client_source = prepare.compose_client(prepare.protected(prepare.OLD / 'client.py').decode())
client = types.ModuleType('sealed_client_test');exec(compile(client_source,'sealed-client.py','exec'),client.__dict__)

class Boundary(unittest.TestCase):
    def setUp(self):
        self.state = {'started_epoch':100.,'expires_epoch':72100.,
                      'started_boottime':20.,'expires_boottime':72020.,
                      'boot_id':'current-boot','machine_id':'current-host',
                      'revoked':False,'closed':False}
    def gate(self,request=None,**change):
        values = {'state':self.state,'uid':1000,'request':request or {'op':'status'},
                  'now_epoch':101.,'now_boot':21.,'boot':'current-boot',
                  'machine':'current-host','username':'alalapi'}
        values.update(change);return server.gate(**values)
    def test_owner_boot_host_expiry_revoke(self):
        self.assertIsNone(self.gate())
        for changes,reason in [({'uid':1001},'WRONG_OWNER'),({'boot':'other'},'BOOT_CHANGED'),
          ({'machine':'other'},'HOST_CHANGED'),({'now_epoch':72100.},'EXPIRED'),
          ({'now_boot':72020.},'EXPIRED'),({'now_boot':19.},'CLOCK_ROLLBACK')]:
            self.assertEqual(self.gate(**changes),reason)
        for key,reason in [('revoked','REVOKED'),('closed','CLOSED')]:
            state=dict(self.state,**{key:True});self.assertEqual(self.gate(state=state),reason)
        self.assertEqual(self.gate(state=dict(self.state,expires_epoch=72101.)),'INVALID_DURATION')
    def test_all_extensions_have_same_time_and_identity_gate(self):
        for op in scoped.EXTRA_OPS:
            request={'op':op}
            if op.startswith('lan.credential.'):
                request.update(name='lan-share-ca-key',approval_reference='local-unit-proof')
            if op=='lan.credential.seal':request['secret_b64']='bWFya2Vy'
            self.assertIsNone(self.gate(request),op)
            self.assertEqual(self.gate(request,uid=1001),'WRONG_OWNER',op)
            self.assertEqual(self.gate(request,now_boot=72020.),'EXPIRED',op)
            self.assertEqual(self.gate(dict(request,arbitrary_path='/tmp/other')),'EXTRA_OR_MISSING_ARGUMENT',op)
    def test_unknown_and_system_unit_effects_removed(self):
        for op in ['shell','service.install','service.restart','lan.credential.rotate']:
            self.assertEqual(self.gate({'op':op}),'OUTSIDE_SCOPE')
        self.assertNotIn('/etc/systemd/system/lan-file-share.service',source)
        self.assertNotIn('repair_resume',source)
        self.assertIn('USED_WINDOW_CANNOT_RESTART_OR_RENEW',source)
    def test_fixed_firewall_resource_and_hash_checks(self):
        request={'op':'firewall.candidate','channel':'home','port':9417,
                 'expected_preimage':'a'*64,'expected_hooks':'b'*64}
        self.assertIsNone(self.gate(request))
        self.assertEqual(self.gate(dict(request,port=22)),'OUTSIDE_RESOURCE')
        self.assertEqual(self.gate(dict(request,channel='public')),'OUTSIDE_RESOURCE')
        self.assertEqual(self.gate(dict(request,expected_preimage='invalid')),'INVALID_PREIMAGE')
    def test_cli_blocks_secret_before_parsing_and_never_echoes_it(self):
        for op in ['lan.credential.seal','lan.credential.get']:
            with patch.object(sys,'argv',['client.py',op,'private-test-marker']):
                with self.assertRaisesRegex(ValueError,'NO_CLI') as error: client.main()
                self.assertNotIn('private-test-marker',str(error.exception))
        self.assertEqual(scoped.argument_gate({'op':'lan.credential.get','name':'other',
                         'approval_reference':'valid'}),'OUTSIDE_CREDENTIAL_NAME')
    def test_bootstrap_rejects_tampering_alias_and_archive_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'fixed';path.write_bytes(b'known')
            self.assertEqual(bootstrap.userbytes(path,prepare.sha(b'known')),b'known')
            with self.assertRaisesRegex(ValueError,'HASH_CHANGED'):
                bootstrap.userbytes(path,prepare.sha(b'changed'))
            alias=path.parent/'alias';alias.symlink_to(path)
            with self.assertRaises(OSError):bootstrap.userbytes(alias,prepare.sha(b'known'))
        for name in ['../server.py','server.py']:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as archive:
                item=tarfile.TarInfo(name);item.size=5;archive.addfile(item,io.BytesIO(b'known'))
            if name.startswith('..'):
                with self.assertRaisesRegex(ValueError,'INVALID_BUNDLE_MEMBER'):
                    bootstrap.unpack(stream.getvalue(),{'server.py':prepare.sha(b'known')})
            else:
                self.assertEqual(bootstrap.unpack(stream.getvalue(),{'server.py':prepare.sha(b'known')}),{'server.py':b'known'})
    def test_root_authentication_is_required_before_effects(self):
        with patch.object(bootstrap.os,'geteuid',return_value=1000):
            with self.assertRaisesRegex(ValueError,'ROOT_AUTHENTICATION_REQUIRED'):bootstrap.main()

if __name__=='__main__':unittest.main()
