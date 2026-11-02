import copy
from email.parser import BytesParser
from email.policy import default
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PIL import Image
from openpyxl import load_workbook

from src.ocr import OCRClient, write_json
from src.pipeline import FIELDS, build_document, load, run, validate_config
from src.rules import clean_text, count_sentences, effective_len, normalize_legacy_documents

BASE = Path(__file__).resolve().parents[1]
TEST_TMP = BASE / '.test_tmp'
TEST_TMP.mkdir(exist_ok=True)
# Windows 沙箱系统 TEMP 的写权限不稳定；所有测试临时文件限定在项目内。
tempfile.tempdir = str(TEST_TMP)
TEST_CONFIG = load(BASE / 'config.production.json')


def multipart(prepared):
    message = BytesParser(policy=default).parsebytes(('Content-Type: ' + prepared.headers['Content-Type'] + '\r\n\r\n').encode() + prepared.body)
    return {p.get_param('name', header='content-disposition'): p for p in message.iter_parts()}


def response(width=12, height=8, text='测试文本', request_id='test'):
    return {'request_id': request_id, 'image': {'width': width, 'height': height},
            'detected_boxes': 1, 'lines': [{'text': text, 'confidence': .98,
                                        'box': [[0, 0], [10, 0], [10, 7], [0, 7]]}], 'timings_ms': {}}


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = copy.deepcopy(TEST_CONFIG)
        self.group = self.config['groups'][0]
        self.note = {'note_id': 'a'*24, 'author_id': 'b'*24, 'title': '测试',
                     'content': '上午9:00 东门→湖边', 'content_scope': 'detail',
                     'url': 'https://www.xiaohongshu.com/explore/' + 'a'*24,
                     'published_at': 1790693062000}
        self.ocr = Mock()

    def tearDown(self):
        if not self.root.resolve().is_relative_to(TEST_TMP.resolve()):
            raise ValueError('拒绝清理项目测试目录之外的路径')
        self.temp.cleanup()

    def build(self):
        return build_document(self.note, self.group, self.config, self.ocr, 'GZCL-NOTE-01', self.root)

    def comments(self, rows):
        rows = [{'note_id': self.note['note_id'], 'comment_id': str(i),
                 'author_id': author, 'content': content} for i, (author, content) in enumerate(rows)]
        write_json(self.root/'comments.json', rows)
        self.note['comment_manifest'] = str(self.root/'index.json')

    def test_clean_preserves_route_and_time(self):
        text, stats = clean_text('<p>上午９：００ 东门→湖边</p>\n#景区[话题]#\n上午９：００ 东门→湖边\nhttps://example.com/x\u200b')
        self.assertEqual(text, '上午09:00 东门→湖边')
        self.assertEqual(stats['url_removed'], 1)
        self.assertEqual(stats['dup_line_removed'], 1)
        self.assertEqual(effective_len('甲，A 1🙂'), 3)

    def test_xhs_markers_preserve_bracketed_travel_information(self):
        text, stats = clean_text('东门→湖边[打卡R][笑哭R][doge][回招呼]🙂\n'
                                 '#九寨沟[话题]#\n[话题]\n'
                                 '[往返时间-游玩路线-行李寄存]\n[视频]\n[未知R]')
        self.assertEqual(text, '东门→湖边\n[往返时间-游玩路线-行李寄存]\n[视频]\n[未知R]')
        self.assertEqual(stats['emoji_removed'], 5)
        self.assertEqual(clean_text(text)[0], text)

    def test_comment_threshold_uses_text_after_xhs_marker_removal(self):
        self.comments([('b'*24, '甲'*30 + '[笑哭R]'), ('b'*24, '乙'*31 + '[派对R]')])
        row, _, _ = self.build()
        self.assertNotIn('甲', row['content'])
        self.assertIn('乙'*31, row['content'])
        self.assertNotIn('[派对R]', row['content'])

    def test_comment_strict_boundary_author_and_other_note(self):
        self.comments([('b'*24, '甲'*30), ('b'*24, '乙'*31), ('c'*24, '丙'*50)])
        row, detail, origins = self.build()
        self.assertNotIn('甲', row['content'])
        self.assertIn('乙'*31, row['content'])
        self.assertNotIn('丙', row['content'])
        self.assertEqual(len(origins), 2)
        self.assertEqual(set(row), set(FIELDS))
        self.assertNotIn(None, row.values())
        for p in row['paragraphs']:
            self.assertEqual(row['content'][p['start']:p['end']], p['text'])

    def test_all_comment_policy_and_dedup(self):
        self.config['comment_policy'] = 'all'
        self.comments([('c'*24, '丙'*31), ('b'*24, '丙'*31)])
        row, detail, _ = self.build()
        self.assertEqual(row['content'].count('丙'*31), 1)
        self.assertFalse(detail['parts'][-1]['included'])

    def test_image_order_threshold_and_provenance(self):
        for i in (1, 2):
            (self.root/f'{i}.png').write_bytes(b'placeholder')
        write_json(self.root/'images.json', {'missing_count': 1, 'images': [
            {'index': 2, 'variants': [{'file': '2.png'}]}, {'index': 1, 'variants': [{'file': '1.png'}]}]})
        self.note['image_manifest'] = str(self.root/'images.json')
        self.ocr.recognize.side_effect = [
            {'status': 'cached', 'response': response(text='甲'*31)},
            {'status': 'cached', 'response': response(text='乙'*30)}]
        row, detail, origins = self.build()
        self.assertIn('甲'*31, row['content'])
        self.assertNotIn('乙', row['content'])
        self.assertEqual(detail['parts'][1]['image_index'], 1)
        self.assertEqual(origins[-1]['kind'], 'image')
        self.assertIn('图片缺失1张', row['quality_flags'])

    def test_tags_only_not_valid_and_raw_length_retained(self):
        self.note['content'] = '#景区[话题]#'
        row, detail, origins = self.build()
        self.assertEqual(row['content'], '')
        self.assertEqual(row['raw_char_len'], len(self.note['content']))
        self.assertEqual(row['is_valid'], 0)
        self.assertEqual(row['paragraphs'], [])

    def test_generated_batch_merge_schema_offsets_and_stable_ids(self):
        cfg = copy.deepcopy(TEST_CONFIG)
        for key in ('raw_archive', 'archive_cache', 'portable_root', 'auxiliary_dir_name', 'quality_dir_name'):
            cfg.pop(key, None)
        old_row, _, _ = self.build()
        write_json(self.root/'legacy/documents.json', [old_row])
        cfg['existing_documents'] = str(self.root/'legacy/documents.json')
        inputs = self.root/'inputs'
        inputs.mkdir()
        image_rows = []
        for i in range(1, 5):
            Image.new('RGB', (12, 8), (i, 0, 0)).save(inputs/f'{i}.png')
            image_rows.append({'index': i, 'variants': [{'file': f'{i}.png'}]})
        write_json(inputs/'images.json', {'images': image_rows, 'missing_count': 0})
        new_note = {**self.note, 'note_id': 'c'*24, 'content': '#景区[话题]#', 'image_manifest': 'images.json'}
        write_json(inputs/'notes.json', [new_note])
        cfg['groups'] = [{**self.group, 'input_dir': str(inputs)}]
        cfg['output_dir'] = str(self.root/'output')
        cfg['ocr']['cache_dir'] = str(self.root/'cache')
        cfg_path = self.root/'config.json'
        write_json(cfg_path, cfg)
        with patch('requests.Session.send', side_effect=AssertionError('禁止联网')):
            first = run(cfg_path)
            docs = load(self.root/'output'/'documents.json')
            second = run(cfg_path)
        old = load(cfg['existing_documents'])
        self.assertEqual(docs[:len(old)], normalize_legacy_documents(old))
        self.assertEqual(first['merged_documents'], len(old)+1)
        self.assertEqual(second['merged_documents'], first['merged_documents'])
        again = load(self.root/'output'/'documents.json')
        self.assertEqual(docs, again)
        self.assertEqual(set(docs[-1]), set(old[0]))
        self.assertEqual(docs[-1]['is_valid'], 0)
        self.assertEqual(load(self.root/'output'/'quality_stats.json')['ocr_status'], {'pending': 4})
        for d in docs:
            for p in d['paragraphs']:
                self.assertEqual(d['content'][p['start']:p['end']], p['text'])
        wb = load_workbook(self.root/'output'/'dataset.xlsx', read_only=True)
        self.assertEqual(wb.sheetnames, ['文档清单', '正文', '段落', '字段说明'])
        self.assertFalse(any(c.data_type == 'f' for ws in wb for row in ws for c in row))
        wb.close()

    def test_bad_config_rejected(self):
        for key, value in [('min_extra_chars', True), ('comment_policy', 'guess')]:
            config = copy.deepcopy(self.config)
            config[key] = value
            with self.assertRaises(ValueError):
                validate_config(config)

    def test_legacy_normalization_is_equivalent_and_idempotent(self):
        row, _, _ = self.build()
        row.update(content='上午９：００，先游览东门！\n接着前往湖边；之后返回→出口。',
                   raw_char_len=2, noise_char_removed=-4)
        original = copy.deepcopy(row)
        normalized = normalize_legacy_documents([row])[0]
        self.assertEqual(row, original)
        self.assertEqual(normalized['content'], '上午9:00,先游览东门!\n接着前往湖边;之后返回→出口。')
        self.assertEqual(normalized['noise_char_removed'], 0)
        self.assertEqual(normalize_legacy_documents([normalized]), [normalized])
        self.assertEqual(normalized['paragraph_count'], 2)
        self.assertEqual(normalized['sentence_count'], count_sentences(normalized['content']))
        for p in normalized['paragraphs']:
            self.assertEqual(normalized['content'][p['start']:p['end']], p['text'])


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root/'test.webp'
        Image.new('RGB', (12, 8), 'white').save(self.image)
        self.client = OCRClient(TEST_CONFIG['ocr'], self.root/'cache', live=True)

    def tearDown(self):
        self.client.close()
        if not self.root.resolve().is_relative_to(TEST_TMP.resolve()):
            raise ValueError('拒绝清理项目测试目录之外的路径')
        self.temp.cleanup()

    def test_webp_multipart_and_success_cache_no_reload(self):
        self.client.session.get = Mock(return_value=Mock(status_code=200, json=lambda: {'status': 'ready'}))
        def post(prepared, **kw):
            self.assertTrue(prepared.url.endswith('/v1/ocr'))
            parts = multipart(prepared)
            payload = parts['image'].get_payload(decode=True)
            self.assertEqual(parts['image'].get_content_type(), 'image/png')
            with Image.open(io.BytesIO(payload)) as image:
                self.assertEqual(image.size, (12, 8))
            self.assertFalse(kw['allow_redirects'])
            data = response(request_id=multipart(prepared)['request_id'].get_payload(decode=True).decode())
            return Mock(status_code=200, text=json.dumps(data), json=lambda: data)
        self.client.session.send = Mock(side_effect=post)
        first = self.client.recognize(self.image)
        second = self.client.recognize(self.image)
        self.assertEqual(first['status'], 'recognized')
        self.assertEqual(second['status'], 'cached')
        self.client.session.get.assert_called_once()
        self.client.session.send.assert_called_once()

    def test_busy_stops_no_retry_and_error_saved(self):
        self.client.ready = True
        self.client.session.send = Mock(return_value=Mock(status_code=429, text='{"error":{"code":"BUSY"}}', json=lambda: {'error': {'code': 'BUSY'}}))
        self.assertEqual(self.client.recognize(self.image)['status'], 'error')
        self.assertEqual(self.client.recognize(self.image)['status'], 'pending')
        self.client.session.send.assert_called_once()
        self.assertEqual(len(list((self.root/'cache'/'errors').glob('*.json'))), 2)

    def test_timeout_stops(self):
        self.client.session.get = Mock(side_effect=requests.Timeout('test timeout'))
        self.assertEqual(self.client.recognize(self.image)['status'], 'error')
        self.assertTrue(self.client.stopped)

    def test_empty_lines_success(self):
        self.client.ready = True
        def post(prepared, **kw):
            data = response(request_id=multipart(prepared)['request_id'].get_payload(decode=True).decode())
            data['lines'] = []
            return Mock(status_code=200, text=json.dumps(data), json=lambda: data)
        self.client.session.send = Mock(side_effect=post)
        self.assertEqual(self.client.recognize(self.image)['response']['lines'], [])

    def test_invalid_response_preserved_and_not_cached(self):
        self.client.ready = True
        self.client.session.send = Mock(return_value=Mock(status_code=200, text='{}', json=lambda: {}))
        result = self.client.recognize(self.image)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(list((self.root/'cache'/'errors').glob('*_raw.json')))

    def test_health_unready_no_post(self):
        self.client.session.get = Mock(return_value=Mock(status_code=503))
        self.client.session.send = Mock()
        self.assertEqual(self.client.recognize(self.image)['status'], 'error')
        self.client.session.send.assert_not_called()

    def test_whole_multipart_limit_includes_overhead(self):
        self.client.ready = True
        self.client.settings = {**self.client.settings, 'max_request_bytes': 200}
        self.client.session.send = Mock()
        result = self.client.recognize(self.image)
        self.assertEqual(result['status'], 'error')
        self.assertIn('multipart', result['reason'])
        self.client.session.send.assert_not_called()

    def test_504_stops_and_preserves_error(self):
        self.client.ready = True
        self.client.session.send = Mock(return_value=Mock(status_code=504, text='{"error":{"code":"TIMEOUT"}}', json=lambda: {'error': {'code': 'TIMEOUT'}}))
        result = self.client.recognize(self.image)
        self.assertEqual(result['status'], 'error')
        self.assertIn('504', result['reason'])
        self.assertEqual(self.client.recognize(self.image)['status'], 'pending')
        self.client.session.send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
