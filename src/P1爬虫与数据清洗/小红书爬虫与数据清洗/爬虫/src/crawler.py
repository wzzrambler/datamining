"""通过正常网页访问采集小红书公开笔记。登录状态仅保存在项目本地。"""
import argparse
import csv
import json
import re
import sys
import sqlite3
import math
import io
import hashlib
import queue
import threading
import time
import random
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urljoin, parse_qsl, urlencode
from .runtime import Store, Guard, RunLock, Stopped, complete, schedule_pause, restriction_reason, response_notice, now, PROVENANCE_FIELDS
from .archive import Archive, extend_plan, attach_images
from .comments import parse_comments
from .interactions import browse_images, expand_comments, wait_comments_ready
from .diagnostics import LoadDiagnostics, safe_error

CODE_BASE = Path(__file__).resolve().parent.parent
BASE = Path(os.environ.get('XHS_WORKSPACE', str(CODE_BASE))).resolve()
_repo = next(p for p in CODE_BASE.parents if (p / 'src' / 'paths.py').is_file())
OUT = BASE / 'output' if os.environ.get('XHS_WORKSPACE') else _repo / 'data' / '小红书增量采集' 
ACTIVE_PROFILE = 'check_20261030'
SEARCH_SELECTOR = ', '.join((
    'input#search-input:not([aria-hidden="true"]):visible',
    'textarea#search-input-ai:not([aria-hidden="true"]):not([tabindex="-1"]):visible',
    'textarea#search-input-in-feeds:not([aria-hidden="true"]):not([tabindex="-1"]):visible',
))
FIELDS = ['note_id', 'title', 'content', 'content_scope', 'author', 'author_id',
          'likes', 'collects', 'comments', 'shares', 'tags', 'published_at',
          'url', 'keyword', 'collected_at']
FIELDS += ['source_archive', 'original_html_path', 'rendered_html_path', 'raw_json_paths']
FIELDS += ['image_count', 'saved_image_count', 'image_manifest']
FIELDS += ['saved_comment_count', 'comment_manifest']


def keyword_slug(keyword):
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', keyword.strip())[:60].strip(' .') or 'keyword'
    return slug + '_' + hashlib.sha256(keyword.strip().encode('utf-8')).hexdigest()[:8]


def keyword_output(keyword):
    return OUT / keyword_slug(keyword)


def restricted(text, url=''):
    return '/website-login/error' in url or any(s in text for s in (
        '安全限制', 'IP存在风险', '安全验证', '访问频次异常', '网络环境存在风险', '请完成验证'))


def parse_state(html):
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.*?)</script>', html, re.S)
    if not match:
        return {}
    # 小红书的初始化对象包含 JSON 以外的 undefined；只替换字符串之外的值。
    raw = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',
                 lambda m: 'null' if m.group() == 'undefined' else m.group(), match[1])
    return json.loads(raw.strip().rstrip(';'))


def extract_notes(data, keyword=''):
    records = {}

    def visit(obj, inherited_id='', token='', note_context=False):
        if isinstance(obj, list):
            for item in obj:
                visit(item)
        elif isinstance(obj, dict):
            nid = obj.get('noteId') or obj.get('note_id') or obj.get('id') or inherited_id
            tok = obj.get('xsecToken') or obj.get('xsec_token') or token
            user = obj.get('user') or {}
            info = obj.get('interactInfo') or obj.get('interact_info') or {}
            if (isinstance(user, dict) and isinstance(info, dict) and nid
                    and (any(k in obj for k in ('title', 'displayTitle', 'display_title', 'desc'))
                         or (note_context and 'user' in obj))
                    and ('user' in obj or info)):
                url = f'https://www.xiaohongshu.com/explore/{nid}'
                if tok:
                    url += '?xsec_token=' + quote(str(tok), safe='') + '&xsec_source=pc_feed'
                tags = obj.get('tagList') or obj.get('tag_list') or []
                candidate = {
                    'note_id': str(nid),
                    'title': obj.get('title') or obj.get('displayTitle') or obj.get('display_title') or '',
                    'content': obj.get('desc', ''),
                    'content_scope': 'detail' if 'desc' in obj else 'feed_summary',
                    'author': user.get('nickname') or user.get('nickName') or '',
                    'author_id': user.get('userId') or user.get('user_id') or '',
                    'likes': info.get('likedCount', info.get('liked_count', '')),
                    'collects': info.get('collectedCount', info.get('collected_count', '')),
                    'comments': info.get('commentCount', info.get('comment_count', '')),
                    'shares': info.get('shareCount', info.get('share_count', '')),
                    'tags': '|'.join(str(t.get('name', '')) for t in tags if isinstance(t, dict)),
                    'published_at': obj.get('time') or obj.get('lastUpdateTime') or '',
                    'url': url, 'keyword': keyword,
                    'collected_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                }
                merge(records, [candidate])
            for key, value in obj.items():
                is_card = key in ('noteCard', 'note_card', 'note')
                visit(value, str(nid or '') if is_card else '', str(tok), is_card)

    visit(data)
    return list(records.values())


def merge(records, rows):
    for row in rows:
        old = records.get(row['note_id'])
        if not old or not complete(old) or complete(row):
            if old and 'xsec_token=' in old['url'] and 'xsec_token=' not in row['url']:
                row['url'] = old['url']
            records[row['note_id']] = row


def browser_state(page):
    # 初始化状态是 JavaScript 对象，不一定是严格 JSON；由浏览器完成序列化。
    return page.evaluate('''() => {
        const seen = new WeakSet();
        return JSON.parse(JSON.stringify(window.__INITIAL_STATE__ || {}, (key, value) => {
            if (value && value.__v_isRef) value = value.value;
            if (value && typeof value === 'object') {
                if (seen.has(value)) return undefined;
                seen.add(value);
            }
            return typeof value === 'bigint' ? String(value) : value;
        }));
    }''')


def browser_notes(page, keyword):
    return extract_notes(browser_state(page), keyword)


def page_note_rows(page, keyword):
    rows = browser_notes(page, keyword)
    # 优先保留页面真实链接的查询参数，不自行猜测访问来源参数。
    links = page.locator('a[href*="/explore/"], a[href*="/discovery/item/"], a[href*="/search_result/"]').evaluate_all(
        '(nodes) => nodes.map(n => n.getAttribute("href"))')
    by_id = {}
    for href in links:
        url = urljoin(page.url, href or '')
        match = re.search(r'/([a-fA-F0-9]{24})(?:[/?#]|$)', urlparse(url).path)
        if match and official_url(url):
            by_id[match[1]] = url
    for row in rows:
        if row['note_id'] in by_id:
            row['url'] = by_id[row['note_id']]
    return rows


def error_summary(exc):
    return re.sub(r'https?://[^\s\"\']+', '[URL omitted]', str(exc).split('\n')[0])[:500]


def job_key(keyword, url=None):
    if url:
        parsed = urlparse(url)
        base = (parsed.hostname or '').lower() + parsed.path.rstrip('/')
        if re.fullmatch(r'/(?:explore|discovery/item|search_result)/[a-fA-F0-9]{24}', parsed.path.rstrip('/')):
            return 'url:' + base  # 同一篇笔记更换 token 不产生新任务。
        ephemeral = {'xsec_token', 'xsec_source', 'source', 'from', 'exsource', 'search_id',
                     'searchid', 'session_id', 'request_id', 'token', 'access_token', 'auth',
                     'signature', 'sign', 'timestamp', 'spm'}
        params = sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                        if key.lower() not in ephemeral and not key.lower().startswith('utm_')
                        and not key.lower().endswith('_token'))
        # v2 不会误用旧版省略查询参数后混在一起的 URL 任务。
        return 'url:v2:' + base + ('?' + urlencode(params) if params else '')
    return 'keyword:' + keyword.strip()


