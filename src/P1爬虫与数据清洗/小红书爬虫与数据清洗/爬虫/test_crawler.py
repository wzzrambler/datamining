"""所有测试均为离线测试；不启动浏览器、不访问小红书。"""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import crawler
from src.archive import Archive, choose_step, extend_plan, attach_images
from src.runtime import Guard, Store, Stopped, complete, restriction_reason, response_notice, schedule_pause
from src.interactions import comment_draw
from src.comments import parse_comments


def note(nid='abc', content='', scope='feed_summary'):
    return {**{k: '' for k in crawler.FIELDS}, 'note_id': nid, 'title': '测试',
            'content': content, 'content_scope': scope, 'keyword': '旅游',
            'url': f'https://www.xiaohongshu.com/explore/{nid}'}


class LocalTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parent / '.test-tmp'
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state.sqlite3'
        self.store = Store(self.path)
        self.output_patch = patch.object(crawler, 'OUT', self.root / 'output')
        self.output_patch.start()

    def tearDown(self):
        self.output_patch.stop()
        self.store.close()
        self.temp.cleanup()

    def test_cache_survives_reopen_and_does_not_downgrade(self):
        self.store.upsert([note(content='正文', scope='detail')], '旅游')
        self.store.close()
        self.store = Store(self.path)
        self.store.upsert([note()], '新词')
        self.assertTrue(complete(self.store.rows('新词', 10)[0]))
        self.assertEqual(self.store.rows('旅游', 10)[0]['content'], '正文')

    def test_empty_detail_does_not_erase_content(self):
        self.store.upsert([note(content='正文', scope='detail')], '旅游')
        self.store.upsert([note(scope='detail')], '旅游')
        self.assertEqual(self.store.rows('旅游', 1)[0]['content'], '正文')

    def test_complete_cache_precedes_old_summaries(self):
        self.store.upsert([note('old-summary')], '旅游')
        self.store.upsert([note('completed', content='正文', scope='detail')], '旅游')
        self.assertEqual(self.store.rows('旅游', 1)[0]['note_id'], 'completed')

    def test_request_limit_blocks_before_extra_request_and_persists(self):
        guard = Guard(self.store, 2, 20, 200, 60)
        guard.allow_request()
        guard.allow_request()
        with self.assertRaises(Stopped):
            guard.allow_request()
        self.assertEqual(self.store.counts(), (2, 2))
        self.store.close()
        self.store = Store(self.path)
        next_guard = Guard(self.store, 20, 20, 200, 60)
        with self.assertRaises(Stopped):
            next_guard.check()
        self.store.clear_stop('测试人工确认')
        self.assertEqual(self.store.counts(), (2, 2))

    def test_daily_and_lifetime_limits_carry_across_runs(self):
        self.store.request()
        for daily, total in ((1, 100), (100, 1)):
            self.store.clear_stop('测试')
            guard = Guard(self.store, 10, daily, total, 60)
            with self.assertRaises(Stopped):
                guard.allow_request()
            self.assertEqual(self.store.counts(), (1, 1))

    def test_task_budget_survives_runs_and_keeps_jobs_separate(self):
        for _ in range(2):
            guard = Guard(self.store, 10, 100, None, 60, task_key='travel', task_limit=2)
            guard.allow_request()
        next_guard = Guard(self.store, 10, 100, None, 60, task_key='travel', task_limit=2)
        with self.assertRaises(Stopped):
            next_guard.check_request_budget()
        self.assertEqual(self.store.get('stop')['reason'], 'task_request_budget_exceeded')
        self.assertEqual(self.store.counts(), (2, 2))
        self.store.clear_stop('offline test')
        other = Guard(self.store, 10, 100, None, 60, task_key='other', task_limit=2)
        other.allow_request()
        self.assertEqual(other.task_requests(), 1)
        self.assertEqual(next_guard.task_requests(), 2)

    def test_history_can_be_statistics_only(self):
        self.store.request()
        guard = Guard(self.store, 10, 100, None, 60)
        guard.allow_request()
        self.assertEqual(self.store.counts(), (2, 2))

    def test_share_redirect_keeps_pending_response_folder_valid(self):
        archive = Archive(self.root / 'archive', '景区攻略')
        folder = archive.begin('detail', 'shared_link')
        meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        meta['note_id'] = 'c' * 24
        archive.write_json('manifest.json', meta)
        # 跳转完成前已分配归档目录的响应，完成时仍能写入同一目录。
        saved = archive.response('https://edith.xiaohongshu.com/api/sns/web/v2/comment/page',
                                 200, 'xhr', 'application/json', b'{"data":{"comments":[]}}', folder)
        self.assertTrue(saved.exists())
        self.assertEqual(json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))['note_id'], meta['note_id'])

    def test_incomplete_target_is_not_reported_as_success(self):
        report = {'status': 'ok', 'details_requested': True, 'target_count': 45}
        crawler.save_result([note(content='正文', scope='detail')], report, output=self.root / 'export')
        self.assertEqual(report['status'], 'partial_target')

    def test_runtime_budget_during_wait(self):
        clock = [0]
        guard = Guard(self.store, 10, 10, 10, 60, clock=lambda: clock[0])
        clock[0] = 61
        with self.assertRaises(Stopped):
            guard.check()
        self.assertEqual(self.store.get('stop')['reason'], 'runtime_budget_exceeded')

    def test_pause_branches_and_persistence(self):
        class RNG:
            def __init__(self, probability):
                self.probability = probability
            def uniform(self, a, b):
                return (a + b) / 2
            def random(self):
                return self.probability
        short, extra = schedule_pause(self.store, rng=RNG(.01), wall=lambda: 100)
        self.assertEqual((short, extra), (35, 450))
        self.assertEqual(self.store.get('next_visit_at'), 585)
        schedule_pause(self.store, rng=RNG(.9), wall=lambda: 100)
        self.assertEqual(self.store.get('next_visit_at'), 585)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get('next_visit_at'), 585)

    def test_response_signals_and_no_body_false_positive(self):
        for status in (403, 429):
            self.assertTrue(restriction_reason(status=status))
        for data in ({'success': False}, {'code': 300012}, {'msg': '操作过于频繁'},
                     {'data': {'verify_url': 'verify'}}, {'code': 98765}):
            self.assertTrue(restriction_reason(payload=data))
        self.assertIsNone(restriction_reason(payload={'success': True, 'data': {'desc': '介绍安全验证'}}))

    def test_html_and_text_offline(self):
        html = self.root / 'test.html'
        html.write_text('<title>旅行</title><div id="detail-desc">景点一<br>景点二</div>', encoding='utf-8')
        rows = crawler.offline_rows(html, '旅行')
        self.assertEqual(rows[0]['content'], '景点一\n景点二')
        text = self.root / 'manual.txt'
        text.write_text('景点一\n景点二', encoding='utf-8')
        self.assertTrue(complete(crawler.offline_rows(text, '旅行')[0]))
        html.write_text('<html>请登录</html>', encoding='utf-8')
        self.assertEqual(crawler.offline_rows(html, ''), [])

    def test_import_old_exports_only_once(self):
        out = self.root / 'out'
        out.mkdir()
        (out / 'notes_old.json').write_text(json.dumps([note(content='正文', scope='detail')]), encoding='utf-8')
        with patch.object(crawler, 'OUT', out):
            crawler.bootstrap(self.store)
            crawler.bootstrap(self.store)
        self.assertEqual(len(self.store.rows('keyword:旅游', 10)), 1)

    def test_multiline_csv_and_duplicate_state_preserve_body(self):
        path = self.root / 'notes.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=crawler.FIELDS)
            writer.writeheader()
            writer.writerow(note(content='第一行\n第二行', scope='detail'))
        self.assertEqual(crawler.offline_rows(path, '')[0]['content'], '第一行\n第二行')
        rows = crawler.extract_notes([
            {'id': 'abc', 'title': '标题', 'desc': '正文', 'user': {}},
            {'id': 'abc', 'display_title': '标题', 'user': {}}])
        self.assertEqual(rows[0]['content'], '正文')

    def test_cache_hit_and_stop_never_launch_browser(self):
        self.store.upsert([note(content='正文', scope='detail')], 'keyword:旅游')
        self.store.set('legacy_imported', True)
        self.store.stop('账号警告')
        args = crawler.parser().parse_args(['--keyword', '旅游', '--limit', '1', '--details', '--state-dir', str(self.root)])
        with patch.object(crawler, 'online') as online, patch.object(crawler, 'save_result', return_value=0) as save, contextlib.redirect_stdout(io.StringIO()):
            crawler.run(args)
            online.assert_not_called()
            self.assertEqual(save.call_args.args[1]['status'], 'cache_hit')
            args.keyword = '新词'
            crawler.run(args)
            online.assert_not_called()
            self.assertEqual(save.call_args.args[1]['status'], 'stopped')

    def test_offline_can_run_while_locked_without_network(self):
        self.store.set('legacy_imported', True)
        self.store.stop('账号警告')
        path = self.root / 'manual.txt'
        path.write_text('手动提供的正文', encoding='utf-8')
        args = crawler.parser().parse_args(['--offline', str(path), '--state-dir', str(self.root)])
        with patch.object(crawler, 'online') as online, patch.object(crawler, 'save_result', return_value=0) as save:
            crawler.run(args)
            online.assert_not_called()
            self.assertEqual(save.call_args.args[0][0]['content'], '手动提供的正文')
        self.assertIsNotNone(self.store.get('stop'))

    def test_interruption_exports_immediate_checkpoint(self):
        self.store.set('legacy_imported', True)
        args = crawler.parser().parse_args(['--state-dir', str(self.root), '--keyword', '旅游'])
        def interrupted(args, store, job, guard, report):
            store.upsert([note(content='已完成', scope='detail')], job)
            raise KeyboardInterrupt
        with patch.object(crawler, 'online', side_effect=interrupted), patch.object(crawler, 'save_result', return_value=2) as save, contextlib.redirect_stdout(io.StringIO()):
            crawler.run(args)
            self.assertEqual(save.call_args.args[1]['status'], 'interrupted')
            self.assertEqual(save.call_args.args[0][0]['content'], '已完成')

    def test_weighted_sampling_boundaries(self):
        class RNG:
            def __init__(self, p): self.p = p
            def random(self): return self.p
        for probability, expected in ((0, 1), (.5999, 1), (.6, 2), (.7999, 2),
                                      (.8, 3), (.8999, 3), (.9, 4), (.9499, 4), (.95, 5), (.999, 5)):
            self.assertEqual(choose_step(RNG(probability)), expected)

    def test_sampling_skips_and_resume_does_not_redraw(self):
        class RNG:
            def __init__(self): self.calls = 0
            def random(self): self.calls += 1; return .85
        rng = RNG()
        plan = extend_plan(None, ['a', 'b'], 1, rng)
        self.assertEqual(plan['selected'], [])
        self.assertEqual(rng.calls, 1)
        plan = extend_plan(plan, ['a', 'b', 'c', 'd'], 1, rng)
        self.assertEqual([r['note_id'] for r in plan['selected']], ['c'])
        self.assertEqual([r['note_id'] for r in plan['skipped']], ['a', 'b'])
        self.assertEqual(rng.calls, 1)
        self.store.set('sampling:test', plan)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get('sampling:test'), plan)

    def test_archive_preserves_response_bytes_and_parses_from_disk(self):
        arc = Archive(self.root / 'archive', '旅游')
        folder = arc.begin('detail', 'abc')
        body = b'{"data":{"id":"abc","title":"Test","desc":"body","user":{}}}'
        saved = arc.response('https://www.xiaohongshu.com/api/feed', 200, 'xhr', 'application/json', body)
        self.assertEqual(saved.read_bytes(), body)
        arc.snapshot('<div id="detail-desc">DOM body</div>', {}, [], 'https://www.xiaohongshu.com/explore/abc')
        rows = crawler.parse_capture(folder, '旅游')
        self.assertEqual(rows[0]['content'], 'body')
        self.assertEqual(rows[0]['original_html_path'], '')
        self.assertTrue(Path(rows[0]['rendered_html_path']).exists())
        self.assertTrue((folder / 'parsed.json').exists())

    def test_spa_rendered_html_is_not_mislabeled_original_document(self):
        arc = Archive(self.root / 'archive', '旅游')
        folder = arc.begin('detail', 'abc')
        original = b'<html>Original explore entry</html>'
        saved = arc.response('https://www.xiaohongshu.com/explore', 200, 'document', 'text/html', original)
        arc.snapshot('<div id="detail-desc">原文</div>', {}, [], 'https://www.xiaohongshu.com/explore/abc')
        rows = crawler.offline_rows(folder, '旅游')
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(rows[0]['content'], '原文')
        self.assertEqual(rows[0]['note_id'], 'abc')
        self.assertEqual(rows[0]['original_html_path'], '')

    def test_search_and_post_archives_are_separate(self):
        arc = Archive(self.root / 'archive', '旅游')
        search = arc.begin('search')
        post = arc.begin('detail', 'abc')
        self.assertIn('search', search.parts)
        self.assertIn('posts', post.parts)
        original = b'<!doctype html><div id="detail-desc">body</div>'
        arc.response('https://www.xiaohongshu.com/explore/abc', 200, 'document', 'text/html', original)
        arc.snapshot(original.decode(), {}, [], 'https://www.xiaohongshu.com/explore/abc')
        rows = crawler.parse_capture(post, '旅游')
        self.assertEqual(Path(rows[0]['original_html_path']).read_bytes(), original)

    def test_checkpoint_csv_includes_search_order_and_skipped_rows(self):
        job = 'keyword:旅游'
        self.store.upsert([note('a'), note('b'), note('c', content='正文', scope='detail')], job)
        self.store.set('sampling:' + job, {'selected': [{'note_id': 'c', 'position': 3, 'step': 3}],
                                        'skipped': [{'note_id': 'a'}, {'note_id': 'b'}]})
        path = crawler.export_checkpoint(self.store, job)
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r['note_id'] for r in rows], ['a', 'b', 'c'])
        self.assertEqual([r['sampling_status'] for r in rows], ['skipped', 'skipped', 'selected_complete'])

    def test_late_response_stays_in_original_stage(self):
        arc = Archive(self.root / 'archive', '旅游')
        old = arc.begin('search')
        new = arc.begin('detail', 'abc')
        arc.response('https://www.xiaohongshu.com/search/notes', 200, 'xhr', 'application/json', b'{}', folder=old)
        self.assertEqual(len(json.loads((old / 'manifest.json').read_text(encoding='utf-8'))['responses']), 1)
        self.assertEqual(json.loads((new / 'manifest.json').read_text(encoding='utf-8'))['responses'], [])

    def test_search_capture_uses_card_order_and_excludes_entry_recommendations(self):
        arc = Archive(self.root / 'archive', '旅游')
        entry = arc.begin('entry')
        arc.response('https://www.xiaohongshu.com/explore', 200, 'document', 'text/html', b'<html>Entry</html>')
        self.assertEqual(crawler.parse_capture(entry, '旅游'), [])
        search = arc.begin('search')
        a, b, other = 'a'*24, 'b'*24, 'c'*24
        data = {'items': [{'id': nid, 'display_title': nid, 'user': {'nickname': '作者'},
                           'interact_info': {'liked_count': '123'}} for nid in (a,b,other)]}
        arc.response('https://www.xiaohongshu.com/search/notes', 200, 'xhr', 'application/json', json.dumps(data).encode())
        html = ''.join(f'<section class="note-item"><a href="/explore/{nid}">笔记</a></section>' for nid in (b,a))
        arc.snapshot(html, {}, [], 'https://www.xiaohongshu.com/search_result?keyword=test')
        rows = crawler.parse_capture(search, '旅游')
        self.assertEqual([r['note_id'] for r in rows], [b,a])
        self.assertEqual(rows[0]['likes'], '123')
        self.assertEqual(rows[0]['author'], '作者')

    def test_pending_sampling_is_not_replaced_by_unselected_full_cache(self):
        self.store.set('legacy_imported', True)
        job = 'keyword:旅游'
        self.store.upsert([note('selected'), note('other', content='缓存', scope='detail')], job)
        self.store.set('sampling:' + job, {'cursor': 0, 'selected': [{'note_id': 'selected', 'position': 1, 'step': 1}],
                                         'skipped': [], 'pending': None})
        self.store.stop('账号警告')
        args = crawler.parser().parse_args(['--keyword', '旅游', '--limit', '1', '--details', '--state-dir', str(self.root)])
        with patch.object(crawler, 'online') as online, patch.object(crawler, 'save_result', return_value=2) as save, contextlib.redirect_stdout(io.StringIO()):
            crawler.run(args)
            online.assert_not_called()
            self.assertEqual(save.call_args.args[1]['status'], 'stopped')
            self.assertEqual(save.call_args.args[0][0]['note_id'], 'selected')

    def test_search_jobs_keep_keywords_filters_and_normalize_query_order(self):
        base = 'https://www.xiaohongshu.com/search_result'
        a = crawler.job_key('', base + '?keyword=Hangzhou&sort=latest&xsec_token=secret')
        b = crawler.job_key('', base + '?sort=latest&keyword=Hangzhou&source=web&xsec_token=other')
        self.assertEqual(a, b)
        self.assertNotEqual(a, crawler.job_key('', base + '?keyword=Xian&sort=latest'))
        self.assertNotEqual(a, crawler.job_key('', base + '?keyword=Hangzhou&sort=hot'))
        self.assertNotIn('secret', a)
        self.assertNotEqual(a, 'url:www.xiaohongshu.com/search_result')

    def test_note_job_ignores_rotated_access_tokens(self):
        url = 'https://www.xiaohongshu.com/explore/' + 'a'*24
        self.assertEqual(crawler.job_key('', url + '?xsec_token=a'),
                         crawler.job_key('', url + '?xsec_token=b&xsec_source=pc_search'))

    def test_summary_keeps_body_and_all_detail_sources_and_saves_search_source(self):
        detail = note(content='正文', scope='detail')
        detail.update(source_archive='posts/abc', original_html_path='posts/abc/original.html',
                      rendered_html_path='posts/abc/rendered.html', raw_json_paths='posts/abc/response.json')
        self.store.upsert([detail], 'keyword:A')
        summary = note()
        summary.update(source_archive='search/B', original_html_path='search/B/original.html',
                       rendered_html_path='search/B/rendered.html', raw_json_paths='search/B/response.json')
        self.store.upsert([summary], 'keyword:B')
        current = self.store.find('abc')
        self.assertEqual(current['content'], '正文')
        for field in crawler.PROVENANCE_FIELDS:
            self.assertEqual(current[field], detail[field])
            self.assertEqual(self.store.discovery_source('keyword:B', 'abc')[field], summary[field])
        path = crawler.export_checkpoint(self.store, 'keyword:B')
        with path.open(encoding='utf-8-sig', newline='') as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row['source_archive'], 'posts/abc')
        self.assertEqual(row['search_source_archive'], 'search/B')

    def test_new_detail_replaces_provenance_as_a_bundle(self):
        old = note(content='旧正文', scope='detail')
        old.update(source_archive='posts/old', original_html_path='old.html')
        self.store.upsert([old], 'job')
        new = note(content='新正文', scope='detail')
        new.update(source_archive='posts/new', rendered_html_path='new-rendered.html')
        self.store.upsert([new], 'job')
        row = self.store.find('abc')
        self.assertEqual(row['content'], '新正文')
        self.assertEqual(row['source_archive'], 'posts/new')
        self.assertEqual(row['original_html_path'], '')
        newer = note(content='没有来源的新正文', scope='detail')
        self.store.upsert([newer], 'job')
        self.assertTrue(all(not self.store.find('abc')[key] for key in crawler.PROVENANCE_FIELDS))

    def test_legacy_body_without_archive_does_not_gain_false_search_origin(self):
        self.store.upsert([note(content='历史正文', scope='detail')], 'job')
        self.store.upsert([{**note(), 'source_archive': 'search/new'}], 'job')
        self.assertEqual(self.store.find('abc')['source_archive'], '')
        self.assertEqual(self.store.discovery_source('job', 'abc')['source_archive'], 'search/new')

    def test_action_log_is_local_and_immediately_readable(self):
        arc = Archive(self.root / 'archive', '旅游')
        report = {}
        with contextlib.redirect_stdout(io.StringIO()) as terminal:
            crawler.action_log(arc, report, '提交搜索', '旅游')
        self.assertIn('提交搜索', terminal.getvalue())
        event = json.loads(Path(report['action_log']).read_text(encoding='utf-8'))
        self.assertEqual(event['action'], '提交搜索')

    def test_single_test_uses_fresh_job_one_note_and_still_obeys_stop_lock(self):
        self.store.set('legacy_imported', True)
        self.store.stop('账号警告')
        with patch.object(crawler, 'online') as online, patch.object(crawler, 'save_result', return_value=2) as save, contextlib.redirect_stdout(io.StringIO()):
            crawler.main(['--test-once', '--keyword', '旅游', '--slow-mo', '500', '--state-dir', str(self.root)])
            online.assert_not_called()
            self.assertEqual(save.call_args.args[1]['status'], 'stopped')
            self.assertTrue(save.call_args.args[1]['test_once'])

    def test_single_test_passes_one_note_and_visible_slow_mode_to_online(self):
        self.store.set('legacy_imported', True)
        def fake_online(args, store, job, guard, report):
            self.assertEqual(args.limit, 1)
            self.assertTrue(args.details)
            self.assertFalse(args.headless)
            self.assertEqual(args.slow_mo, 500)
            self.assertTrue(job.startswith('test:keyword:旅游:'))
        with patch.object(crawler, 'online', side_effect=fake_online), patch.object(crawler, 'save_result', return_value=0):
            crawler.main(['--test-once', '--keyword', '旅游', '--slow-mo', '500', '--state-dir', str(self.root)])

    def test_default_profile_is_the_jointly_logged_in_session(self):
        args = crawler.parser().parse_args([])
        self.assertEqual(args.profile, 'check_20261030')
        self.assertFalse(args.login)

    def test_old_account_directory_is_rejected_without_opening_browser(self):
        with patch.object(crawler, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                crawler.main(['--profile', 'default'])
            run.assert_not_called()

    def test_user_provided_search_result_cover_link_is_preserved(self):
        arc = Archive(self.root / 'archive', '广东旅游景点')
        folder = arc.begin('search')
        nid = '6a8fde870000000021032a8f'
        # 使用测试占位 token，不使用用户贴出的真实值。
        html = ('<input id="search-input" placeholder="董洁">'
                '<textarea aria-hidden="true" name="aiSearchTextarea"></textarea>'
                '<section class="note-item"><a class="cover mask ld" target="_self" '
                f'href="/search_result/{nid}?xsec_token=TEST_ONLY&amp;xsec_source=pc_search">'
                '<img src="test.webp"></a></section>')
        arc.snapshot(html, {}, [], 'https://www.xiaohongshu.com/search_result?keyword=test')
        rows = crawler.parse_capture(folder, '广东旅游景点')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['note_id'], nid)
        self.assertIn('/search_result/' + nid, rows[0]['url'])
        self.assertIn('xsec_token=TEST_ONLY&xsec_source=pc_search', rows[0]['url'])

    def test_observed_onebox_empty_result_is_nonblocking_with_notice(self):
        url = 'https://edith.xiaohongshu.com/api/sns/web/v1/search/onebox'
        payload = {'msg': '成功', 'data': {}, 'code': 0, 'success': False}
        self.assertIsNone(restriction_reason(200, payload, url=url))
        self.assertEqual(response_notice(200, payload, url), 'onebox_empty_inconsistent_response')
        self.assertEqual(restriction_reason(200, payload, url=url.replace('/onebox', '/notes')), 'api_unsuccessful')
        self.assertEqual(restriction_reason(200, payload), 'api_unsuccessful')
        self.assertTrue(restriction_reason(200, payload, url=url.replace('edith.xiaohongshu.com', 'example.com')))

    def test_onebox_exception_never_ignores_restrictions_or_unknown_changes(self):
        url = 'https://edith.xiaohongshu.com/api/sns/web/v1/search/onebox'
        payload = {'msg': '成功', 'data': {}, 'code': 0, 'success': False}
        for status in (403, 429):
            self.assertTrue(restriction_reason(status, payload, url=url))
        for changed in ({**payload, 'msg': '访问过于频繁'},
                        {**payload, 'code': 300012},
                        {**payload, 'data': {'verify_url': 'verify'}},
                        {**payload, 'extra': 'unknown'},
                        {**payload, 'data': {'error': 'unknown'}}):
            self.assertTrue(restriction_reason(200, changed, url=url))
        self.assertTrue(restriction_reason(200, payload, text='安全限制', url=url))

    def test_explicit_note_card_without_title_is_kept_but_hot_query_is_not(self):
        data = {'items': [
            {'id': 'a'*24, 'model_type': 'note', 'note_card': {
                'user': {'nickname': '作者'}, 'interact_info': {'liked_count': '1'}}},
            {'id': 'b'*24, 'model_type': 'hot_query', 'hot_query': {'title': '推荐词'}},
        ]}
        rows = crawler.extract_notes(data, '广东旅游景点')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['note_id'], 'a'*24)
        self.assertEqual(rows[0]['title'], '')
        self.assertEqual(rows[0]['content_scope'], 'feed_summary')

    def test_passive_image_storage_preserves_bytes_and_deduplicates_urls(self):
        arc = Archive(self.root / 'archive', '旅游')
        url = 'https://sns-webpic-qc.xhscdn.com/example/abcdefghijklmnop!thumb'
        body = b'RIFF-local-image-test-WEBP'
        row = arc.image_response(url, 'image/webp', body)
        self.assertEqual((arc.root / 'received_images' / row['file']).read_bytes(), body)
        self.assertIsNone(arc.image_response(url, 'image/webp', body))
        self.assertIsNone(arc.image_response(url+'2', 'text/html', b'not an image'))
        self.assertEqual(len((arc.root / 'received_images/index.jsonl').read_text(encoding='utf-8').splitlines()), 1)

    def test_note_images_match_only_received_assets_without_filling_missing_images(self):
        arc = Archive(self.root / 'archive', '旅游')
        received_url = 'https://sns-webpic-qc.xhscdn.com/new-sign/abcdefghijklmnop!thumb'
        body = b'RIFF-received-WEBP'
        arc.image_response(received_url, 'image/webp', body)
        arc.image_response('https://sns-webpic-qc.xhscdn.com/path/unrelatedavatar01', 'image/png', b'avatar')
        folder = arc.begin('detail', 'abc')
        note_data = {'id': 'abc', 'title': 'Test', 'desc': '正文', 'user': {}, 'image_list': [
            {'width': 1000, 'height': 1500, 'url_default': 'http://sns-webpic-qc.xhscdn.com/old-sign/abcdefghijklmnop!large'},
            {'width': 1000, 'height': 1500, 'url_default': 'https://sns-webpic-qc.xhscdn.com/path/notreceivedimage1'},
        ]}
        arc.response('https://edith.xiaohongshu.com/api/sns/web/v1/feed', 200, 'xhr', 'application/json', json.dumps(note_data).encode())
        arc.snapshot('<div id="detail-desc">正文</div>', {}, [], 'https://www.xiaohongshu.com/explore/abc')
        rows = crawler.parse_capture(folder, '旅游')
        self.assertEqual(rows[0]['image_count'], 2)
        self.assertEqual(rows[0]['saved_image_count'], 1)
        index = json.loads(Path(rows[0]['image_manifest']).read_text(encoding='utf-8'))
        self.assertEqual(index['images'][1]['status'], 'not_received')
        image = folder / 'images' / index['images'][0]['variants'][0]['file']
        self.assertEqual(image.read_bytes(), body)
        self.assertEqual(index['images'][0]['variants'][0]['matched_by'], 'asset_variant')
        self.assertEqual(len(list((folder / 'images').glob('*.webp'))), 1)

    def test_images_received_after_snapshot_can_be_matched_offline(self):
        arc = Archive(self.root / 'archive', '旅游')
        folder = arc.begin('detail', 'abc')
        url = 'https://sns-webpic-qc.xhscdn.com/path/abcdefghijklmnop'
        arc.snapshot('<div id="detail-desc">正文</div>', {'id':'abc','imageList':[{'url':url}]}, [], 'https://www.xiaohongshu.com/explore/abc')
        self.assertEqual(attach_images(folder)['saved_image_count'], 0)
        arc.image_response(url, 'image/jpeg', b'JPEG-received-later')
        self.assertEqual(attach_images(folder)['saved_image_count'], 1)
        self.assertEqual(attach_images(folder)['saved_image_count'], 1)
        self.assertEqual(len(list((folder / 'images').glob('*.jpg'))), 1)

    def test_single_test_accepts_direct_note_url_without_reusing_chat_token(self):
        self.store.set('legacy_imported', True)
        url = 'https://www.xiaohongshu.com/explore/' + 'a'*24 + '?xsec_token=TEST_ONLY'
        def fake_online(args, store, job, guard, report):
            self.assertEqual(args.url, url)
            self.assertEqual(args.limit, 1)
            self.assertTrue(args.details)
        with patch.object(crawler, 'online', side_effect=fake_online), patch.object(crawler, 'save_result', return_value=0):
            crawler.main(['--test-once', '--url', url, '--state-dir', str(self.root)])

    def test_comment_draw_probabilities_and_insufficient_candidates(self):
        class RNG:
            def __init__(self, value): self.value = value
            def random(self): return self.value
        for probability, expected in ((0, 0), (.499, 0), (.5, 1), (.749, 1), (.75, 2), (.999, 2)):
            self.assertEqual(comment_draw(2, RNG(probability))['actual'], expected)
        self.assertEqual(comment_draw(1, RNG(.8))['actual'], 0)
        self.assertEqual(comment_draw(0, RNG(.6))['actual'], 0)
        self.assertEqual(comment_draw(1, RNG(.8))['reason'], 'insufficient_candidates')

    def test_new_browsing_preferences_are_default_and_can_be_disabled(self):
        args = crawler.parser().parse_args([])
        self.assertTrue(args.browse_images)
        self.assertTrue(args.expand_comments)
        args = crawler.parser().parse_args(['--no-browse-images', '--no-expand-comments'])
        self.assertFalse(args.browse_images)
        self.assertFalse(args.expand_comments)

    def test_comments_are_parsed_offline_with_replies_and_deduplicated(self):
        arc = Archive(self.root / 'archive', '旅游')
        folder = arc.begin('detail', 'abc')
        comment = {'id':'c1','note_id':'abc','content':'主评论','user_info':{'nickname':'作者'},
                   'sub_comments':[{'id':'r1','note_id':'abc','content':'回复'}]}
        payload = {'success':True,'data':{'comments':[comment], 'has_more':False}}
        for _ in range(2):
            arc.response('https://edith.xiaohongshu.com/api/sns/web/v2/comment/page',200,'xhr','application/json',json.dumps(payload).encode())
        result = parse_comments(folder)
        self.assertEqual(result['saved_comment_count'], 2)
        saved = json.loads((folder/'comments/comments.json').read_text(encoding='utf-8'))
        self.assertEqual(next(r for r in saved if r['comment_id']=='r1')['parent_comment_id'], 'c1')


if __name__ == '__main__':
    unittest.main()
