"""图片 OCR 复读作者正文时的跨来源去重。"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from src.ocr import write_json
from src.pipeline import build_document, load
from src.rules import grams, repeats_body, squash

TEST_TMP = BASE / '.test_tmp'
TEST_TMP.mkdir(exist_ok=True)
tempfile.tempdir = str(TEST_TMP)
TEST_CONFIG = load(BASE / 'config.production.json')

BODY = ('1 准备物品\n'
        '官方APP——长隆旅游(看排队时长)\n'
        '10:00 鲸鲨馆——海鸟世界——北极熊馆——企鹅馆——海狮剧场\n'
        '13:00 雨林仙踪——金蛇秘境——考拉园——熊猫村')


def ocr_result(texts):
    return {'status': 'cached',
            'response': {'request_id': 'test', 'image': {'width': 12, 'height': 8},
                         'detected_boxes': len(texts),
                         'lines': [{'text': t, 'confidence': .98, 'box': [[0, 0], [10, 0], [10, 7], [0, 7]]}
                                   for t in texts],
                         'timings_ms': {}}}


class RepeatTests(unittest.TestCase):
    def setUp(self):
        self.body = squash(BODY)
        self.grams = grams(self.body)

    def test_verbatim_line_is_repeat(self):
        self.assertTrue(repeats_body(squash('10:00 鲸鲨馆——海鸟世界——北极熊馆'), self.body, self.grams))

    def test_one_wrong_character_still_counts(self):
        # 截图字迹和正文有出入时，OCR 常错一两个字，覆盖率仍然够高。
        line = squash('13:00 雨林仙踪——金蛇秘境——考拉园——熊猫材')
        self.assertTrue(repeats_body(line, self.body, self.grams))

    def test_new_information_is_kept(self):
        self.assertFalse(repeats_body(squash('17:00 烟花表演在横琴海'), self.body, self.grams))
        self.assertFalse(repeats_body(squash('门票平日 350 元'), self.body, self.grams))

    def test_three_character_fragment_is_left_alone(self):
        # 三个字太短，撞上正文的概率不低，宁可留着交给下游的人工校验。
        self.assertFalse(repeats_body(squash('海鸟世'), self.body, self.grams))


class BuildDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = copy.deepcopy(TEST_CONFIG)
        self.group = self.config['groups'][0]
        self.note = {'note_id': 'a'*24, 'author_id': 'b'*24, 'title': '测试',
                     'content': BODY, 'content_scope': 'detail',
                     'url': 'https://www.xiaohongshu.com/explore/' + 'a'*24,
                     'published_at': 1790693062000}
        self.ocr = Mock()

    def tearDown(self):
        if not self.root.resolve().is_relative_to(TEST_TMP.resolve()):
            raise ValueError('拒绝清理项目测试目录之外的路径')
        self.temp.cleanup()

    def add_images(self, results):
        files = []
        for i in range(1, len(results) + 1):
            (self.root/f'{i}.png').write_bytes(b'placeholder')
            files.append({'index': i, 'variants': [{'file': f'{i}.png'}]})
        write_json(self.root/'images.json', {'missing_count': 0, 'images': files})
        self.note['image_manifest'] = str(self.root/'images.json')
        self.ocr.recognize.side_effect = [ocr_result(r) for r in results]
        return build_document(self.note, self.group, self.config, self.ocr, 'ZHCL-NOTE-04', self.root)

    def test_screenshot_of_the_same_itinerary_does_not_duplicate(self):
        row, detail, _ = self.add_images([[BODY, '17:00 烟花表演在横琴海']])
        self.assertEqual(row['content'].count('鲸鲨馆'), 1)
        self.assertEqual(row['content'].count('考拉园'), 1)
        self.assertIn('17:00 烟花表演在横琴海', row['content'])
        self.assertEqual(detail['parts'][1]['ocr_repeat_lines'], 4)

    def test_fully_repeated_image_is_marked_and_counted(self):
        repeated = ['10:00 鲸鲨馆——海鸟世界——北极熊馆——企鹅馆——海狮剧场',
                    '13:00 雨林仙踪——金蛇秘境——考拉园——熊猫村']
        row, detail, origins = self.add_images([repeated])
        self.assertEqual(detail['parts'][1]['included'], False)
        self.assertEqual(detail['parts'][1]['exclude_reason'], '图片文字与作者正文重复')
        self.assertGreaterEqual(row['dup_line_removed'], 2)
        self.assertEqual([o['kind'] for o in origins], ['body'] * 4)


if __name__ == '__main__':
    unittest.main()
