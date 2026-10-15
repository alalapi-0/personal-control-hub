"""Story results must remain bound to immutable, alias-free staged inputs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hub.service_contract import ServiceError
from hub.story_exchange import is_business, output_result, prompt, read_result, validate
from hub.task_service import root_identity


class StoryExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / 'stage'
        self.source = base / 'source'
        self.root.mkdir(); self.source.mkdir()
        (self.root / 'input').mkdir(); (self.root / 'output').mkdir()
        inputs = {'AGENTS.md': b'Only create three candidate proposals.',
                  'input/export.json': b'{"request_id":"request-1"}',
                  'input/context.json': b'{"stage":"C01"}',
                  'input/result-template.json': b'{"not_a_result":true}',
                  'input/result-schema.json': json.dumps({'type': 'object', 'required': ['objects'], 'properties': {'objects': {'type': 'array'}}}).encode()}
        for name, raw in inputs.items():
            (self.root / name).write_bytes(raw)
        self.grant = {'kind': 'story_exchange', 'grant_id': 'story-grant', 'version': 1,
                      'source': 'verified_owner_decision', 'task_id': 'task-1', 'project_id': 'story-faceless-utopia',
                      'root': str(self.root), 'root_identity': root_identity(self.root), 'mode': 'new',
                      'parent_task_id': None, 'auth': 'ChatGPT', 'expires_at': time.time()+1000,
                      'max_turn_seconds': 120, 'isolated_root': True, 'writable_scope': ['output/'],
                      'checks': ['story_exchange'], 'task_spec': {'requirement': 'Three stories', 'scope': 'C01', 'acceptance': 'Three distinct candidates'},
                      'story_exchange': {'canonical_root': str(self.source), 'canonical_root_identity': root_identity(self.source),
                                         'registry_hash': '1'*64, 'specification_sha256': '2'*64,
                                         'request_id': 'request-1', 'request_sha256': '3'*64,
                                         'input_files': [{'path': name, 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in inputs.items()],
                                         'output_path': 'output/result.json', 'registration_sha256': '4'*64,
                                         'expected_count': 3, 'source_preconditions': {'workspace_revision': 22, 'workspace_sha256': '5'*64,
                                                                                  'source_state_sha256': '6'*64, 'baseline_id': 'baseline', 'dataset': 'live'}}}
        self.result = {'request_id': 'request-1', 'request_sha256': '3'*64, 'baseline_id': 'baseline', 'dataset': 'live', 'objects': []}
        for i in range(3):
            data = {k: f'{k} meaningful story {i}' for k in ('title', 'goal', 'obstacle', 'action', 'choice', 'consequence', 'difference', 'strength', 'weakness')}
            data['request_id'] = 'request-1'
            self.result['objects'].append({'id': f'option-{i}', 'type': 'story_option', 'status': 'candidate', 'baseline_id': 'baseline', 'dataset': 'live',
                                          'provenance': {'actor': 'agent', 'simulated': False}, 'data': data})

    def write_result(self):
        (self.root/'output/result.json').write_text(json.dumps(self.result))

    def test_exact_contract_and_deepcopy(self):
        self.assertTrue(is_business(self.grant))
        checked = validate(self.grant, live=True)
        checked['task_spec']['scope'] = 'different'
        self.assertEqual(self.grant['task_spec']['scope'], 'C01')
        self.assertIn('模板不是结果', prompt(self.grant))
        for patch in ({'approved_commands': []}, {'writable_scope': ['.']}, {'max_turn_seconds': True}, {'expires_at': float('nan')}):
            with self.subTest(patch=patch), self.assertRaises(ServiceError):
                validate(dict(self.grant, **patch))

    def test_real_output_summary_repeat_is_readonly(self):
        self.write_result()
        first = output_result(self.grant)
        self.assertEqual(first, output_result(self.grant))
        self.assertFalse(first['source_promoted'])
        self.assertEqual(len(first['objects']), 3)
        self.assertNotIn('provenance', first['objects'][0])
        self.assertFalse(list(self.source.iterdir()))
        self.assertEqual(first['output_sha256'], hashlib.sha256((self.root/'output/result.json').read_bytes()).hexdigest())

    def test_public_summary_stays_below_store_byte_limit(self):
        for index, item in enumerate(self.result['objects']):
            for field in ('title', 'goal', 'obstacle', 'action', 'choice', 'consequence', 'difference', 'strength', 'weakness'):
                item['data'][field] = str(index) + '故事候选' * 300
        self.write_result()
        summary = output_result(self.grant)
        self.assertLess(len(json.dumps(summary, ensure_ascii=False).encode()), 16384)
        self.assertLessEqual(len(summary['objects'][0]['goal']), 96)
        self.assertGreater(len(read_result(self.grant)['objects'][0]['data']['goal']), 96)

    def test_expired_grant_only_allows_verified_artifact_read(self):
        self.write_result()
        self.grant['expires_at'] = time.time() - 100
        expiry = self.grant['expires_at']
        with self.assertRaises(ServiceError): validate(self.grant, live=True)
        with self.assertRaises(ServiceError): output_result(self.grant)
        self.assertEqual(read_result(self.grant), self.result)
        self.assertEqual(self.grant['expires_at'], expiry)
        (self.root/'input/context.json').write_text('{}')
        with self.assertRaises(ServiceError): read_result(self.grant)

    def test_input_tamper_and_unregistered_outputs(self):
        self.write_result()
        target = self.root/'input/context.json'
        original = target.read_bytes()
        target.write_text('{}')
        with self.assertRaises(ServiceError): output_result(self.grant)
        target.write_bytes(original)
        (self.root/'output/extra.txt').write_text('extra')
        with self.assertRaises(ServiceError): validate(self.grant, live=True)

    def test_file_and_directory_aliases(self):
        target = self.root/'input/context.json'
        raw = target.read_bytes()
        target.unlink(); target.symlink_to(self.source/'context.json')
        (self.source/'context.json').write_bytes(raw)
        with self.assertRaises(ServiceError): validate(self.grant, live=True)
        target.unlink(); os.link(self.source/'context.json', target)
        with self.assertRaises(ServiceError): validate(self.grant, live=True)
        target.unlink(); target.write_bytes(raw)
        (self.root/'output').rmdir(); (self.root/'output').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((ServiceError, OSError)): validate(self.grant, live=True)

    def test_result_binding_and_non_simulation(self):
        for mutate in (lambda r: r.update(request_sha256='9'*64),
                       lambda r: r.update(dataset='fixture'),
                       lambda r: r['objects'][0]['provenance'].update(simulated=True),
                       lambda r: r['objects'][0]['data'].update(request_id='other'),
                       lambda r: r['objects'][0]['data'].update(goal='<concrete goal>'),
                       lambda r: r['objects'].append(copy.deepcopy(r['objects'][0]))):
            original = copy.deepcopy(self.result)
            mutate(self.result); self.write_result()
            with self.subTest(mutate=mutate), self.assertRaises(ServiceError): output_result(self.grant)
            self.result = original

    def test_json_ambiguity_and_schema_are_rejected(self):
        for raw in ('{"objects":[],"objects":[]}', '{"objects":NaN}', '{"objects":1e999}'):
            (self.root/'output/result.json').write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(ServiceError): output_result(self.grant)
        self.write_result()
        schema_path = self.root/'input/result-schema.json'
        for schema in ({'type': 'string'}, {'$ref': 'https://example.invalid/schema'}):
            raw = json.dumps(schema).encode(); schema_path.write_bytes(raw)
            for row in self.grant['story_exchange']['input_files']:
                if row['path'] == 'input/result-schema.json': row['sha256'] = hashlib.sha256(raw).hexdigest()
            with self.subTest(schema=schema), self.assertRaises(ServiceError): output_result(self.grant)


if __name__ == '__main__':
    unittest.main()
