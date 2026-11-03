"""航空语境判定：别把"飞快""飞过去"当成航段，也别只认几个城市。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P2实体识别/src'))
from p2_entities.extractor import AVIATION_CITIES, aviation_destination

PLACES = ('某某沟', '某某山谷')


class DestinationTests(unittest.TestCase):
    def test_common_cities(self):
        for rest in ('成都双流机场', '广州白云', '九寨沟黄龙机场', '重庆，然后转车'):
            with self.subTest(rest=rest):
                self.assertTrue(aviation_destination(rest, PLACES))

    def test_named_in_lexicon_wins(self):
        # 词表里的地名不用靠城市常量，换景区也就自动生效
        self.assertTrue(aviation_destination('某某沟机场', PLACES))

    def test_administrative_suffix(self):
        self.assertTrue(aviation_destination('香格里拉市', ()))
        self.assertTrue(aviation_destination('汶川县', ()))

    def test_plain_verbs_are_not_flights(self):
        for rest in ('快', '快得很', '奔过去', '过去两个小时', '了起来', '向山顶'):
            with self.subTest(rest=rest):
                self.assertFalse(aviation_destination(rest, PLACES))

    def test_place_names_are_not_hardcoded_only(self):
        # 回归：早先只写了六个城市，'丽江'这种就漏；现在至少 20 个常见出发地 + 后缀规则
        self.assertGreaterEqual(len(AVIATION_CITIES), 20)
        self.assertTrue(aviation_destination('丽江古城', ()))

    def test_leading_preposition(self):
        self.assertTrue(aviation_destination('往成都', ()))
        self.assertTrue(aviation_destination('到深圳', ()))


if __name__ == '__main__':
    unittest.main()
