"""从仓库根目录运行的离线测试；不启动浏览器，不连接OCR。"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P1 = ROOT / 'src/P1爬虫与数据清洗/小红书爬虫与数据清洗'


class OfflineSuites(unittest.TestCase):
    def run_suite(self, cwd, args):
        completed = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest', *args],
                                   cwd=cwd, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        print(completed.stderr.strip())

    def test_cleaning(self):
        self.run_suite(P1 / '数据清洗', ['discover', '-s', 'tests', '-v'])

    def test_crawler_offline(self):
        self.run_suite(P1 / '爬虫', ['test_crawler', 'test_batch', 'test_diagnostics', 'test_login', '-v'])

    def test_documents(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'tools/verify_p1.py')],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
