"""P7 旅游规划 Agent 的离线测试。

Agent 读的是项目2/6 的产物，所以这些用例属于集成测试：
产物不在（比如只 checkout 了代码）就整体跳过，而不是伪造数据让它假通过。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'P7旅游规划Agent'))

try:
    import p7_agent as agent
except FileNotFoundError as exc:      # 产物缺失
    agent = None
    SKIP_REASON = "缺少 P7 依赖的产物：%s" % exc
else:
    SKIP_REASON = ""


@unittest.skipIf(agent is None, SKIP_REASON)
class ToolContractTests(unittest.TestCase):
    """五个工具的返回结构是 Agent 决策的依据，字段缺一个策略就会走错分支。"""

    def test_check_area_reports_route_count(self):
        out = agent.check_area("九寨沟风景名胜区")
        self.assertTrue(out["exists"])
        self.assertGreater(out["routes"], 0)

    def test_check_area_unknown_place(self):
        out = agent.check_area("三亚")
        self.assertFalse(out["exists"])
        self.assertEqual(out["routes"], 0)

    def test_get_route_returns_steps_and_span(self):
        out = agent.get_route("广州长隆旅游度假区", days=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["step_count"], len(out["steps"]))
        self.assertTrue(all("text" in s and "to" in s for s in out["steps"]))

    def test_get_route_marks_coarse_route(self):
        # 步数少于 5 只到园区级，撑不起两天的安排
        for route in agent.ROUTES:
            out = agent.get_route(route["scenic_area"])
            self.assertEqual(out["coarse"], out["step_count"] < 5)

    def test_spots_are_inside_the_area(self):
        # 项目2 里 scope=outside 的餐厅（成都天府店之类）不该出现在九寨沟景点里
        out = agent.search_spots("九寨沟风景名胜区", top_k=20)
        self.assertGreater(out["hit"], 0)
        for name in out["spots"]:
            self.assertNotIn("天府店", name)
            self.assertNotIn("成都", name)

    def test_tips_carry_condition(self):
        out = agent.get_tips("九寨沟风景名胜区", limit=5)
        self.assertGreater(out["count"], 0)
        self.assertTrue(all(isinstance(t, str) and t for t in out["tips"]))

    def test_tailor_keeps_every_step(self):
        route = agent.get_route("九寨沟风景名胜区")
        out = agent.tailor_route(route, "老人")
        # 裁剪只打标不删步，删了路线就断了
        named = [s for s in route["steps"] if s["to"]]
        self.assertEqual(out["count"], len(named))
        self.assertLessEqual(out["tagged"], out["count"])


@unittest.skipIf(agent is None, SKIP_REASON)
class LoopTests(unittest.TestCase):
    """七个场景的出口是 Agent 的主要行为，状态错了比答案难看更严重。"""

    def status(self, question):
        return agent.Agent(question).run()["status"]

    def test_exits(self):
        cases = {
            "带爸妈去九寨沟玩一天，他们走不动太久": {"ok", "partial"},
            "去三亚玩三天": {"no_data"},
            "帮我规划一下": {"need_info"},
            "一天想把广州长隆和珠海长隆都玩了": {"conflict"},
        }
        for q, allowed in cases.items():
            with self.subTest(q=q):
                self.assertIn(self.status(q), allowed)

    def test_does_not_loop_forever(self):
        run = agent.Agent("九寨沟玩两天，怎么安排", max_turns=3).run()
        self.assertLessEqual(len(run["turns"]), 4)   # 0 号是槽位解析

    def test_tool_failure_does_not_crash(self):
        # 回归：早先工具抛异常后 _brief 仍按正常字段取值，会话在记录环节崩掉
        a = agent.Agent("带爸妈去九寨沟玩一天")
        a._brief("check_area", {"ok": False, "error": "connection reset"})
        a._brief("get_route", {"ok": False, "reason": "没有解析出结构化路线"})
        a._brief("tailor_route", {"ok": False})

    def test_no_period_placeholder_in_answer(self):
        run = agent.Agent("带爸妈去九寨沟玩一天，他们走不动太久").run()
        for bad in ("【未说明】", "【unspecified】", "【None】"):
            self.assertNotIn(bad, run["answer"])


@unittest.skipIf(agent is None, SKIP_REASON)
class NormalizeTests(unittest.TestCase):
    """项目3/6 的路线按证据存，摊平成线性步骤的规则单独测一遍。"""

    def test_falls_back_to_evidence_text(self):
        raw = {"route_id": "R1", "scenic_code": "JZG",
               "granularity": "in_park",
               "steps": [{"poi_refs": [], "transport_refs": [],
                          "time": {"period": "上午"},
                          "evidence": {"text": "走到镜海边上"}}]}
        out = agent._norm_route(raw)
        self.assertEqual(out["steps"][0]["to"], "走到镜海边上")
        self.assertEqual(out["scenic_area"], "九寨沟风景名胜区")

    def test_transport_comes_from_first_ref(self):
        raw = {"route_id": "R2", "scenic_code": "GZCL", "granularity": "in_park",
               "steps": [{"poi_refs": [{"poi_role": "attraction", "canonical_name": "熊猫村"}],
                          "transport_refs": [{"text": "缆车。"}],
                          "time": {"start_minute": 570}, "evidence": {"text": "坐缆车去熊猫村"}}]}
        out = agent._norm_route(raw)
        self.assertEqual(out["steps"][0]["transport"], "缆车")
        self.assertEqual(out["time_start"], "09:30")

    def test_empty_route_has_no_span(self):
        out = agent._norm_route({"route_id": "R3", "scenic_code": "ZHCL",
                                 "granularity": "unknown", "steps": []})
        self.assertEqual(out["step_count"], 0)
        self.assertEqual(out["time_start"], "")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(agent is None, SKIP_REASON)
class CapacityTests(unittest.TestCase):
    """路线步数与天数必须对得上，不能把 15 步塞进一天。"""

    def _steps(self, n):
        return [{"step": i + 1, "period": "上午", "time": "", "spot": "P%d" % i,
                 "transport": "", "tag": ""} for i in range(n)]

    def test_over_capacity_is_split_into_more_days(self):
        run = agent.Agent("带爸妈去九寨沟玩一天，他们走不动太久").run()
        plan = run["plan"]
        self.assertGreaterEqual(plan["days_actual"], 2)
        self.assertLessEqual(plan["days_actual"], 3)
        self.assertIn("【第1 天】", run["answer"])
        self.assertIn("【第2 天】", run["answer"])
        self.assertIn("走不完", "；".join(plan["notes"]))

    def test_split_preserves_order_and_covers_all_steps(self):
        steps = self._steps(15)
        groups = agent.split_days(steps, 1)
        self.assertEqual([s["step"] for g in groups for s in g], list(range(1, 16)))
        for g in groups:
            self.assertLessEqual(len(g), agent.MAX_STEPS_PER_DAY)

    def test_requested_days_are_honored_when_enough_capacity(self):
        groups = agent.split_days(self._steps(8), 2)
        self.assertEqual(len(groups), 2)
        self.assertEqual(sum(len(g) for g in groups), 8)

    def test_empty_and_single_day(self):
        self.assertEqual(agent.split_days([], 1), [])
        self.assertEqual(len(agent.split_days(self._steps(8), 1)), 1)
