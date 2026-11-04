# -*- coding: utf-8 -*-
"""启动器：两种模式的边界、端口顺延、自检输出。"""
import contextlib
import io
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P7旅游规划Agent'))

import serve  # noqa: E402


class ModeTests(unittest.TestCase):
    def test_local_binds_loopback_and_ignores_deploy_token(self):
        with patch.dict(os.environ, {'P7_ACCESS_TOKEN': 'deploy-only-token'}, clear=False):
            config = serve.resolve_mode(False)
        self.assertFalse(config['public'])
        self.assertEqual(config['host'], '127.0.0.1')
        self.assertEqual(config['token'], '')      # 部署口令不该拦住本机调试
        self.assertTrue(config['open_browser'])

    def test_local_accepts_explicit_token(self):
        config = serve.resolve_mode(False, token='local-test')
        self.assertEqual(config['token'], 'local-test')

    def test_public_without_token_is_refused(self):
        with patch.dict(os.environ, {'P7_ACCESS_TOKEN': ''}, clear=False):
            with self.assertRaises(SystemExit) as caught:
                serve.resolve_mode(True)
        self.assertIn('口令', str(caught.exception))

    def test_public_with_short_token_is_refused(self):
        with self.assertRaises(SystemExit):
            serve.resolve_mode(True, token='short')

    def test_public_defaults_to_all_interfaces_and_same_origin(self):
        config = serve.resolve_mode(True, token='x' * 16)
        self.assertEqual(config['host'], '0.0.0.0')
        self.assertEqual(config['cors'], '')       # 同源部署，不需要放开跨域
        self.assertFalse(config['open_browser'])   # 服务器上没有浏览器可开

    def test_public_keeps_explicit_cors(self):
        with patch.dict(os.environ, {'P7_CORS_ORIGINS': 'https://demo.example'}, clear=False):
            config = serve.resolve_mode(True, token='x' * 16)
        self.assertEqual(config['cors'], 'https://demo.example')

    def test_public_star_is_narrowed_to_same_origin(self):
        with patch.dict(os.environ, {'P7_CORS_ORIGINS': '*'}, clear=False):
            config = serve.resolve_mode(True, token='x' * 16)
        self.assertEqual(config['cors'], '')


class PortTests(unittest.TestCase):
    def test_pick_port_skips_the_busy_one(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen(1)
            busy = sock.getsockname()[1]
            picked = serve.pick_port('127.0.0.1', busy, tries=12)
        self.assertNotEqual(picked, busy)
        self.assertTrue(serve.port_free('127.0.0.1', picked))


class CheckTests(unittest.TestCase):
    def test_check_covers_data_files_and_uses_known_levels(self):
        rows = serve.check()
        self.assertTrue(rows)
        for level, name, detail in rows:
            self.assertIn(level, ('OK', 'WARN', 'FAIL'))
            self.assertTrue(name)
            self.assertTrue(detail)
        data = [row for row in rows if row[1] in ('P2 实体', 'P6 路线', 'P6 条件建议')]
        self.assertEqual(len(data), 3)
        self.assertTrue(all(level == 'OK' for level, _, _ in data))

    def test_public_check_flags_missing_token(self):
        with patch.dict(os.environ, {'P7_ACCESS_TOKEN': ''}, clear=False):
            rows = serve.check(public=True)
        token = [row for row in rows if row[1] == '访问口令']
        self.assertEqual(token[0][0], 'FAIL')

    def test_check_accepts_token_argument(self):
        """文档里写的是 --check --public --token，不应该再去环境里找。"""
        with patch.dict(os.environ, {'P7_ACCESS_TOKEN': ''}, clear=False):
            rows = serve.check(public=True, token='x' * 16)
        token = [row for row in rows if row[1] == '访问口令'][0]
        self.assertEqual(token[0], 'OK')

    def test_public_check_treats_same_origin_as_ok(self):
        with patch.dict(os.environ, {'P7_CORS_ORIGINS': ''}, clear=False):
            rows = serve.check(public=True, token='x' * 16)
        cors = [row for row in rows if row[1] == 'CORS'][0]
        self.assertEqual(cors[0], 'OK')
        self.assertIn('同源', cors[2])

    def test_public_banner_prints_proxy_hint(self):
        config = serve.resolve_mode(True, token='x' * 16, port=8123)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            serve.banner(config)
        text = buffer.getvalue()
        self.assertIn('proxy_buffering off', text)
        self.assertIn('8123', text)
        self.assertIn('单 worker', text)

    def test_banner_behind_proxy_skips_lan_address(self):
        """反代场景只监听 127.0.0.1，不能提示一个连不上的局域网地址，也不该让人放行 8000。"""
        config = serve.resolve_mode(True, token='x' * 16, host='127.0.0.1', port=8123)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            serve.banner(config)
        text = buffer.getvalue()
        self.assertIn('Nginx 反代', text)
        self.assertNotIn('局域网', text)
        self.assertNotIn('放行 8123', text)


if __name__ == '__main__':
    unittest.main()
