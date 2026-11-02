"""验证真实风险：时间边界、同名入口、来源隔离、位置稳定和无伪造实体。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P2实体识别/src'))
from p2_entities.extractor import Extractor
from p2_entities.pipeline import validate_entities, validate_input
from p2_entities.time_rules import definitions, extract_times


def document(*paragraphs, title=''):
    result, offset = [], 0
    for i, text in enumerate(paragraphs, 1):
        result.append({'i': i, 'start': offset, 'end': offset + len(text), 'text': text})
        offset += len(text) + 1
    return {'doc_id': 'GZCL-TEST', 'scenic_code': 'GZCL', 'scenic_area': '广州长隆旅游度假区',
            'source_platform': '测试', 'text_type': 'travel_note', 'title': title,
            'content': '\n'.join(paragraphs), 'paragraphs': result}


def entry(term, label='POI', role='attraction', subtype='spot', guard='named', canonical=None):
    return {'term': term, 'type': label, 'poi_role': role if label == 'POI' else '',
            'subtype': subtype if label == 'POI' else 'mode', 'guard': guard, 'enabled': True,
            'scope': 'in_area' if label == 'POI' else '', 'canonical_name': term if canonical is None else canonical,
            'rule_id': term + label}


def engine(rows, origins=()):
    config = {'lexicon': {'GZCL': {'entries': rows, 'review_terms': [], 'excluded': []}},
              'time_rules': definitions(), 'policy': {'context_paragraphs': 3, 'context_max_chars': 500}}
    return Extractor({'config_hash': 'test'}, config, list(origins))


class TimeRulesTests(unittest.TestCase):
    def hits(self, text):
        return [(text[r['start']:r['end']], r['subtype']) for r in extract_times(text, definitions())]

    def test_complete_ranges_and_approximate_duration(self):
        self.assertEqual(self.hits('09:30-18:00；15-20分钟；约2h'),
                         [('09:30-18:00', 'clock_range'), ('15-20分钟', 'duration'), ('2h', 'duration')])

    def test_chinese_clock_and_day_not_step(self):
        self.assertEqual(self.hits('上午九点半，下午五点，第三站，第二天，Day 2'),
                         [('上午九点半', 'clock'), ('下午五点', 'clock'), ('第二天', 'day_index'), ('Day 2', 'day_index')])

    def test_dates_and_non_time_numbers(self):
        self.assertEqual(self.hits('3150m；280元；6层；4.1万人；3/7号线；2024-06-03'), [('2024-06-03', 'date')])

    def test_date_range_relative_and_interval(self):
        self.assertEqual(self.hits('10月下旬到11月初；提前7-14天；30分钟一场；前一天'),
                         [('10月下旬到11月初', 'date_range'), ('7-14天', 'duration'),
                          ('30分钟', 'interval'), ('前一天', 'relative')])

    def test_invalid_times_and_dates_not_sliced(self):
        self.assertEqual(self.hits('25:00，13:99，二十五点，2023-02-29'), [])

    def test_cross_midnight_clock_range(self):
        self.assertEqual(self.hits('23:00-01:00'), [('23:00-01:00', 'clock_range')])

    def test_one_and_half_hours_and_trip_length(self):
        self.assertEqual(self.hits('一个半小时，3天2晚'), [('一个半小时', 'duration'), ('3天2晚', 'duration')])

    def test_degree_and_numbered_points_are_not_clock_times(self):
        self.assertEqual(self.hits('补充一点体验，便宜一点，提前一点排队，有以下两点特别注意'), [])
        self.assertEqual(self.hits('下午一点开园，一点出发'), [('下午一点', 'clock'), ('一点', 'clock')])


class EntityTests(unittest.TestCase):
    def test_longest_name_suppresses_inner_mode_and_poi(self):
        ex = engine([entry('长隆野生动物世界北门', role='support', subtype='gate'),
                     entry('北门', role='support', subtype='gate', guard='gate'),
                     entry('汉溪长隆地铁站', role='support', subtype='station'),
                     entry('地铁', label='TRANSPORT')])
        doc = document('到汉溪长隆地铁站，坐地铁去长隆野生动物世界北门')
        out, _ = ex.extract(doc)
        self.assertEqual([x['text'] for x in out['mentions']], ['汉溪长隆地铁站', '地铁', '长隆野生动物世界北门'])

    def test_gate_context_isolation_across_images(self):
        doc = document('长隆欢乐世界游览路线', '北门进园')
        rows = [entry('北门', role='support', subtype='gate', guard='gate')]
        separate = [{'doc_id': doc['doc_id'], 'para_id': 1, 'kind': 'image', 'source': '1.png'},
                    {'doc_id': doc['doc_id'], 'para_id': 2, 'kind': 'image', 'source': '2.png'}]
        out, notes = engine(rows, separate).extract(doc)
        self.assertEqual(out['mentions'], [])
        self.assertTrue(notes)
        same = deepcopy(separate)
        same[1]['source'] = '1.png'
        out, _ = engine(rows, same).extract(doc)
        self.assertEqual(out['mentions'][0]['canonical_name'], '长隆欢乐世界北门')

    def test_same_gate_different_parks_not_aligned(self):
        rows = [entry('北门', role='support', subtype='gate', guard='gate')]
        a, _ = engine(rows).extract(document('北门入园', title='长隆欢乐世界攻略'))
        b, _ = engine(rows).extract(document('北门入园', title='野生动物世界攻略'))
        self.assertNotEqual(a['mentions'][0]['entity_id'], b['mentions'][0]['entity_id'])

    def test_activity_and_animal_not_invented(self):
        rows = [entry('海豚剧场'), entry('海底互动船', subtype='ride')]
        out, _ = engine(rows).extract(document('看海豚、白鲸和海狮三个剧场，乘坐海底互动船，看烟花秀'))
        self.assertEqual([(m['text'], m['type']) for m in out['mentions']], [('海底互动船', 'POI')])

    def test_dual_mode_requires_context(self):
        ex = engine([entry('小火车', label='TRANSPORT', guard='dual_mobility')])
        a, _ = ex.extract(document('坐小火车去北门'))
        b, _ = ex.extract(document('小火车是必玩项目'))
        c, notes = ex.extract(document('北门→小火车→青龙山'))
        self.assertEqual(a['mentions'][0]['type'], 'TRANSPORT')
        self.assertEqual((b['mentions'][0]['type'], b['mentions'][0]['poi_role']), ('POI', 'attraction'))
        self.assertEqual(c['mentions'], [])
        self.assertTrue(notes)

    def test_fullwidth_and_spacing_preserve_original_offsets(self):
        doc = document('先去４Ｄ 影院，再去4D影院，09：30出发')
        out, _ = engine([entry('4D影院')]).extract(doc)
        self.assertEqual([m['text'] for m in out['mentions']], ['４Ｄ 影院', '4D影院', '09：30'])
        for m in out['mentions']:
            self.assertEqual(doc['content'][m['start']:m['end']], m['text'])

    def test_repeated_mentions_ids_and_determinism(self):
        doc = document('五花海→五花海', '下午再去五花海')
        ex = engine([entry('五花海')])
        a, _ = ex.extract(doc)
        b, _ = ex.extract(doc)
        self.assertEqual(a, b)
        pois = [x for x in a['mentions'] if x['type'] == 'POI']
        self.assertEqual(len(pois), 3)
        self.assertEqual(len({m['mention_id'] for m in pois}), 3)

    def test_ocr_line_does_not_join_into_new_name(self):
        out, _ = engine([entry('五花海')]).extract(document('五花', '海'))
        self.assertEqual(out['mentions'], [])

    def test_input_and_output_tamper_detected(self):
        doc = document('五花海')
        validate_input([doc])
        out, _ = engine([entry('五花海')]).extract(doc)
        validate_entities({'documents': [out]}, [doc])
        changed = deepcopy(out)
        changed['mentions'][0]['end'] -= 1
        with self.assertRaises(ValueError):
            validate_entities({'documents': [changed]}, [doc])
        doc['paragraphs'][0]['start'] = 1
        with self.assertRaises(ValueError):
            validate_input([doc])


if __name__ == '__main__':
    unittest.main()
