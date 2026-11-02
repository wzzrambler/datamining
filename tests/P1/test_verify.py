"""基线迁移回归：缺失或正文被改动必须阻断正式数据校验。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import verify_p1


class BaselineVerification(unittest.TestCase):
    def test_missing_committed_baseline_is_rejected(self):
        parent = ROOT / '.test_tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder = Path(temp)
            shutil.copy2(verify_p1.paths.P1 / 'documents.json', folder / 'documents.json')
            with patch.object(verify_p1.paths, 'P1', folder):
                with self.assertRaisesRegex(AssertionError, '缺少原17篇历史基线'):
                    verify_p1.verify()

    def test_changed_legacy_content_is_rejected(self):
        source = verify_p1.paths.P1
        legacy = verify_p1.load(source / '辅助资料/legacy_documents.json')
        legacy[0]['content'] += '\n被意外改动的历史正文'
        parent = ROOT / '.test_tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder = Path(temp)
            shutil.copy2(source / 'documents.json', folder / 'documents.json')
            baseline = folder / '辅助资料/legacy_documents.json'
            baseline.parent.mkdir()
            baseline.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
            with patch.object(verify_p1.paths, 'P1', folder):
                with self.assertRaisesRegex(AssertionError, '历史基线内容不一致'):
                    verify_p1.verify()


if __name__ == '__main__':
    unittest.main()
