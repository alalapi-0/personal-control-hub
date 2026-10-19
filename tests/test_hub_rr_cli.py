"""Owner-only CLI, actual attachment inspection and no implicit initialization."""
import contextlib
import base64
import hashlib
import io
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.rr_cli import agent_action, local_context, main, gateway_group
from hub.rr_feedback import RRFeedbackError, RRFeedbackStore
from hub.task_store import TaskStore
from hub.rr_auth import GatewayAuth, canonical
from hub.rr_service import RRApplication, build_manifest


class RRCLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'hub'; self.root.mkdir(mode=0o700)
        for directory in ('src/hub', 'scripts', 'data'):
            (self.root / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.root / 'project.yaml').write_text('project:\n  id: personal-control-hub\n')
        (self.root / 'project.yaml').chmod(0o600)
        for name in ('scripts/pch_rr.py', 'src/hub/rr_cli.py'):
            (self.root / name).write_text('# synthetic installation identity\n')
            (self.root / name).chmod(0o600)
        self.config = {'schema': 1, 'enabled': True, 'project_id': 'personal-control-hub',
                       'agent_uid': os.getuid(), 'gateway_uid': os.getuid()+12345,
                       'gateway_gid': os.getgid()+12345, 'origin': 'https://pch.example.test',
                       'devices': {'a'*64: 'personal-control-hub'}}
        self.path = self.root / 'data/rr-adapter.json'
        self.task = TaskStore(self.root); self.task.register_storage()
        self.store = RRFeedbackStore(self.task)

    def write_config(self):
        self.path.write_text(json.dumps(self.config)); self.path.chmod(0o600)

    def test_disabled_missing_wrong_owner_and_shared_install_refuse_without_db_mutation(self):
        before = self.task.path.read_bytes()
        with self.assertRaises(RRFeedbackError): local_context(self.root)
        self.write_config(); local_context(self.root)
        for key, value in [('enabled', False), ('agent_uid', os.getuid()+1), ('gateway_uid', os.getuid())]:
            old=self.config[key]; self.config[key]=value; self.write_config()
            with self.assertRaises(RRFeedbackError): local_context(self.root)
            self.config[key]=old
        self.write_config(); self.root.chmod(0o755)
        with self.assertRaises(RRFeedbackError): local_context(self.root)
        self.root.chmod(0o700)
        self.assertEqual(before, self.task.path.read_bytes())

    def test_agent_read_actual_bytes_ack_result_and_repeat_read(self):
        raw = 'fixture review\n'.encode()
        doc = {'schema': 1, 'client_request_id': str(uuid.uuid4()), 'project_id': 'personal-control-hub',
               'page_path': '/', 'source': {'commit':'a'*40,'build_id':'fixture','dirty':False},
               'resource': {'id':'icon','sha256':'b'*64,'mode':'readonly','preview_version':'v1'},
               'description':'fixture only','expected':'expected','actual':'observed','steps':['read'],
               'submitted_at':'2026-10-19T00:00:00+00:00',
               'attachments':[{'attachment_id':'note','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}
        _, receipt = self.store.submit('device-a', doc, [{'attachment_id':'note','filename':'note.txt',
                                                        'content_type':'text/plain','data':raw}])
        ident=receipt['feedback_id']
        with self.assertRaises(RRFeedbackError): agent_action(self.root,self.store,'ack',feedback_id=ident,proof={})
        read=agent_action(self.root,self.store,'read',feedback_id=ident)
        self.assertEqual(Path(read['attachments'][0]['local_file']).read_bytes(),raw)
        self.assertEqual(read['document'],doc)
        again=agent_action(self.root,self.store,'read',feedback_id=ident)
        self.assertEqual(again['read_receipt'],read['read_receipt'])
        for path in (Path(read['proof_file']),Path(read['attachments'][0]['local_file'])):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        ack=agent_action(self.root,self.store,'ack',feedback_id=ident,proof=read['read_receipt'])
        result=agent_action(self.root,self.store,'result',feedback_id=ident,proof=read['read_receipt'],
                            summary='Inspected fixture bytes; no task execution.',references=[])
        self.assertEqual(result['ack_id'],ack['ack_id'])
        self.assertEqual(self.store.get('device-a',ident)['state'],'processed')
        self.assertEqual(self.task.tasks(),[])

    def test_symlink_cli_and_group_extra_member_rejected(self):
        self.write_config()
        source=self.root/'scripts/pch_rr.py'; source.unlink(); source.symlink_to(self.root/'project.yaml')
        with self.assertRaises(RRFeedbackError): local_context(self.root)
        owner=mock.Mock(pw_name='owner',pw_gid=os.getgid())
        gateway=mock.Mock(pw_name='gateway',pw_gid=self.config['gateway_gid'])
        group=mock.Mock(gr_gid=self.config['gateway_gid'],gr_mem=['gateway','unexpected'])
        with mock.patch('hub.rr_cli.grp.getgrgid',return_value=group), \
             mock.patch('hub.rr_cli.pwd.getpwuid',side_effect=[gateway,owner]), \
             mock.patch('hub.rr_cli.pwd.getpwall',return_value=[owner,gateway]):
            with self.assertRaises(RRFeedbackError): gateway_group(self.config)

    def test_no_cli_identity_or_database_override_and_missing_config_no_initialization(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(['pending','--root','untrusted'],installation_root=self.root)
        self.path.unlink(missing_ok=True)
        before=self.task.path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['pending'],installation_root=self.root),1)
        self.assertNotIn(str(self.root),error.getvalue())
        self.assertEqual(before,self.task.path.read_bytes())
        self.assertFalse((self.root/'data/rr-agent-reads').exists())

    def test_gateway_submission_and_recovery_use_same_authoritative_agent_bytes(self):
        repo=Path(__file__).resolve().parents[1]
        resources={'pch-icon':repo/'src/hub/web/icon.svg'}
        manifest=build_manifest(repo,resources)
        self.assertRegex(manifest['source']['commit'],r'^[0-9a-f]{40,64}$')
        self.assertIs(type(manifest['source']['dirty']),bool)
        key=b'synthetic integration fixture key'
        device='a'*64; origin='https://pch.example.test'
        auth=GatewayAuth(key,{device:'personal-control-hub'},'personal-control-hub',origin,
                         os.getuid(),os.getuid(),fixture=True,clock=lambda:1000)
        app=RRApplication(self.store,auth,manifest,resources)
        raw=b'actual integration fixture bytes'
        doc={'schema':1,'client_request_id':str(uuid.uuid4()),'project_id':'personal-control-hub',
             'page_path':'/','source':manifest['source'],'resource':manifest['resources'][0],
             'description':'fixture only','expected':'saved','actual':'observed','steps':['inspect bytes'],
             'submitted_at':'2026-10-19T00:00:00Z',
             'attachments':[{'attachment_id':'note','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}
        body=(b'--b\r\nContent-Disposition: form-data; name="feedback"\r\nContent-Type: application/json\r\n\r\n'
              +canonical(doc)+b'\r\n--b\r\nContent-Disposition: form-data; name="attachment.0"; filename="note.txt"\r\n'
              b'Content-Type: text/plain\r\n\r\n'+raw+b'\r\n--b--\r\n')
        def call(method,path,body=b''):
            value={'v':1,'challenge':auth.challenge,'device_sha256':device,'project_id':'personal-control-hub',
                   'origin':origin,'method':method,'path':path,'body_sha256':hashlib.sha256(body).hexdigest(),
                   'nonce':str(uuid.uuid4()),'issued_at':1000,'expires':1030}
            value['mac']=hmac.new(key,canonical(value),hashlib.sha256).hexdigest()
            headers={'origin':origin,'x-pch-rr-auth':base64.urlsafe_b64encode(canonical(value)).decode().rstrip('='),
                     'content-type':'multipart/form-data; boundary=b'}
            return app.dispatch(method,path,headers,body,os.getuid())
        code,receipt,_=call('POST','/__rr/v1/feedback',body)
        self.assertEqual(code,201)
        self.assertEqual(call('POST','/__rr/v1/feedback',body)[0],200)
        recovered=call('GET','/__rr/v1/feedback/by-request/'+doc['client_request_id'])[1]
        self.assertEqual(recovered['receipt'],receipt)
        ident=receipt['feedback_id']
        self.assertEqual(call('GET','/__rr/v1/feedback/'+ident+'/attachments/note')[1],raw)
        read=agent_action(self.root,self.store,'read',feedback_id=ident)
        self.assertEqual(Path(read['attachments'][0]['local_file']).read_bytes(),raw)
        agent_action(self.root,self.store,'ack',feedback_id=ident,proof=read['read_receipt'])
        agent_action(self.root,self.store,'result',feedback_id=ident,proof=read['read_receipt'],
                     summary='Fixture content inspected; no business instruction executed.',references=[])
        self.assertEqual(call('GET','/__rr/v1/feedback/'+ident)[1]['state'],'processed')
        self.assertEqual(self.task.tasks(),[])


if __name__ == '__main__': unittest.main()
