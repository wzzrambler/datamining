"""句子定位的边界情况。

原来用 next() 直接找"引用位置落在哪一句"，句子是按句末标点切出来的，
引号串、没有句末标点的尾巴都会掉进缝隙里，next() 找不到就抛
StopIteration，一篇文档连带整轮 P4 一起中断。
"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / 'src/P4共指消解'
sys.path.insert(0, str(BASE))
from v2 import sent_index

SENTS = [{'start': 0, 'end': 10}, {'start': 10, 'end': 20}, {'start': 20, 'end': 30}]


class SentIndexTests(unittest.TestCase):
    def test_inside(self):
        self.assertEqual(sent_index(SENTS, 0), 0)
        self.assertEqual(sent_index(SENTS, 15), 1)
        self.assertEqual(sent_index(SENTS, 29), 2)

    def test_gap_falls_back_to_previous(self):
        # 25 之后文档还有字但切分没覆盖（比如引号里的话），退回前一句而不是抛异常
        self.assertEqual(sent_index(SENTS, 35), 2)
        self.assertEqual(sent_index([{'start': 0, 'end': 10}], 99), 0)

    def test_before_first_sentence(self):
        self.assertEqual(sent_index([{'start': 5, 'end': 12}], 1), 0)

    def test_single_sentence(self):
        self.assertEqual(sent_index([{'start': 0, 'end': 100}], 50), 0)

    def test_reference_beyond_text_does_not_raise(self):
        for pos in (0, 9, 10, 30, 10_000):
            with self.subTest(pos=pos):
                self.assertIsInstance(sent_index(SENTS, pos), int)


if __name__ == '__main__':
    unittest.main()