def official_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ''
    return parsed.scheme == 'https' and (host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com') or host in ('xhslink.com', 'xhslink.cn'))


def share_url(url):
    parsed = urlparse(url)
    return parsed.scheme == 'https' and parsed.hostname in ('xhslink.com', 'xhslink.cn') and bool(parsed.path.strip('/'))


def plan_rows(store, job, limit):
    ordered = store.ordered_rows(job)
    plan = extend_plan(store.get('sampling:' + job), [r['note_id'] for r in ordered], limit, random)
    store.set('sampling:' + job, plan)
    return [store.find(p['note_id']) for p in plan['selected'][:limit]]


def export_checkpoint(store, job):
    """每次解析完成后覆盖写一份可直接查看的任务清单，不影响内部停止锁。"""
    folder = OUT / 'checkpoints'
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', job.removeprefix('keyword:'))[:40].strip(' .') or 'task'
    path = folder / (slug + '_' + hashlib.sha256(job.encode()).hexdigest()[:8] + '.csv')
    plan = store.get('sampling:' + job) or {}
    selected = {p['note_id']: p for p in plan.get('selected', [])}
    skipped = {p['note_id'] for p in plan.get('skipped', [])}
    fields = ['search_job', 'search_position', 'sampling_status', 'sampling_step'] + FIELDS
    fields += ['search_' + key for key in PROVENANCE_FIELDS]
    temp = path.with_suffix('.csv.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for position, row in enumerate(store.ordered_rows(job), 1):
            nid = row['note_id']
            status = ('selected_complete' if complete(row) else 'selected_pending') if nid in selected else 'skipped' if nid in skipped else 'cached_complete' if complete(row) else 'discovered'
            writer.writerow({**row, 'search_job': job, 'search_position': position,
                             'sampling_status': status, 'sampling_step': selected.get(nid, {}).get('step', ''),
                             **{'search_' + key: value for key, value in store.discovery_source(job, nid).items()}})
    os.replace(temp, path)
    return path


def parse_capture(folder, keyword):
    """所有数据都从已经落盘的响应／HTML／状态文件解析，不向网页补请求。"""
    folder = Path(folder)
    meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if meta['kind'] == 'entry':
        return []  # 入口页推荐内容不属于用户的搜索结果。
    records = {}
    warnings = []
    files = []
    for response in meta['responses']:
        if response['file'].endswith('.json'):
            patterns = ('/feed',) if meta['kind'] == 'detail' else ('/search/notes', '/user_posted')
            if not any(pattern in response['url'] for pattern in patterns):
                continue
        files.append(folder / response['file'])
    if (folder / 'state.json').exists():
        files.append(folder / 'state.json')
    for file in files:
        try:
            merge(records, offline_rows(file, keyword))
        except (ValueError, UnicodeError, TypeError) as exc:
            warnings.append({'file': file.name, 'error_type': type(exc).__name__})
    rendered = folder / 'rendered.html'
    if rendered.exists():
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(rendered.read_text(encoding='utf-8'), 'html.parser')
        cards = soup.select('section.note-item')
        if meta['kind'] == 'detail':
            nid = meta['note_id']
            row = records.get(nid)
            desc = soup.select_one('#detail-desc')
            if desc and desc.get_text('\n', strip=True) and not (row and complete(row)):
                title = soup.select_one('#detail-title, .note-content .title')
                row = row or {**{k: '' for k in FIELDS}, 'note_id': nid, 'keyword': keyword,
                              'title': title.get_text(strip=True) if title else '',
                              'url': meta.get('url', ''), 'collected_at': now()}
                merge(records, [{**row, 'content': desc.get_text('\n', strip=True), 'content_scope': 'detail'}])
        elif cards:
            # 保存页缺少初始化数据时，也可从本地卡片链接提取最小列表摘要。
            card_order = []
            for card in cards:
                link = card.select_one('a[href*="/explore/"], a[href*="/search_result/"], a[href*="/discovery/item/"]')
                if not link:
                    continue
                url = urljoin(meta.get('url', ''), link.get('href', ''))
                match = re.search(r'/([a-fA-F0-9]{24})(?:[/?#]|$)', urlparse(url).path)
                if match and official_url(url):
                    card_order.append(match[1])
                    title = card.select_one('.title')
                    if match[1] in records:
                        records[match[1]]['url'] = url
                    else:
                        merge(records, [{**{k: '' for k in FIELDS}, 'note_id': match[1],
                                         'title': title.get_text(strip=True) if title else '', 'url': url,
                                         'keyword': keyword, 'content_scope': 'feed_summary', 'collected_at': now()}])
            if card_order:
                records = {nid: records[nid] for nid in card_order if nid in records}
    if (folder / 'links.json').exists():
        links = json.loads((folder / 'links.json').read_text(encoding='utf-8'))
        for href in links:
            url = urljoin(meta.get('url', ''), href or '')
            match = re.search(r'/([a-fA-F0-9]{24})(?:[/?#]|$)', urlparse(url).path)
            if match and official_url(url) and match[1] in records:
                records[match[1]]['url'] = url
    # SPA 路由变化后，不把入口 /explore 的文档声称为搜索页／某篇详情的原始文档。
    htmls = [str((folder / r['file']).resolve()) for r in meta['responses']
             if r['file'].endswith('.html') and urlparse(r['url']).path.rstrip('/') == urlparse(meta.get('url', '')).path.rstrip('/')]
    jsons = [str((folder / r['file']).resolve()) for r in meta['responses'] if r['file'].endswith('.json')]
    rows = list(records.values())
    if meta['kind'] == 'detail':
        rows = [r for r in rows if r['note_id'] == meta['note_id']]
    for row in rows:
        row.update(source_archive=str(folder.resolve()), original_html_path=htmls[0] if htmls else '',
                   rendered_html_path=str(rendered.resolve()) if rendered.exists() else '', raw_json_paths='|'.join(jsons))
        if meta['kind'] == 'detail':
            row.update(attach_images(folder))
            row.update(parse_comments(folder))
    (folder / 'parsed.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'parse_report.json').write_text(json.dumps({'count': len(rows), 'warnings': warnings}, ensure_ascii=False, indent=2), encoding='utf-8')
    return rows


def capture_page(page, archive, keyword):
    # 先保存页面，再导出内存状态。两者都只读取本机浏览器，不会补发网络请求。
    html = page.content()
    (archive.current / 'rendered.html').write_text(html, encoding='utf-8')
    state = browser_state(page)
    links = page.locator('a[href]').evaluate_all('(nodes) => nodes.map(n => n.getAttribute("href"))')
    folder = archive.snapshot(html, state, links, page.url)
    return parse_capture(folder, keyword)


def action_log(archive, report, action, detail=''):
    event = {'at': now(), 'action': action, 'detail': detail}
    print(f'[{event["at"]}] {action}' + (f'：{detail}' if detail else ''), flush=True)
    archive.root.mkdir(parents=True, exist_ok=True)
    path = archive.root / 'actions.jsonl'
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')
    report['action_log'] = str(path.resolve())


def offline_rows(path, keyword):
    """只读取本地内容，不执行 HTML/JavaScript，不下载资源。"""
    path = Path(path)
    if path.is_dir():
        if (path / 'manifest.json').exists():
            return parse_capture(path, keyword)
        rows = {}
        for manifest in sorted(path.rglob('manifest.json')):
            merge(rows, parse_capture(manifest.parent, keyword))
        return list(rows.values())
    raw = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.json':
        data = json.loads(raw)
        if isinstance(data, list) and all(isinstance(r, dict) and r.get('note_id') for r in data):
            return [{**{k: '' for k in FIELDS}, **r} for r in data]
        return extract_notes(data, keyword)
    if path.suffix.lower() == '.csv':
        return [{**{k: '' for k in FIELDS}, **r} for r in csv.DictReader(io.StringIO(raw)) if r.get('note_id')]
    if path.suffix.lower() in ('.html', '.htm'):
        try:
            rows = extract_notes(parse_state(raw), keyword)
        except (ValueError, TypeError):
            rows = []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, 'html.parser')
        desc = soup.select_one('#detail-desc')
        link = soup.select_one('link[rel="canonical"]')
        url = link.get('href', '') if link else ''
        nid = re.search(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})', url)
        if desc:
            content = desc.get_text('\n', strip=True)
            matching = next((r for r in rows if nid and r['note_id'] == nid[1]), None)
            if matching:
                matching.update(content=content, content_scope='detail')
                return rows
        if rows:
            return rows
        if not desc:
            return []
        title = soup.select_one('meta[property="og:title"]')
        title = title.get('content', '') if title else (soup.title.get_text(strip=True) if soup.title else path.stem)
        content = desc.get_text('\n', strip=True)
        note_id = nid[1] if nid else 'offline-' + hashlib.sha256(raw.encode()).hexdigest()[:24]
    elif path.suffix.lower() in ('.txt', '.md'):
        content = '\n'.join(line.rstrip() for line in raw.replace('\r\n', '\n').splitlines()).strip()
        title, url = path.stem, ''
        note_id = 'offline-' + hashlib.sha256(raw.encode()).hexdigest()[:24]
    else:
        raise ValueError('离线输入仅支持 JSON、CSV、HTML、TXT、MD')
    if not content:
        return []
    return [{**{k: '' for k in FIELDS}, 'note_id': note_id, 'title': title,
             'content': content, 'content_scope': 'detail', 'keyword': keyword,
             'url': url, 'collected_at': now()}]


def bootstrap(store):
    if store.get('legacy_imported'):
        return
    imported = 0
    for path in sorted(OUT.rglob('notes_*.json')):
        try:
            rows = offline_rows(path, '')
            for row in rows:
                store.upsert([row], job_key(row.get('keyword') or ''))
                imported += 1
        except (OSError, ValueError, TypeError):
            continue
    store.set('legacy_imported', True)
    store.audit('legacy_import', imported)


def save_result(rows, report, output=None):
    output = output if output is not None else OUT
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    report.update(count=len(rows), content_count=sum(complete(r) for r in rows),
                  summary_count=sum(not complete(r) for r in rows), collected_at=now())
    if report['status'] == 'ok' and report['details_requested'] and not all(complete(r) for r in rows):
        report['status'] = 'partial_content'
    if not rows and report['status'] == 'ok':
        report['status'] = 'no_notes'
    if report['status'] == 'ok' and len(rows) < report.get('target_count', 0):
        report['status'] = 'partial_target'
    (output / f'report_{stamp}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if rows:
        path = output / f'notes_{stamp}'
        path.with_suffix('.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        with path.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        print(f'已导出 {len(rows)} 条，完整正文 {report["content_count"]} 条：{path}.csv')
    print(f'状态：{report["status"]}；本轮浏览器请求数：{report.get("requests_run", 0)}')
    return 0 if report['status'] in ('ok', 'cache_hit') and rows else 2


def wait_ready(page, guard):
    """一秒一检查；后台响应仍被处理，等待中可 Ctrl+C，期限跨次保留。"""
    announced = False
    while True:
        guard.check()
        remaining = float(guard.store.get('next_visit_at', 0)) - time.time()
        if remaining <= 0:
            return
        if not announced:
            print(f'等待剩余约 {remaining:.0f} 秒（可 Ctrl+C 保存退出）。')
            announced = True
        if page:
            page.wait_for_timeout(min(remaining, 1) * 1000)
        else:
            time.sleep(min(remaining, 1))


def pause_after_note(args, store, report):
    short, extra = schedule_pause(store, args.min_wait, args.max_wait, args.long_probability,
                                 args.long_min, args.long_max)
    report['pauses'].append({'short_seconds': round(short, 2), 'extra_seconds': round(extra, 2)})
    print(f'下次访问前等待 {short:.1f} 秒' + (f'，附加长等待 {extra:.1f} 秒' if extra else '') + '。')


def page_check(page, guard):
    guard.check()
    # 优先使用错误页面路径或验证 UI，避免因笔记正文中出现风险相关词误停。
    reason = restriction_reason(url=page.url)
    ui = page.locator('[class*="captcha"], [class*="verify-modal"], [class*="login-modal"]')
    for i in range(min(ui.count(), 10)):
        if ui.nth(i).is_visible():
            reason = reason or 'verification_or_login_ui'
    if '/login' in urlparse(page.url).path:
        reason = reason or 'login_required'
    if not page.locator('#detail-desc, section.note-item').count():
        reason = reason or restriction_reason(text=page.locator('body').inner_text()[:5000])
    if reason:
        guard.halt(reason)
    guard.check()


def online(args, store, job, guard, report):
    from playwright.sync_api import sync_playwright
    phase = {'kind': 'idle', 'id': None, 'login': False}
    archive = Archive(keyword_output(args.keyword) / 'archive', args.keyword,
                      search_root=OUT / 'search_html' / keyword_slug(args.keyword))
    diagnostics = LoadDiagnostics(archive.root)
    request_folders = {}
    comment_batches = {}
    report['archive_root'] = str(archive.root.resolve())
    report['search_archive_root'] = str(archive.search_root.resolve())
    def show(action, detail=''):
        action_log(archive, report, action, detail)
    with sync_playwright() as p:
        options = {'headless': args.headless, 'locale': 'zh-CN', 'service_workers': 'block', 'slow_mo': args.slow_mo}
        if args.channel != 'chromium':
            options['channel'] = args.channel
        if args.profile == 'default':
            raise Stopped('旧账号目录已停用，请使用 check_20261030 或明确指定新的独立目录。')
        profile = BASE / '.profiles' / args.profile
        report['browser_profile'] = args.profile
        show('启动浏览器', f'{args.channel}，{"无界面" if args.headless else "可见窗口"}，目录 {args.profile}')
        context = p.chromium.launch_persistent_context(str(profile), **options)
        page = None
        try:
            def route_request(route):
                try:
                    guard.allow_request()
                except Stopped:
                    route.abort()
                else:
                    request = route.request
                    if phase['kind'] in ('search', 'detail') and request.resource_type in ('document', 'xhr', 'fetch'):
                        request_folders[request] = archive.current
                    route.continue_()

            def on_response(response):
                folder = request_folders.pop(response.request, None)
                host = urlparse(response.url).hostname or ''
                content_type = response.headers.get('content-type', '')
                image_host = (host == 'xhscdn.com' or host.endswith('.xhscdn.com')
                              or host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com'))
                if response.status == 200 and image_host and content_type.lower().startswith('image/'):
                    try:
                        archive.image_response(response.url, content_type, response.body())
                    except Exception as exc:
                        report.setdefault('image_capture_warnings', []).append(type(exc).__name__)
                    return
                official = host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com')
                relevant = official and (response.request.resource_type in ('document', 'xhr', 'fetch'))
                if not relevant:
                    return
                reason = restriction_reason(status=response.status, url=response.url)
                payload = None
                json_response = 'json' in content_type
                raw_path = None
                if folder and (json_response or (response.request.resource_type == 'document' and 'html' in content_type)):
                    try:
                        raw_path = archive.response(response.url, response.status, response.request.resource_type,
                                                    content_type, response.body(), folder)
                    except Exception as exc:
                        diagnostics.write('raw_response_save_error', error_type=type(exc).__name__, error=safe_error(exc))
                        guard.halt('raw_response_save_error')
                if json_response:
                    try:
                        # 已落盘响应优先从文件读；未归档的登录／状态响应只用于停止判断。
                        payload = json.loads(raw_path.read_bytes()) if raw_path else response.json()
                    except (ValueError, UnicodeError):
                        payload = None
                    except Exception:
                        payload = None
                    reason = reason or restriction_reason(status=response.status, payload=payload, url=response.url)
                    notice = response_notice(response.status, payload, response.url)
                    if notice and not reason:
                        warning = {'kind': notice, 'path': urlparse(response.url).path,
                                   'raw_file': str(raw_path.resolve()) if raw_path else ''}
                        report.setdefault('response_notices', []).append(warning)
                        store.audit('response_notice', json.dumps(warning, ensure_ascii=False))
                        show('记录非阻断响应', notice)
                if reason:
                    # 手动登录阶段仅放行明确的未登录状态，验证和限流仍立即停。
                    if not (phase['login'] and reason == 'api_restriction_or_login' and isinstance(payload, dict)
                            and str(payload.get('code')) in ('-100', '-101')):
                        guard.halt(reason)
                    return
                if (urlparse(response.url).path == '/api/sns/web/v2/comment/page'
                        and response.status == 200 and isinstance(payload, dict)
                        and isinstance(payload.get('data'), dict)
                        and isinstance(payload['data'].get('comments'), list)):
                    nid = dict(parse_qsl(urlparse(response.url).query)).get('note_id')
                    if nid:
                        comment_batches[nid] = {'count': len(payload['data']['comments'])}
                # 正文／搜索列表在 capture_page 中从归档文件解析，不在回调里直接入库。

            # 路由拦截发生在网络发送前；不添加自定义请求头、不重放接口。
            context.route('**/*', route_request)
            context.on('request', diagnostics.request)
            context.on('response', diagnostics.response)
            context.on('requestfinished', lambda request: diagnostics.end(request))
            context.on('requestfailed', lambda request: diagnostics.end(request, failed=True))
            context.on('response', on_response)
            page = context.new_page()
            page.on('pageerror', lambda error: diagnostics.write('pageerror', error=safe_error(error)))
            page.on('console', lambda message: diagnostics.write('console_error', error=safe_error(message.text)) if message.type == 'error' else None)
            page.on('domcontentloaded', lambda: diagnostics.write('domcontentloaded'))
            page.on('load', lambda: diagnostics.write('load'))

            def short_pause(seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    guard.check()
                    page.wait_for_timeout(min(.25, max(0, deadline-time.monotonic())) * 1000)
                if not phase['login']:
                    page_check(page, guard)

            def before_action(label):
                delay = random.uniform(1, 5)
                show('操作前等待', f'{label}，{delay:.2f} 秒')
                short_pause(delay)

            def visit(url):
                if not official_url(url):
                    guard.halt('invalid_saved_url')
                    guard.check()
                wait_ready(page, guard)
                before_action('打开页面')
                show('打开页面', (urlparse(url).hostname or '') + urlparse(url).path)
                response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                if response:
                    reason = restriction_reason(status=response.status, url=response.url)
                    if reason:
                        guard.halt(reason)
                guard.check()
                page.wait_for_timeout(1000)
                if not phase['login']:
                    page_check(page, guard)
                show('页面已打开')

            if args.login:
                phase['login'] = True
                visit('https://www.xiaohongshu.com/explore')
                print('请手动登录；完成后回终端按回车。不要发送密码或验证码。')
                show('等待人工登录', '完成后在终端按回车')
                answers = queue.Queue()

                def read_enter():
                    try:
                        input()
                        answers.put(True)
                    except EOFError:
                        answers.put(False)
                threading.Thread(target=read_enter, daemon=True).start()
                while answers.empty():
                    guard.check()
                    page.wait_for_timeout(500)
                if not answers.get():
                    raise Stopped('终端无法读取输入，请在交互式 PowerShell 中登录。')
                phase['login'] = False
                page_check(page, guard)
                if args.login_only:
                    # 持久化浏览器目录由 context.close() 写回，不导出 Cookie 明文。
                    report['login_confirmed_by_user'] = True
                    show('登录操作已确认', '只保存当前浏览器状态，不搜索或访问笔记')
                    return

            resolved_share = False
            if args.test_once and args.url and share_url(args.url):
                # 用同一浏览器完成分享跳转，复用当前页面，不再访问解析后的地址。
                phase['kind'] = 'detail'
                original_folder = archive.begin('detail', 'shared_link')
                visit(args.url)
                page.wait_for_url(re.compile(r'https://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item|search_result)/[a-fA-F0-9]{24}(?:[?#].*)?$'), timeout=15000)
                args.url = page.url
                nid = urlparse(args.url).path.rstrip('/').rsplit('/', 1)[-1]
                # 响应正文读取可能跨越跳转完成时刻；保持目录稳定，避免旧回调写入已移动路径。
                meta = json.loads((original_folder / 'manifest.json').read_text(encoding='utf-8'))
                meta['note_id'] = nid
                archive.write_json('manifest.json', meta)
                resolved_share = True
                show('分享链接已解析，复用当前笔记页面', nid)
            direct_note = args.test_once and bool(args.url)
            if direct_note:
                nid = urlparse(args.url).path.rstrip('/').rsplit('/', 1)[-1]
                store.upsert([{**{key: '' for key in FIELDS}, 'note_id': nid, 'url': args.url,
                               'keyword': args.keyword, 'content_scope': 'feed_summary'}], job)
                selected = store.rows(job, 1)
            else:
                selected = plan_rows(store, job, args.limit) if args.details else store.rows(job, args.limit)
            searched = len(selected) < args.limit
            if searched:
                phase['kind'] = 'search'
                archive.begin('search' if args.url else 'entry')
                if args.url:
                    search_url = args.url
                else:
                    # 使用网站搜索框提交，让网页决定搜索路由和正常接口调用。
                    if args.login and urlparse(page.url).path.rstrip('/') == '/explore':
                        show('复用登录后的探索页', '不重复加载整页')
                        page_check(page, guard)
                    else:
                        visit('https://www.xiaohongshu.com/explore')
                    wait_ready(page, guard)
                    # 同一页面可能包含隐藏的重复输入框；只选择当前可见的搜索框。
                    # 实测新布局使用带明确 ID 的真实 textarea；排除 aria-hidden 的重复控件。
                    search = page.locator(SEARCH_SELECTOR).first
                    search.wait_for(state='visible', timeout=15000)
                    if not search.is_editable():
                        raise RuntimeError('当前搜索控件不可编辑，已停止。')
                    archive.begin('search')
                    show('定位实际搜索控件', search.get_attribute('id') or 'unknown')
                    show('输入搜索词', args.keyword)
                    before_action('输入搜索词')
                    search.fill(args.keyword)
                    if search.input_value() != args.keyword:
                        raise RuntimeError('搜索框输入值与关键词不一致，已停止，未提交搜索。')
                    show('提交搜索')
                    before_action('提交搜索')
                    search.press('Enter')
                    page.wait_for_url('**/search_result**', timeout=15000)
                    page.wait_for_timeout(2000)
                    page_check(page, guard)
                    search_url = page.url
                if args.url:
                    visit(search_url)
                rounds = 1 if args.test_once else args.search_rounds
                previous_count, stagnant_rounds = len(store.ordered_rows(job)), 0
                for i in range(rounds):
                    page_check(page, guard)
                    show('保存搜索页面并离线解析', str(archive.current))
                    store.upsert(capture_page(page, archive, args.keyword), job)
                    selected = plan_rows(store, job, args.limit) if args.details else store.rows(job, args.limit)
                    export_checkpoint(store, job)
                    show('更新搜索清单', f'已发现 {len(store.ordered_rows(job))} 条，选中 {len(selected)} 条')
                    guard.check()
                    if len(selected) >= args.limit:
                        break
                    discovered_count = len(store.ordered_rows(job))
                    stagnant_rounds = stagnant_rounds + 1 if discovered_count <= previous_count else 0
                    previous_count = discovered_count
                    if stagnant_rounds >= 3:
                        report['search_stop_reason'] = 'three_rounds_without_new_notes'
                        show('停止继续滚动搜索', '连续三轮没有新增笔记；仅处理已选条目，不表示已达到目标数量')
                        break
                    if i < rounds - 1:
                        pause_after_note(args, store, report)
                        wait_ready(page, guard)
                        archive.begin('search')
                        show('滚动加载下一批搜索结果')
                        before_action('滚动搜索结果')
                        page.mouse.wheel(0, 650)
                        page.wait_for_timeout(1500)
                phase['kind'] = 'idle'
                # 搜索解析完成后，先开始约定的随机等待，再打开第一篇详情。
                pause_after_note(args, store, report)
            selected = store.rows(job, 1) if direct_note else plan_rows(store, job, args.limit) if args.details else store.rows(job, args.limit)
            report['selected_ids'] = [r['note_id'] for r in selected]
            if args.details:
                for index, row in enumerate(selected):
                    if complete(store.find(row['note_id']) or {}) and not args.test_once:
                        report['cache_skipped'] += 1
                        show('跳过完整缓存', row['note_id'])
                        continue
                    wait_ready(page, guard)
                    phase.update(kind='detail', id=row['note_id'])
                    if not resolved_share:
                        archive.begin('detail', row['note_id'])
                    result = {'note_id': row['note_id'], 'status': 'started'}
                    report['detail_results'].append(result)
                    # 有真实页面链接时点击进入；断点恢复、列表无链接时才使用已保存 URL。
                    # 优先点击用户确认的封面链接，包括 /search_result/笔记ID 路径。
                    links = page.locator(f'a.cover[href*="/{row["note_id"]}"]:visible')
                    if not links.count():
                        links = page.locator(f'a[href*="/{row["note_id"]}"]:visible')
                    visible = next((links.nth(i) for i in range(min(links.count(), 20)) if links.nth(i).is_visible()), None)
                    origin = page.url
                    if resolved_share:
                        result['navigation'] = 'share_redirect_reused'
                    elif visible:
                        show('点击笔记链接', row['note_id'])
                        before_action('点击笔记链接')
                        visible.click(timeout=15000)
                        result['navigation'] = 'page_link_click'
                        page.wait_for_timeout(1500)
                    else:
                        result['navigation'] = 'saved_url_resume'
                        show('使用保存的笔记链接', row['note_id'])
                        visit(row['url'])
                    page_check(page, guard)
                    if args.expect_title:
                        title = page.locator('#detail-title, .note-content .title').first
                        title.wait_for(state='visible', timeout=12000)
                        actual_title = title.inner_text().strip()
                        if actual_title != args.expect_title.strip():
                            raise RuntimeError('笔记标题与指定标题不一致，停止评论测试。')
                        show('标题核对通过', actual_title)
                    # 等待站点本身的正文 UI；不以固定 3 秒代替加载完成。
                    desc = page.locator('#detail-desc')
                    try:
                        desc.first.wait_for(state='visible', timeout=12000)
                    except Exception:
                        page_check(page, guard)
                    show('保存笔记页面并离线解析', row['note_id'])
                    captured = capture_page(page, archive, args.keyword)
                    fresh_content = any(r['note_id'] == row['note_id'] and complete(r) for r in captured)
                    store.upsert(captured, job)
                    export_checkpoint(store, job)
                    guard.check()
                    current = store.find(row['note_id']) or row
                    content_ok = fresh_content if args.test_once else complete(current)
                    result['status'] = 'content_ok' if content_ok else 'content_missing'
                    result['fresh_content'] = fresh_content
                    print(f'笔记 {row["note_id"]}：{result["status"]}')
                    show('正文解析完成', f'{row["note_id"]}，{len(current.get("content") or "")} 字符，{result["status"]}')
                    if not content_ok:
                        guard.halt('detail_content_missing')
                        guard.check()
                    result['status'] = 'interacting'
                    activity = {'images': {}, 'comments': {}}
                    result['interactions'] = activity

                    def save_activity(name):
                        target = archive.current / 'interactions'
                        target.mkdir(exist_ok=True)
                        (target / (name + '.html')).write_text(page.content(), encoding='utf-8')
                        (target / 'progress.json').write_text(json.dumps(activity, ensure_ascii=False, indent=2), encoding='utf-8')

                    check = lambda: page_check(page, guard)
                    if args.browse_images:
                        browse_images(page, int(current.get('image_count') or 0), check, short_pause,
                                      save_activity, show, activity['images'])
                    else:
                        activity['images']['status'] = 'disabled'
                    if args.expand_comments:
                        activity['comments']['loading'] = wait_comments_ready(
                            page, check, show, lambda: comment_batches.get(row['note_id']))
                        save_activity('before_comment_expansion')
                        expand_comments(page, check, short_pause, save_activity, show, activity['comments'],
                                        test_one=args.test_expand_reply)
                    else:
                        activity['comments']['status'] = 'disabled'
                    save_activity('interactions_finished')
                    # 保留最终轮播位置、展开后的回复，以及交互过程中实际返回的数据。
                    store.upsert(capture_page(page, archive, args.keyword), job)
                    export_checkpoint(store, job)
                    guard.check()
                    result['status'] = 'content_ok'
                    pause_after_note(args, store, report)
                    phase['kind'] = 'idle'
                    if not content_ok:
                        guard.halt('detail_content_missing')
                        guard.check()
                    pending = any(not complete(store.find(r['note_id']) or {}) for r in selected[index+1:])
                    if visible and pending:
                        # 返回也可能发请求，因此同样遵守刚记录的间隔。
                        wait_ready(page, guard)
                        close = page.locator('.close-circle, .note-detail-mask .close, [aria-label="关闭"]')
                        closer = next((close.nth(i) for i in range(min(close.count(), 10)) if close.nth(i).is_visible()), None)
                        if closer:
                            show('关闭笔记详情')
                            before_action('关闭笔记详情')
                            closer.click()
                        elif page.url != origin:
                            show('返回搜索结果')
                            before_action('返回搜索结果')
                            page.go_back(wait_until='domcontentloaded', timeout=30000)
                        else:
                            guard.halt('detail_return_unavailable')
                        page_check(page, guard)
        except BaseException as exc:
            report['load_diagnostics'] = diagnostics.snapshot(page, type(exc).__name__)
            if page is not None and archive.current:
                # 失败时保留实时 DOM 与截图，便于区分定位错误、布局变化或登录问题。
                try:
                    (archive.current / 'error_rendered.html').write_text(page.content(), encoding='utf-8')
                    report['error_diagnostics'] = str(archive.current.resolve())
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(archive.current / 'error_page.png'), timeout=3000)
                except Exception:
                    pass
            results = report['detail_results']
            if results and results[-1]['status'] in ('started', 'interacting'):
                results[-1].update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'stopped' if isinstance(exc, Stopped) else 'error',
                                   error_type=type(exc).__name__)
            raise
        finally:
            if 'load_diagnostics' not in report:
                report['load_diagnostics'] = diagnostics.snapshot(page, 'completed')
            # context.close 之前先让路由全部中止，避免退出时继续计入新访问。
            try:
                context.route('**/*', lambda route: route.abort())
                context.unroute('**/*', route_request)
            finally:
                context.close()
                show('浏览器已关闭')
                # 最后从已接收图片池再匹配一次，覆盖快照之后才完成的图片响应。
                for manifest in (archive.root / 'posts').rglob('manifest.json'):
                    folder = manifest.parent
                    rows = parse_capture(folder, args.keyword)
                    store.upsert(rows, job)
                    for row in rows:
                        show('图片归档完成', f'{row["note_id"]}，已保存 {row.get("saved_image_count", 0)}/{row.get("image_count", 0)} 张（仅已加载图片）')


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--keyword', default='景区信息 旅游规划')
    ap.add_argument('--url')
    ap.add_argument('--limit', type=int, default=45, help='目标总条数，默认 45，包含命中的缓存')
    ap.add_argument('--details', action=argparse.BooleanOptionalAction, default=True, help='默认采集正文；--no-details 仅保存摘要')
    ap.add_argument('--search-rounds', type=int, default=30, help='搜索结果最多采集轮数，每轮之间遵守随机等待')
    ap.add_argument('--login', action='store_true')
    ap.add_argument('--login-only', action='store_true', help='仅人工登录并保存浏览器状态，不执行采集')
    ap.add_argument('--headless', action='store_true')
    ap.add_argument('--slow-mo', type=int, default=0, help='浏览器操作慢放毫秒数，便于观察；不替代随机等待')
    ap.add_argument('--browse-images', action=argparse.BooleanOptionalAction, default=True,
                    help='默认浏览全部配图，翻图前随机等待 1–5 秒')
    ap.add_argument('--expand-comments', action=argparse.BooleanOptionalAction, default=True,
                    help='默认按 50%%/25%%/25%% 概率展开 0/1/2 条回复，可展开数量不足则不展开')
    ap.add_argument('--test-once', action='store_true', help='单次验证：最多一篇；给定完整笔记 URL 时跳过重复搜索')
    ap.add_argument('--test-expand-reply', action='store_true', help='仅配合 --test-once 验证一条回复展开，正常采集仍使用随机规则')
    ap.add_argument('--expect-title', help='单篇测试时核对可见笔记标题，不一致则停止')
    ap.add_argument('--channel', default='msedge', choices=['msedge', 'chrome', 'chromium'])
    ap.add_argument('--profile', default=ACTIVE_PROFILE, help='默认复用 check_20261030；不自动切回旧账号目录')
    ap.add_argument('--offline', type=Path, nargs='+', help='仅解析本地文件，不联网')
    ap.add_argument('--import-only', action='store_true', help='导入历史导出文件后退出，不联网')
    ap.add_argument('--status', action='store_true', help='查看缓存、停止锁和预算后退出')
    ap.add_argument('--clear-stop', metavar='REASON', help='记录人工解除停止锁的原因后退出，不重置预算')
    ap.add_argument('--stop', metavar='REASON', help='手动设置全局停止锁后退出，不联网')
    ap.add_argument('--state-dir', type=Path, default=BASE / '.state')
    ap.add_argument('--max-requests', type=int, default=15000)
    ap.add_argument('--daily-requests', type=int, default=20000)
    ap.add_argument('--task-requests', type=int, default=15000, help='同一搜索词或 URL 任务跨次累计请求上限，从本版本启用时起统计')
    ap.add_argument('--total-requests', type=int, default=None, help='可选历史累计请求上限；默认只统计不限制')
    ap.add_argument('--max-minutes', type=float, default=180)
    ap.add_argument('--min-wait', type=float, default=10)
    ap.add_argument('--max-wait', type=float, default=60)
    ap.add_argument('--long-probability', type=float, default=.05)
    ap.add_argument('--long-min', type=float, default=300)
    ap.add_argument('--long-max', type=float, default=600)
    return ap


def run(args):
    with RunLock(args.state_dir / 'run.lock'):
        store = Store(args.state_dir / 'state.sqlite3')
        try:
            bootstrap(store)
            if args.stop:
                store.stop(args.stop)
                print('已设置全局停止锁，没有联网。')
                return 0
            if args.clear_stop:
                store.clear_stop(args.clear_stop)
                print('已解除停止锁，审计原因已保存；没有联网，预算及等待未重置。')
                return 0
            if args.status or args.import_only:
                print(json.dumps(store.status(), ensure_ascii=False, indent=2))
                return 0
            job = 'login:' + args.profile if args.login_only else job_key(args.keyword, args.url)
            budget_job = job  # 单篇测试也累计到稳定任务键，不随测试时间戳重置。
            if args.test_once:
                job = 'test:' + job + ':' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            report = {'keyword': args.keyword, 'status': 'ok', 'details_requested': args.details,
                      'mode': 'offline' if args.offline else 'browser', 'detail_results': [],
                      'cache_skipped': 0, 'pauses': [], 'requests_run': 0}
            report['test_once'] = args.test_once
            report['target_count'] = args.limit
            report['search_rounds_limit'] = args.search_rounds
            report['budgets'] = {'run_requests': args.max_requests, 'daily_requests': args.daily_requests,
                                 'task_requests': args.task_requests, 'total_requests': args.total_requests,
                                 'max_minutes': args.max_minutes}
            guard = None
            try:
                if args.offline:
                    for path in args.offline:
                        store.upsert(offline_rows(path, args.keyword), job)
                else:
                    rows = store.rows(job, args.limit)
                    existing_plan = store.get('sampling:' + job)
                    if args.details and existing_plan:
                        rows = [store.find(p['note_id']) for p in existing_plan['selected'][:args.limit]]
                    if not args.login and not args.test_once and len(rows) >= args.limit and (not args.details or all(complete(r) for r in rows)):
                        report['status'] = 'cache_hit'
                        report['cache_skipped'] = len(rows)
                    else:
                        guard = Guard(store, args.max_requests, args.daily_requests, args.total_requests, args.max_minutes * 60,
                                      task_key=budget_job, task_limit=args.task_requests)
                        guard.check()
                        guard.check_request_budget()
                        # 启动浏览器前先遵守上次未结束的等待。
                        wait_ready(None, guard)
                        online(args, store, job, guard, report)
            except KeyboardInterrupt:
                report['status'] = 'interrupted'
                store.audit('interrupted', '用户中断，保留缓存与剩余等待')
                if guard and guard.requests:
                    pause_after_note(args, store, report)
                print('已中断，缓存和等待时间已保留。')
            except Stopped as exc:
                report.update(status='stopped', reason=str(exc))
                print(f'已停止：{exc}')
            except Exception as exc:
                report.update(status='error', error_type=type(exc).__name__, error_message=error_summary(exc))
                if not args.offline:
                    store.stop('runtime_error:' + type(exc).__name__)
                print(f'运行异常并停止：{report["error_message"]}')
            if guard:
                report['requests_run'] = guard.requests
                report['runtime_seconds'] = round(guard.clock() - guard.started, 2)
            report['state'] = store.status()
            report['requests_task'] = int(store.get('task_requests:' + budget_job, 0))
            if args.login_only:
                folder = OUT / 'login_reports'
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('login_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print('登录操作报告：' + str(path))
                return 0 if report['status'] == 'ok' else 2
            report['checkpoint_csv'] = str(export_checkpoint(store, job).resolve())
            if report.get('selected_ids') is not None:
                rows = [store.find(nid) for nid in report['selected_ids']]
            elif args.details and not args.offline and store.get('sampling:' + job):
                rows = [store.find(p['note_id']) for p in store.get('sampling:' + job)['selected'][:args.limit]]
            else:
                rows = store.rows(job, args.limit)
            return save_result([r for r in rows if r], report, output=keyword_output(args.keyword))
        finally:
            store.close()


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    if args.login_only:
        if any((args.url, args.test_once, args.offline, args.status, args.import_only,
                args.stop, args.clear_stop)):
            ap.error('--login-only 不可与采集测试、链接、离线或状态管理参数组合')
        args.login = True
    if args.test_expand_reply and (not args.test_once or not args.expand_comments):
        ap.error('--test-expand-reply 需要 --test-once 且不能禁用评论展开')
    if args.expect_title and not args.test_once:
        ap.error('--expect-title 仅用于 --test-once')
    if args.test_once:
        args.limit, args.details = 1, True
        if args.offline or args.status or args.import_only or args.stop or args.clear_stop:
            ap.error('--test-once 不可与离线或状态管理参数组合')
        if args.url and not share_url(args.url) and not re.fullmatch(r'/(?:explore|search_result|discovery/item)/[a-fA-F0-9]{24}', urlparse(args.url).path.rstrip('/')):
            ap.error('--test-once --url 只接受完整笔记或官方分享短链接，不接受列表')
    if not 0 <= args.slow_mo <= 5000:
        ap.error('--slow-mo 范围为 0–5000 毫秒')
    if not 1 <= args.limit <= 100:
        ap.error('--limit 范围为 1–100')
    if not 1 <= args.search_rounds <= 100:
        ap.error('--search-rounds 范围为 1–100')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', args.profile):
        ap.error('--profile 只能包含 1–40 个英文字母、数字、下划线或短横线')
    if args.profile == 'default':
        ap.error('旧账号 default 目录已停用，请使用 check_20261030 或新的独立目录')
    if args.login and args.headless:
        ap.error('--login 需要可见浏览器')
    if sum(bool(v) for v in (args.status, args.import_only, args.offline, args.clear_stop, args.stop)) > 1:
        ap.error('--status、--import-only、--offline、--clear-stop、--stop 请分别运行')
    if any(not math.isfinite(v) for v in (args.max_minutes, args.min_wait, args.max_wait,
                                           args.long_min, args.long_max, args.long_probability)):
        ap.error('时间和概率必须为有限数值')
    if min(args.max_requests, args.daily_requests, args.task_requests, args.max_minutes) <= 0 or (args.total_requests is not None and args.total_requests <= 0):
        ap.error('预算必须大于 0')
    if not (10 <= args.min_wait <= args.max_wait and 300 <= args.long_min <= args.long_max and 0 <= args.long_probability <= 1):
        ap.error('短等待至少 10 秒，长等待至少 300 秒，概率范围 0–1，最大值不得小于最小值')
    if args.clear_stop is not None and not args.clear_stop.strip():
        ap.error('解除停止锁必须填写原因')
    if args.stop is not None and not args.stop.strip():
        ap.error('停止锁必须填写原因')
    if args.url:
        if not official_url(args.url):
            ap.error('仅支持小红书 HTTPS 链接')
    try:
        return run(args)
    except (Stopped, OSError, ValueError, sqlite3.Error) as exc:
        print('无法启动：' + error_summary(exc))
        return 2


if __name__ == '__main__':
    sys.exit(main())


