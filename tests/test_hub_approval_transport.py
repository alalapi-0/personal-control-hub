import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.codex_adapter import AppServerAdapter, APPROVAL_RESPONSE
from hub.workbench_store import canonical
from hub.service_contract import ServiceError


class ApprovalTransportTests(unittest.TestCase):
    def adapter(self):
        adapter=AppServerAdapter('/fixture',schemas={'ServerRequest':{'type':'object'},
            APPROVAL_RESPONSE:{'type':'object','properties':{'decision':{'enum':['accept','decline']}},'required':['decision']}})
        sent=[];adapter._send=sent.append
        return adapter,sent

    def request(self):
        return {'id':17,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread','turnId':'turn'}}

    def decision(self,request):
        return {'status':'accepted','decision':'accept','request_digest':hashlib.sha256(canonical(request)).hexdigest(),
                'operation_sha256':'a'*64,'control_id':'control'}

    def test_exact_backend_single_accept_reaches_native_response(self):
        adapter,sent=self.adapter();request=self.request();adapter.request_handler=lambda value:self.decision(value)
        adapter.inbox.put(request);adapter._message(.1)
        self.assertEqual(sent,[{'id':17,'result':{'decision':'accept'}}])
        self.assertEqual(adapter.denials,[]);self.assertEqual(len(adapter.approvals),1)

    def test_wrong_digest_broader_decision_and_file_changes_never_accept(self):
        for mutation in ({'request_digest':'b'*64},{'decision':'acceptForSession'},{'status':'pending'},{'operation_sha256':None}):
            with self.subTest(mutation=mutation):
                adapter,sent=self.adapter();request=self.request()
                adapter.request_handler=lambda value:{**self.decision(value),**mutation}
                adapter.inbox.put(request);adapter._message(.1)
                self.assertEqual(sent[0]['result']['decision'],'decline');self.assertEqual(len(adapter.denials),1)
        adapter,sent=self.adapter();request=self.request();request['method']='item/fileChange/requestApproval'
        adapter.request_handler=lambda value:self.decision(value)
        adapter.inbox.put(request);adapter._message(.1)
        self.assertEqual(sent[0]['result']['decision'],'decline')

    def test_missing_response_schema_sends_decline_and_reports_protocol_failure(self):
        adapter,sent=self.adapter();del adapter.schemas[APPROVAL_RESPONSE]
        adapter.request_handler=lambda value:self.decision(value)
        adapter.inbox.put(self.request())
        with self.assertRaises(ServiceError):adapter._message(.1)
        self.assertEqual(sent[0]['result']['decision'],'decline')
