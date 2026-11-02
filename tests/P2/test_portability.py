"""Checkout EOL may change; content and binary integrity must not."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from artifact_integrity import file_sha256, matches_sha256, repository_path


class PortableIntegrityTests(unittest.TestCase):
    def test_text_eol_is_compatible_but_content_changes_fail(self):
        base = ROOT / '.test_tmp'
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            path = Path(temp) / 'data.json'
            lf = '{\n  "内容": "甲\\n乙"\n}\n'.encode('utf-8')
            crlf = lf.replace(b'\n', b'\r\n')
            hashes = [hashlib.sha256(data).hexdigest() for data in (lf, crlf)]
            for data in (lf, crlf):
                path.write_bytes(data)
                self.assertTrue(all(matches_sha256(path, h) for h in hashes))
                self.assertEqual(file_sha256(path), hashes[0])
            path.write_bytes(lf.replace('甲'.encode(), '丙'.encode()))
            self.assertTrue(all(not matches_sha256(path, h) for h in hashes))

    def test_binary_hashes_remain_byte_exact(self):
        base = ROOT / '.test_tmp'
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            path = Path(temp) / 'data.zip'
            path.write_bytes(b'PK\r\n')
            self.assertFalse(matches_sha256(path, hashlib.sha256(b'PK\n').hexdigest()))

    def test_csv_record_eol_and_quoted_multiline_source_are_preserved(self):
        base = ROOT / '.test_tmp'
        base.mkdir(exist_ok=True)
        mixed = b'id,text\r\n1,"first\nsecond ""quoted"" line"\r\n'
        expected = hashlib.sha256(mixed).hexdigest()
        lf = mixed.replace(b'\r\n', b'\n')
        with tempfile.TemporaryDirectory(dir=base) as temp:
            path = Path(temp) / 'evidence.csv'
            for data in (mixed, lf, lf.replace(b'\n', b'\r\n')):
                path.write_bytes(data)
                self.assertTrue(matches_sha256(path, expected))
            path.write_bytes(lf.replace(b'first', b'changed'))
            self.assertFalse(matches_sha256(path, expected))

    def test_manifest_paths_and_escape_rejection(self):
        self.assertEqual(repository_path(ROOT, 'processed\\P5条件建议抽取\\README.md'),
                         repository_path(ROOT, 'processed/P5条件建议抽取/README.md'))
        for name in ('../outside', '..\\outside', 'C:\\outside', '/outside', 'C:outside'):
            with self.assertRaises(ValueError):
                repository_path(ROOT, name)
