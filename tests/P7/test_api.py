# -*- coding: utf-8 -*-
"""Web 接口：结构、状态码、SSE 事件顺序。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P7旅游规划Agent'))

try:
    from fastapi.testclient import TestClient
    import api
    CLIENT = TestClient(api.app)
    SKIP = None
except Exception as error:      # 没装 fastapi 时不让整套测试红掉
    CLIENT = None
    SKIP = 'Web 依赖不可用：%s' % error


@unittest.skipIf(CLIENT is None, SKIP or '')
class ApiTests(unittest.TestCase):
    def test_health_reports_data_scale(self):
        body = CLIENT.get('/api/health').json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['counts']['documents'], 47)
        self.assertGreater(body['counts']['routes'], 20)
        self.assertGreater(body['counts']['rules'], 1000)

    def test_scenes_cover_three_areas(self):
        areas = CLIENT.get('/api/scenes').json()['areas']
        self.assertEqual(len(areas), 3)
        self.assertTrue(all(a['routes'] > 0 for a in areas))
        self.assertEqual(sum(a['routes'] for a in areas), 29)

    def test_plan_returns_renderable_structure(self):
        response = CLIENT.post('/api/plan', json={
            'query': '带6岁孩子去广州长隆，一天怎么安排', 'mode': 'rule'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['plan']['days_actual'], 1)
        self.assertEqual(len(body['plan']['day_groups']), 1)
        self.assertEqual(len(body['plan']['steps']), 8)
        first = body['plan']['steps'][0]
        self.assertNotIn('\u20e3', first['spot'])   # 序号由前端渲染，不再重复带 1⃣
        self.assertTrue(body['turns'])

    def test_day_groups_match_cli_answer_for_every_scene(self):
        """接口层重算的分组必须和 CLI 的 answer 同源，否则前端"第 N 天"会和文本对不上。"""
        for query in api.engine.SCENES:
            body = CLIENT.post('/api/plan', json={'query': query, 'mode': 'rule'}).json()
            plan = body['plan']
            if not plan:
                continue
            self.assertEqual(len(plan['day_groups']), plan['days_actual'], query)
            self.assertEqual(sum(len(g) for g in plan['day_groups']),
                             len(plan['steps']), query)

    def test_two_day_route_uses_source_day_boundary(self):
        body = CLIENT.post('/api/plan', json={
            'query': '九寨沟玩两天，怎么安排', 'mode': 'rule'}).json()
        plan = body['plan']
        self.assertEqual(plan['days_actual'], 2)
        self.assertEqual([len(g) for g in plan['day_groups']], [12, 16])
        self.assertIn('【第1 天】', body['answer'])
        self.assertIn('【第2 天】', body['answer'])

    def test_out_of_library_area_is_explained(self):
        body = CLIENT.post('/api/plan', json={
            'query': '去三亚玩三天', 'mode': 'rule'}).json()
        self.assertEqual(body['status'], 'no_data')
        self.assertIsNone(body['plan'])
        self.assertIn('三亚', body['answer'])

    def test_short_query_rejected(self):
        response = CLIENT.post('/api/plan', json={'query': '嗯', 'mode': 'rule'})
        self.assertEqual(response.status_code, 422)

    def test_llm_without_key_is_rejected(self):
        if api.LLM_READY:
            self.skipTest('本机配置了模型密钥')
        response = CLIENT.post('/api/plan', json={'query': '带爸妈去九寨沟一天', 'mode': 'llm'})
        self.assertEqual(response.status_code, 409)

    def test_sse_emits_meta_turns_and_result(self):
        names = []
        with CLIENT.stream('GET', '/api/plan/stream',
                           params={'query': '带爸妈去九寨沟玩一天', 'mode': 'rule'}) as response:
            self.assertEqual(response.status_code, 200)
            for line in response.iter_lines():
                if line.startswith('event:'):
                    names.append(line.split(':', 1)[1].strip())
        self.assertEqual(names[0], 'meta')
        self.assertEqual(names[-1], 'result')
        self.assertGreaterEqual(names.count('turn'), 5)

    def test_static_page_is_served(self):
        response = CLIENT.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('行迹', response.text)
        self.assertIn('./app.js', response.text)


if __name__ == '__main__':
    unittest.main()


@unittest.skipIf(CLIENT is None, SKIP or '')
class AuthTests(unittest.TestCase):
    def setUp(self):
        self._old = api.ACCESS_TOKEN

    def tearDown(self):
        api.ACCESS_TOKEN = self._old

    def test_access_token_required_when_configured(self):
        api.ACCESS_TOKEN = 'secret'
        self.assertEqual(CLIENT.get('/api/health').status_code, 401)
        self.assertEqual(CLIENT.get('/api/health',
                                    headers={'Authorization': 'Bearer secret'}).status_code, 200)
        self.assertEqual(CLIENT.get('/api/health?token=secret').status_code, 200)

    def test_open_when_token_unset(self):
        api.ACCESS_TOKEN = ''
        self.assertEqual(CLIENT.get('/api/health').status_code, 200)
