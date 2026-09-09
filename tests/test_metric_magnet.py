import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hub.metric_magnet import collect_magnet, collect_jav, _details, _log
NOW = '2026-09-09T00:00:00Z'

class MagnetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, path, text):
        dest = self.root/path
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(text)

    def collect(self,jav=False):
        result = (collect_jav if jav else collect_magnet)(self.root,'fixture',NOW,{'data_root':str(self.root)})
        return result,{r['metric_id'].split('.',1)[1]:r for r in result['metrics']}

    def jav(self):
        self.put('jav_codes.txt','AA-01\nAA-01\nAA-02\nAA-03\ninvalid\n')
        def block(code,source=''):
            return f'## {code}\n'+(f'来源: {source}\n' if source else '')+f'标题: PRIVATE\n大小: 1 GiB\n磁力: magnet:?xt={code}\n'
        self.put('magnets.txt','# 每行一个磁力链接，已排除标题含 Reducing Mosaic 的结果\n# 选择规则: 文件体积最大，其次分辨率/码率更高\n# === 首次抓取成功 (1) ===\nmagnet:?xt=AA-01\n# === 重试抓取成功 (1) ===\nmagnet:?xt=AA-02\n# 详细信息\n'+block('AA-01','首次抓取')+block('AA-02','重试抓取')+'## AA-03\n状态: 未找到可用资源 (未处理)\n')
        self.put('magnets_log.txt','总计: 3 个番号\n成功: 2\n  首次抓取: 1\n  重试抓取: 1\n失败: 1\n[OK/首次] AA-01 -> PRIVATE\n[OK/重试] AA-02 -> PRIVATE\n[FAIL] AA-03 -> 未处理\n')
        self.put('magnets_retry.txt','# 重试抓取成功的磁力链接（共 1 个）\n# AA-02\nmagnet:?xt=AA-02\n# 详细信息\n'+block('AA-02'))
        self.put('magnets_retry_codes.txt','# 通过 --retry-failed 重试成功的番号\nAA-02\n')

    def test_magnet_47(self):
        self.put('input/codes.txt','\n'.join(f'AA-{n:02}' for n in range(47))+'\nAA-01\nAA-01\nAA-02\nAA-03\n')
        text='# Magnet Fetcher 输出结果\n# 总数: 47 | 成功: 34 | 未找到: 13 | 异常: 0\n'
        text+='\n'.join(f'[AA-{n:02}]\n状态: '+('成功' if n<34 else '未找到')+'\n标题: PRIVATE\n大小: -\n磁力链: -\n' for n in range(47))
        self.put('output/magnets.txt',text)
        result,rows=self.collect()
        for name,n in [('input_unique_codes',47),('saved_total',47),('saved_success',34),('saved_not_found',13),('saved_error',0)]:
            self.assertEqual(rows[name]['value'],n)
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertNotIn('AA-01',json.dumps(result))
        self.put('output/magnets.txt',text.replace('总数: 47','总数: 48'))
        _,rows=self.collect()
        self.assertIsNone(rows['saved_total']['value'])
        self.assertEqual(rows['input_unique_codes']['value'],47)

    def test_jav_partition_privacy(self):
        self.jav()
        result,rows=self.collect(True)
        for name,n in [('saved_total',3),('saved_success',2),('saved_fail_or_unprocessed',1),('saved_initial_success',1),('saved_retry_success',1),('retry_file_success',1),('input_unique_codes',3)]:
            self.assertEqual(rows[name]['value'],n,name)
        self.assertTrue(all(r['value']==1 for k,r in rows.items() if k.endswith('_consistent')))
        for private in ['PRIVATE','AA-01','magnet:?']:
            self.assertNotIn(private,json.dumps(result))
        self.assertIsNone(rows['current_run_state']['value'])
        self.assertTrue(all(r['business_at'] is None for r in rows.values()))

    def test_old_source_and_semantics(self):
        self.jav()
        _,before=self.collect(True)
        p=self.root/'magnets.txt'
        p.write_text(p.read_text().replace('PRIVATE','CHANGED'))
        _,after=self.collect(True)
        self.assertEqual(before['saved_success']['source_version'],after['saved_success']['source_version'])
        p.write_text(p.read_text().replace('来源: 首次抓取\n','').replace('来源: 重试抓取\n',''))
        _,rows=self.collect(True)
        self.assertEqual(rows['saved_success']['value'],2)
        self.assertIsNone(rows['saved_initial_success']['value'])
        self.assertEqual(rows['log_initial_success']['value'],1)

    def test_invalid_independent_groups(self):
        self.jav()
        p=self.root/'magnets.txt'
        original=p.read_text()
        for text in [original.replace('## AA-02','## AA-01'),original.replace('标题:','BAD:',1),original.replace('# 每行一个','wrong 每行一个')]:
            p.write_text(text)
            _,rows=self.collect(True)
            self.assertIsNone(rows['saved_total']['value'])
            self.assertEqual(rows['log_total']['value'],3)
            self.assertEqual(rows['retry_file_success']['value'],1)

    def test_empty_retry_prefix_preserves_other_files(self):
        self.jav()
        self.put('magnets_retry.txt', '# 详细信息\n')
        _, rows = self.collect(True)
        self.assertIsNone(rows['retry_file_success']['value'])
        self.assertEqual(rows['saved_success']['value'], 2)
        self.assertEqual(rows['log_total']['value'], 3)

    def test_magnet_printable_codes_keep_spaces_and_case_deduplication(self):
        self.put('input/codes.txt', 'SP ACE-01\nsp ace-01\n')
        self.put('output/magnets.txt', '# Magnet Fetcher 输出结果\n# 总数: 1 | 成功: 0 | 未找到: 1 | 异常: 0\n[SP ACE-01]\n状态: 未找到\n标题: -\n大小: -\n磁力链: -\n')
        _, rows = self.collect()
        self.assertEqual(rows['input_unique_codes']['value'], 1)
        self.assertEqual(rows['saved_total']['value'], 1)
        self.assertEqual(rows['saved_not_found']['value'], 1)

    def test_conflict_budgets(self):
        self.jav()
        self.put('magnets_retry_codes.txt','# 通过 --retry-failed 重试成功的番号\nAA-01\n')
        _,rows=self.collect(True)
        self.assertEqual(rows['retry_membership_consistent']['value'],0)
        self.assertEqual(rows['saved_success']['value'],2)
        for setting in ['MAX_BYTES','MAX_LINES','MAX_RECORDS']:
            with patch('hub.metric_magnet.'+setting,1):
                _,rows=self.collect(True)
                self.assertIsNone(rows['saved_total']['value'])

    def test_bad_source_preserves_counts_and_bad_prefix(self):
        self.jav()
        p=self.root/'magnets.txt'
        p.write_text(p.read_text().replace('来源: 首次抓取','来源: INVALID'))
        _,rows=self.collect(True)
        self.assertEqual(rows['saved_success']['value'],2)
        self.assertIsNone(rows['saved_initial_success']['value'])
        p.write_text(p.read_text().replace('# 详细信息','garbage\n# 详细信息'))
        _,rows=self.collect(True)
        self.assertIsNone(rows['saved_total']['value'])

    def test_replacement_during_read_and_retry_duplicate(self):
        self.jav()
        self.put('magnets_retry_codes.txt','# 通过 --retry-failed 重试成功的番号\nAA-02\nAA-02\n')
        _,rows=self.collect(True)
        self.assertIsNone(rows['retry_code_records']['value'])
        self.assertEqual(rows['saved_success']['value'],2)
        from hub.metric_sources import read_metadata
        def changed(root,name):
            data,metadata=read_metadata(root,name)
            if name=='magnets.txt':
                dest=Path(root)/name
                new=dest.with_suffix('.new')
                new.write_bytes(data)
                new.replace(dest)
            return data,metadata
        with patch('hub.metric_magnet.read_metadata',side_effect=changed):
            _,rows=self.collect(True)
        self.assertIsNone(rows['saved_total']['value'])
        self.assertEqual(rows['log_total']['value'],3)

if __name__=='__main__':
    unittest.main()
