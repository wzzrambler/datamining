# -*- coding: utf-8 -*-
"""离线单页：自包含、七场景齐全、字段与在线接口一致。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/P7'))

try:
    import build_offline
    SKIP = None
except Exception as error:          # 上游产物缺失时不让 P7 其他测试跟着红
    build_offline = None
    SKIP = '离线包依赖不可用：%s' % error


@unittest.skipIf(build_offline is None, SKIP or '')
class OfflineBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        try:
            cls.path = build_offline.build(Path(cls.tmp.name) / 'offline.html')
        except (FileNotFoundError, SystemExit) as error:
            raise unittest.SkipTest('缺少 outputs/P7/agent_runs.json：%s' % error)
        cls.html = cls.path.read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bundle_is_self_contained(self):
        self.assertNotIn('<link rel="stylesheet"', self.html)
        self.assertNotIn('<script src=', self.html)
        self.assertNotIn('href="http', self.html)
        self.assertGreater(len(self.html), 40_000)

    def test_payload_has_all_seven_scenes(self):
        match = re.search(r'window\.__P7_OFFLINE__ = (\{.*?\});</script>', self.html, re.S)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(len(payload['scenes']), 7)
        self.assertEqual(payload['meta']['counts']['documents'], 47)
        self.assertEqual(payload['meta']['counts']['routes'], 29)

    def test_each_scene_is_renderable(self):
        match = re.search(r'window\.__P7_OFFLINE__ = (\{.*?\});</script>', self.html, re.S)
        payload = json.loads(match.group(1))
        for scene in payload['scenes']:
            result = scene['result']
            self.assertIn(result['status'],
                          ('ok', 'partial', 'need_info', 'no_data', 'conflict'))
            self.assertTrue(result['answer'])
            if result['plan']:
                self.assertTrue(result['plan']['day_groups'])
                self.assertEqual(sum(len(g) for g in result['plan']['day_groups']),
                                 len(result['plan']['steps']))

    def test_bundle_covers_every_status(self):
        match = re.search(r'window\.__P7_OFFLINE__ = (\{.*?\});</script>', self.html, re.S)
        payload = json.loads(match.group(1))
        statuses = {scene['status'] for scene in payload['scenes']}
        self.assertEqual(statuses, {'ok', 'partial', 'need_info', 'no_data', 'conflict'})

    def test_web_data_is_in_sync_with_bundle(self):
        """静态托管读 web/offline.json，不能和单文件里的数据对不上。"""
        path = build_offline.WEB_DATA
        if not path.exists():
            self.skipTest('web/offline.json 还没生成')
        data = json.loads(path.read_text(encoding='utf-8'))
        match = re.search(r'window\.__P7_OFFLINE__ = (\{.*?\});</script>', self.html, re.S)
        payload = json.loads(match.group(1))

        def shape(scenes):
            return [(scene['input'], scene['status'],
                     len(scene['result']['plan']['steps']) if scene['result']['plan'] else 0)
                    for scene in scenes]

        self.assertEqual(shape(data['scenes']), shape(payload['scenes']))


if __name__ == '__main__':
    unittest.main()
