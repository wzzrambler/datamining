import json
import tempfile
import unittest
from pathlib import Path
from src.diagnostics import LoadDiagnostics, safe_error, safe_url


class DiagnosticsTests(unittest.TestCase):
    def test_credentials_are_not_logged(self):
        self.assertEqual(safe_url('https://user:secret@example.org/path?xsec_token=private#fragment'), 'https://example.org/path')
        text = safe_error('failed https://example.org/path?xsec_token=private token=another cookie=hidden')
        for secret in ('private', 'another', 'hidden'):
            self.assertNotIn(secret, text)

    def test_pending_and_failed_are_distinct(self):
        class Request:
            url = 'https://example.org/script.js?token=private'
            method = 'GET'
            resource_type = 'script'
            failure = 'net::ERR_CONNECTION_RESET'
        root = Path(__file__).parent / '.test-tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            diagnostics = LoadDiagnostics(Path(tmp))
            request = Request()
            diagnostics.request(request)
            diagnostics.snapshot(None, 'timeout')
            data = json.loads((Path(tmp)/'load_diagnostics.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['pending']), 1)
            diagnostics.end(request, failed=True)
            diagnostics.snapshot(None, 'timeout')
            data = json.loads((Path(tmp)/'load_diagnostics.json').read_text(encoding='utf-8'))
            self.assertEqual(data['pending'], [])
            self.assertEqual(data['counts']['failed'], 1)
            log = (Path(tmp)/'network.jsonl').read_text(encoding='utf-8')
            self.assertNotIn('private', log)
            self.assertIn('ERR_CONNECTION_RESET', log)
