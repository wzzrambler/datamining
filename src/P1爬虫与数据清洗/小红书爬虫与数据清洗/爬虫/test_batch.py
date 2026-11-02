import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src import batch, crawler
from src.archive import Archive


class BatchTests(unittest.TestCase):
    def setUp(self):
        root = crawler.BASE / '.test-tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)
        self.config = self.root / 'tasks.json'

    def tearDown(self):
        self.temp.cleanup()

    def write(self, tasks):
        self.config.write_text(json.dumps(tasks, ensure_ascii=False), encoding='utf-8')

    def test_later_blank_task_prevents_any_network_task(self):
        self.write([{'keyword': '旅行'}, {'keyword': ''}])
        with patch.object(crawler, 'main') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main(['--config', str(self.config)]), 2)
        run.assert_not_called()

    def test_preview_and_arguments_preserve_individual_settings(self):
        self.write([{'keyword': '广东旅游景点', 'target_count': 15, 'wait': {'min_seconds': 20},
                     'budget': {'max_requests': 9000, 'max_minutes': 120}}])
        tasks = batch.load_tasks(self.config)
        args = crawler.parser().parse_args(batch.task_argv(tasks[0]))
        self.assertEqual((args.keyword, args.limit, args.min_wait, args.max_requests, args.max_minutes),
                         ('广东旅游景点', 15, 20, 9000, 120))
        self.assertEqual(args.profile, 'check_20261030')
        self.assertTrue(args.details)
        self.assertIsNone(args.total_requests)
        with patch.object(crawler, 'main') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main(['--config', str(self.config), '--check']), 0)
        run.assert_not_called()

    def test_batch_stops_before_next_keyword_on_error(self):
        self.write([{'keyword': k, 'target_count': 15} for k in ('A', 'B', 'C')])
        with patch.object(crawler, 'OUT', self.root / 'output'), patch.object(crawler, 'main', side_effect=[0, 2]) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main(['--config', str(self.config)]), 2)
            self.assertEqual(run.call_count, 2)
            report = json.loads(next((self.root/'output').glob('batch_report_*.json')).read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'stopped')
            self.assertEqual(len(report['tasks']), 2)

    def test_three_keywords_each_receive_target_fifteen(self):
        self.write([{'keyword': k, 'target_count': 15} for k in ('A', 'B', 'C')])
        with patch.object(crawler, 'OUT', self.root / 'output'), patch.object(crawler, 'main', return_value=0) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main(['--config', str(self.config)]), 0)
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertEqual(crawler.parser().parse_args(call.args[0]).limit, 15)

    def test_bad_wait_budget_types_and_duplicates_rejected(self):
        cases = [[{'keyword': 'A', 'budget': {'max_requests': True}}],
                 [{'keyword': 'A', 'wait': {'min_seconds': 1}}],
                 [{'keyword': 'A', 'wait': {'max_seconds': float('nan')}}],
                 [{'keyword': 'A'}, {'keyword': ' A '}],
                 [{'keyword': 'A', 'target_count': 15.5}]]
        for case in cases:
            self.write(case)
            with self.assertRaises(ValueError):
                batch.load_tasks(self.config)

    def test_split_archive_keeps_search_and_detail_paths_and_bytes(self):
        archive = Archive(self.root / 'output/keyword/archive', '旅行', search_root=self.root/'output/search_html/keyword')
        search = archive.begin('search')
        raw = b'<html>original search</html>'
        saved = archive.response('https://www.xiaohongshu.com/search_result', 200, 'document', 'text/html', raw)
        self.assertTrue(search.is_relative_to(self.root/'output/search_html'))
        self.assertEqual(saved.read_bytes(), raw)
        detail = archive.begin('detail', 'abc')
        self.assertTrue(detail.is_relative_to(self.root/'output/keyword'))
        self.assertFalse(detail.is_relative_to(self.root/'output/search_html'))

    def test_source_move_keeps_original_profile_and_state_root(self):
        self.assertEqual(crawler.BASE, Path(__file__).resolve().parent)
        self.assertEqual(crawler.parser().parse_args([]).state_dir, crawler.BASE/'.state')


if __name__ == '__main__':
    unittest.main()
