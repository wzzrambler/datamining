"""质检关键误报与漏报回归；不访问网络，不改正式数据。"""
import copy
import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import quality_p1 as qa


class QualityChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = qa.load(qa.paths.P1 / 'documents.json')
        cls.expected = qa.load(qa.paths.P1 / '辅助资料/legacy_documents.json')[0]

    def test_wechat_article_parameters_are_not_discarded(self):
        a = 'https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=X&scene=21'
        b = 'https://mp.weixin.qq.com/s?mid=2&__biz=A&idx=1&sn=Y'
        same = 'https://mp.weixin.qq.com/s?sn=X&idx=1&mid=1&__biz=A&scene=9'
        self.assertNotEqual(qa.source_identity(a), qa.source_identity(b))
        self.assertEqual(qa.source_identity(a), qa.source_identity(same))

    def test_xhs_detail_routes_share_note_identity(self):
        note = 'a'*24
        self.assertEqual(qa.source_identity(f'https://www.xiaohongshu.com/explore/{note}?xsec_token=first'),
                         qa.source_identity(f'https://www.xiaohongshu.com/search_result/{note}?xsec_token=second'))

    def test_shifted_offsets_detected_even_when_substring_is_same(self):
        d = copy.deepcopy(self.docs[0])
        d['paragraphs'][0]['start'] += 1
        problems = qa.inspect_document(d, self.expected)
        self.assertTrue(any(p['检测项']=='段落位置异常' and p['严重程度']=='错误' for p in problems))

    def test_noise_and_legitimate_brackets_distinguished(self):
        text = '[行李寄存] 东门→湖边[笑哭R]\n[话题]\nhttp://example.com/test\n\ufffd'
        self.assertTrue(qa.NOISE_PATTERNS['已知表情残留'].search(text))
        self.assertTrue(qa.NOISE_PATTERNS['话题标记残留'].search(text))
        self.assertTrue(qa.NOISE_PATTERNS['替换字符乱码'].search(text))
        self.assertFalse(qa.NOISE_PATTERNS['疑似未知表情'].search('[行李寄存]'))
        self.assertFalse(qa.NOISE_PATTERNS['已知表情残留'].search('[行李寄存]'))

    def test_all_document_pairs_and_containment_checked(self):
        def doc(i, text):
            return dict(doc_id=i, content=text, source_url='https://example.com/'+i,
                        paragraphs=[dict(text=text)])
        text = ''.join(chr(0x4e00+i) for i in range(500))
        pairs = qa.find_duplicates([doc('old-a',text),doc('old-b',text),doc('new',text[:250])])
        self.assertTrue(any(p['文档A']=='old-a' and p['文档B']=='old-b' and '完全重复' in p['类型'] for p in pairs))
        self.assertTrue(any('包含关系' in p['类型'] for p in pairs))

    def test_metadata_csv_tampering_is_detected(self):
        parent = ROOT / '.test_tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            target = Path(temp)
            for name in ('documents.csv','paragraphs.csv','dataset.xlsx'):
                shutil.copy2(qa.paths.P1/name,target/name)
            with (target/'documents.csv').open(encoding='utf-8-sig',newline='') as f:
                reader = csv.DictReader(f)
                rows, fields = list(reader), reader.fieldnames
            rows[0]['title'] = '被意外修改的标题'
            qa.write_csv(target/'documents.csv',fields,rows)
            problems = qa.cross_format(target,self.docs)
            self.assertTrue(any(p['字段或段落']=='documents.csv全字段' for p in problems))

    def test_issue_evidence_does_not_export_access_token(self):
        result = qa.issue('a','test','url','https://example.com/path?xsec_token=SECRET','test')
        self.assertNotIn('SECRET',result['证据'])

    def test_negative_length_delta_is_rejected_under_unified_policy(self):
        d = copy.deepcopy(self.docs[0])
        d['raw_char_len'] = len(d['content']) - 2
        d['noise_char_removed'] = -2
        problems = qa.inspect_document(d,self.expected)
        self.assertTrue(any(p['检测项']=='数值字段为负' and p['严重程度']=='错误' for p in problems))
        d['noise_char_removed'] = -3
        self.assertTrue(any(p['检测项']=='数值字段为负' and p['严重程度']=='错误' for p in qa.inspect_document(d,self.expected)))


if __name__ == '__main__':
    unittest.main()
