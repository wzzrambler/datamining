# -*- coding: utf-8 -*-
"""跨批次复用：索引只收完成包，指纹相同才命中，拷贝时标明来源。"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P5条件建议抽取'))

from p5_agent import reuse  # noqa: E402
from p5_agent.common import read_json, write_json  # noqa: E402


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.a = self.root / 'run-a'
        self.b = self.root / 'run-b'

    def tearDown(self):
        self.tmp.cleanup()

    def checkpoint(self, run, key, fingerprint, status='COMPLETED'):
        write_json(run / 'checkpoints' / (key + '.json'),
                   {'fingerprint': fingerprint, 'host': {'status': status},
                    'usage': [{'prompt_tokens': 100, 'completion_tokens': 10}]})

    def test_index_keeps_only_completed(self):
        self.checkpoint(self.a, 'PKG-1', 'fp-1')
        self.checkpoint(self.a, 'PKG-2', 'fp-2', status='FAILED')
        index = reuse.index_run(self.a)
        self.assertEqual(index, {'PKG-1': 'fp-1'})

    def test_index_rebuilds_after_new_checkpoint(self):
        self.checkpoint(self.a, 'PKG-1', 'fp-1')
        self.assertEqual(len(reuse.index_run(self.a)), 1)
        self.checkpoint(self.a, 'PKG-2', 'fp-2')
        # 索引缓存里记的是文件数，多了一个包就必须重建
        self.assertEqual(len(reuse.index_run(self.a)), 2)

    def test_build_map_prefers_latest_run(self):
        self.checkpoint(self.a, 'PKG-1', 'fp-1')
        time.sleep(0.05)
        self.checkpoint(self.b, 'PKG-9', 'fp-1')
        mapping = reuse.build_map(self.root, current=self.root / 'run-c')
        self.assertEqual(mapping['fp-1'][0].name, 'run-b')

    def test_current_run_is_never_a_source(self):
        self.checkpoint(self.a, 'PKG-1', 'fp-1')
        mapping = reuse.build_map(self.root, current=self.a)
        self.assertEqual(mapping, {})

    def test_reuse_marks_source_and_inherited_usage(self):
        self.checkpoint(self.a, 'PKG-1', 'fp-1')
        dest = self.b / 'checkpoints' / 'PKG-1.json'
        state = reuse.reuse_checkpoint(self.a, 'PKG-1', dest, 'run-a')
        self.assertEqual(state['reused_from'], 'run-a')
        self.assertTrue(state['usage_inherited'])
        self.assertTrue(dest.exists())
        self.assertEqual(read_json(dest)['fingerprint'], 'fp-1')


if __name__ == '__main__':
    unittest.main()
