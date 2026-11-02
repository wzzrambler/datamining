import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/P6知识图谱"))
from p6_quality.times import clock_intervals, summarize_times


class HistoricalTimesTests(unittest.TestCase):
    def test_cross_document_union_keeps_conditions_and_itinerary(self):
        facts = []
        records = {str(i): {"provenance": {"doc_id": f"DOC-{i}"}, "package_key": f"PKG-{i}"} for i in range(5)}
        for i, (text, attr, cond) in enumerate([
            ("09:00—12:00", "opening_time", None), ("11:00—17:00", "opening_time", None),
            ("18:00—20:00", "opening_time", {"text": "节假日"}),
            ("视客流逐步开放", "opening_time", None), ("10:00", "arrival_time", None)]):
            facts.append({"entity_id": "POI-A", "attribute": attr, "value_text": text,
                          "condition": cond, "modality": "asserted", "route_id": None,
                          "step_id": None, "observation_ids": [str(i)]})
        groups = summarize_times(facts, records)
        self.assertEqual(len(groups), 2)
        opening = next(g for g in groups if g["attribute"] == "opening_time")
        self.assertEqual(len(opening["observations"]), 4)
        unconditioned = next(g for g in opening["interval_unions"] if g["condition"] is None)
        self.assertEqual(unconditioned["intervals"], [{"start": "09:00", "end": "17:00", "observation_ids": ["0", "1"]}])
        self.assertEqual(opening["observations"][3]["normalization"], "text_preserved")
        arrival = next(g for g in groups if g["attribute"] == "arrival_time")
        self.assertEqual(arrival["interval_unions"], [])

    def test_overnight_and_qualified_ranges(self):
        self.assertEqual(clock_intervals("22:00—02:00"), [(1320, 1440), (0, 120)])
        self.assertEqual(clock_intervals("22:00—00:00"), [(1320, 1440)])
        for text in ["周末09:00—17:00", "09:00—25:00", "09:00—09:00", "上午9点到下午5点"]:
            self.assertIsNone(clock_intervals(text))

    def test_excluded_fragment_never_becomes_global_clock_union(self):
        fact = {'entity_id':'P','attribute':'opening_time','value_text':'09:00—17:00',
                'condition':None,'modality':'asserted','route_id':None,'step_id':None,
                'scope_status':'excluded_route_fragment_scope','upstream_route_id':'OLD',
                'scenic_code':'JZG','observation_ids':['O']}
        result = summarize_times([fact],{'O':{'provenance':{'doc_id':'D'},'package_key':'PKG'}})
        self.assertEqual(result[0]['interval_unions'],[])
        self.assertEqual(result[0]['observations'][0]['scope']['upstream_route_id'],'OLD')

    def test_conditional_availability_is_retained_without_fabricating_clock_ranges(self):
        records = {"OBS": {"provenance": {"doc_id": "DOC"}, "package_key": "PKG"}}
        rules = [{"rule_id": "RULE", "statement_kind": "conditional_fact", "condition": {"text": "视客流"},
                  "consequence_text": "餐厅逐步开放", "force": "descriptive",
                  "targets": [{"kind": "POI", "id": "POI-A"}, {"kind": "POI", "id": "POI-B"}], "observation_ids": ["OBS"]},
                 {"rule_id": "RULE-NO", "statement_kind": "conditional_restriction", "condition": {"text": "下雨"},
                  "consequence_text": "缆车不开放", "force": "prohibited",
                  "targets": [{"kind": "TRANSPORT", "id": "MODE-A"}], "observation_ids": ["OBS"]}]
        result = summarize_times([], records, rules)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(not g["interval_unions"] for g in result))
        self.assertTrue(all(g["observations"][0]["origin_kind"] == "conditional_rule" for g in result))
        self.assertEqual(next(g for g in result if g["target_id"] == "MODE-A")["observations"][0]["modality"], "prohibited")
