import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_read_diagnostic as d
from hub.service_contract import ServiceError


class ReadDiagnosticTests(unittest.TestCase):
    def schemas(self):
        return {'ServerNotification':{'oneOf':[{'properties':{'method':{'enum':[m]}}}
            for m in d.ALLOWED_NOTIFICATIONS]}}

    def test_unknown_notification_cannot_erase_received_response(self):
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'response.json'
            fact=d.response_fact({'id':d.THREAD,'cwd':'/owned','title':'private'},'/owned')
            with self.assertRaises(ServiceError):
                d.retain_response_then_notifications(path,fact,[{'method':'unapproved','params':{'secret':'private'}}],self.schemas())
            self.assertEqual(json.loads(path.read_text()),fact)
            self.assertNotIn('private',path.read_text())
            with self.assertRaises(ServiceError):d.atomic_record(path,{'replacement':True})
            self.assertEqual(json.loads(path.read_text()),fact)
            self.assertFalse(path.with_name(path.name+'.tmp').exists())

    def test_controlled_errors_keep_categories_and_discard_bodies(self):
        for category,expected in [('unsupported_store_operation','operation_unsupported'),
            ('method_unsupported','method_unsupported'),('thread_unavailable','thread_unavailable')]:
            error=ServiceError('CODEX_RPC_REJECTED',details={'rpc_code':-32601,'error_categories':[category],
                'message_sha256':'a'*64,'message_bytes':31,'raw_message_retained':False,'body':'private'})
            fact=d.controlled_error(error);self.assertEqual(fact['status'],expected)
            self.assertNotIn('private',json.dumps(fact))
        self.assertEqual(d.controlled_error(ServiceError('CODEX_SCHEMA_REJECTED'))['status'],'schema_rejection')

    def test_schema_exact_bound_thread_and_remote_disabled_required(self):
        schema=self.schemas()
        for method in ('account/updated','account/rateLimits/updated','configWarning'):
            self.assertFalse(d.notification_fact({'method':method,'params':{'title':'private'}},schema)['body_retained'])
        with self.assertRaises(ServiceError):d.notification_fact({'method':'thread/status/changed','params':{'threadId':'other'}},schema)
        with self.assertRaises(ServiceError):d.notification_fact({'method':'remoteControl/status/changed','params':{'status':'enabled'}},schema)
        with self.assertRaises(ServiceError):d.notification_fact({'method':'configWarning'}, {'ServerNotification':{'oneOf':[]}})
