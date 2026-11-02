"""正式规则边界及用户口径验证。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P2实体识别/src'))
from p2_entities.config import PHASE, load_config
from p2_entities.extractor import Extractor
from p2_entities.pipeline import run, validate_entities
from p2_entities.time_rules import definitions, extract_times
from test_entities import document, entry, engine


def official_engine(rows):
    old = engine(rows)
    old.config['time_rules'] = definitions()
    return Extractor(old.manifest, old.config, [])


class OfficialRulesTests(unittest.TestCase):
    def hits(self, text):
        return [(text[m['start']:m['end']], m['subtype']) for m in extract_times(text, definitions())]

    def test_colon_label_and_complete_date(self):
        self.assertEqual(self.hits('演出时间:19:30，旺季(至11月15日)'), [('19:30', 'clock'), ('旺季', 'period'), ('11月15日', 'date')])
        self.assertEqual(self.hits('25:00，13:99，2023-02-29'), [])

    def test_core_time_values_keep_original_context(self):
        doc = document('23:59前；提前7-14天；下午五点以后；约2h；30分钟一场')
        out, _ = official_engine([]).extract(doc)
        self.assertEqual([(m['text'], m['subtype']) for m in out['mentions']],
                         [('23:59', 'clock'), ('7-14天', 'duration'), ('下午五点', 'clock'), ('2h', 'duration'), ('30分钟', 'interval')])
        self.assertEqual(out['mentions'][1]['time_context']['prefix'], '提前')
        self.assertEqual(out['mentions'][2]['time_context']['suffix'], '以后')
        validate_entities({'documents': [out]}, [doc])

    def test_day_ordinal_not_duration_substring(self):
        self.assertEqual(self.hits('第一日，一日精华游'), [('第一日', 'day_index'), ('一日', 'duration')])

    def test_preserve_walking_but_filter_literary_substring(self):
        out, _ = official_engine([entry('步行', label='TRANSPORT'), entry('徒步', label='TRANSPORT')]).extract(
            document('细步行走，步行走到北门，徒步去看瀑布'))
        self.assertEqual([m['text'] for m in out['mentions']], ['步行', '徒步'])

    def test_morning_not_transport_cue(self):
        ex = official_engine([entry('小火车', label='TRANSPORT', guard='dual_mobility')])
        a, _ = ex.extract(document('上午必冲小火车'))
        b, _ = ex.extract(document('乘坐小火车前往北门'))
        c, notes = ex.extract(document('北门→小火车→青龙山'))
        self.assertEqual((a['mentions'][1]['type'], a['mentions'][1]['subtype']), ('POI', 'ride'))
        self.assertEqual(b['mentions'][0]['type'], 'TRANSPORT')
        self.assertEqual(c['mentions'], [])
        self.assertTrue(notes)

    def test_literal_list_names_not_inner_fragments(self):
        rows = [entry('花海', guard='route_label', canonical=''), entry('熊森林', guard='route_label', canonical='')]
        out, _ = official_engine(rows).extract(document('五花海、花海、双龙海；母熊森林；北门→熊森林→南门'))
        self.assertEqual([m['text'] for m in out['mentions']], ['花海', '熊森林'])


    def test_unverified_third_party_changes_not_applied(self):
        manifest, config = load_config()
        self.assertEqual(manifest['config_version'], 'v2')
        pois = {r['term'] for d in config['lexicon'].values() for r in d['entries'] if r['type'] == 'POI' and r['enabled']}
        self.assertIn('大夫森林公园', pois)
        self.assertNotIn('大金铃海', pois)
        self.assertFalse(pois & {'大象传奇', '白虎跳水', '非洲发现', '海洋保卫战', '烟花秀', '虎鲸之约', '宇宙世界'})



if __name__ == '__main__':
    unittest.main()
