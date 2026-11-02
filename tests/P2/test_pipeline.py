"""正式语料、逐字节重现和输入保护。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P2实体识别/src'))
from p2_entities.pipeline import run


class PipelineTests(unittest.TestCase):
    def test_full_corpus_reproducible_and_approved(self):
        base = ROOT / '.test_tmp'
        base.mkdir(exist_ok=True)
        expected = json.loads((ROOT / 'src/P2实体识别/config/acceptance.json').read_text(encoding='utf-8'))
        original = (ROOT / 'processed/P1爬虫数据清洗/documents.json').read_bytes()
        with tempfile.TemporaryDirectory(dir=base, prefix='p2_official_') as value:
            folder = Path(value).resolve()
            self.assertTrue(folder.is_relative_to(base.resolve()))
            output, figures = folder / 'results', folder / 'figures'
            validation, _ = run(output, figures, draw=False)
            self.assertEqual(validation['documents'], 47)
            first = (output / 'entities.json').read_bytes()
            self.assertEqual(hashlib.sha256(first).hexdigest(), expected['entities_sha256'])
            run(output, figures, draw=False)
            self.assertEqual((output / 'entities.json').read_bytes(), first)
            checked = json.loads((output / 'quality_check.json').read_text(encoding='utf-8'))
            self.assertEqual((checked['passed'], checked['total']), (39, 39))
            self.assertTrue((output / '辅助资料/activity_candidates.json').is_file())
        self.assertEqual((ROOT / 'processed/P1爬虫数据清洗/documents.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
