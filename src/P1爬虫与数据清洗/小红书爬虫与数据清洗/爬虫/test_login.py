"""登录入口的离线验证；不启动浏览器、不访问网站。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src import crawler, login


class LoginTests(unittest.TestCase):
    def test_entry_uses_only_login_and_current_profile(self):
        with patch.object(crawler, 'main', return_value=2) as run:
            self.assertEqual(login.login(), 2)
        args = crawler.parser().parse_args(run.call_args.args[0])
        self.assertTrue(args.login_only)
        self.assertEqual(args.profile, 'check_20261030')
        self.assertFalse(args.headless)

    def test_login_only_excludes_collection_modes(self):
        for extra in (['--test-once'], ['--status'], ['--url', 'https://www.xiaohongshu.com/explore'],
                      ['--headless'], ['--profile', 'default']):
            with self.subTest(extra=extra), patch.object(crawler, 'run') as run:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    crawler.main(['--login-only', *extra])
                run.assert_not_called()

    def test_login_report_does_not_export_notes_or_checkpoint(self):
        root = crawler.BASE / '.test-tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            folder = Path(temp)
            def confirm(args, store, job, guard, report):
                self.assertTrue(args.login)
                self.assertEqual(job, 'login:check_20261030')
                report['login_confirmed_by_user'] = True
            with patch.object(crawler, 'OUT', folder/'output'), \
                    patch.object(crawler, 'bootstrap'), \
                    patch.object(crawler, 'online', side_effect=confirm), \
                    patch.object(crawler, 'save_result') as export, \
                    patch.object(crawler, 'export_checkpoint') as checkpoint, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawler.main(['--login-only', '--state-dir', str(folder/'state')]), 0)
            export.assert_not_called()
            checkpoint.assert_not_called()
            report = json.loads(next((folder/'output/login_reports').glob('*.json')).read_text(encoding='utf-8'))
            self.assertTrue(report['login_confirmed_by_user'])
            self.assertEqual(report['requests_run'], 0)


if __name__ == '__main__':
    unittest.main()
