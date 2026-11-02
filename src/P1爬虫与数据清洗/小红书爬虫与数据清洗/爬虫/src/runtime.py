"""本地缓存、全局停止锁与请求预算；不依赖浏览器，也不发起网络请求。"""
import json
import os
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PROVENANCE_FIELDS = ('source_archive', 'original_html_path', 'rendered_html_path', 'raw_json_paths',
                     'image_count', 'saved_image_count', 'image_manifest',
                     'saved_comment_count', 'comment_manifest')


class Stopped(RuntimeError):
    pass


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def complete(row):
    return row.get('content_scope') == 'detail' and bool(str(row.get('content') or '').strip())


class RunLock:
    """OS 文件锁随进程结束释放，不因中断留下无法恢复的运行锁。"""
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise Stopped('已有实例运行；请先关闭它。') from None
        return self

    def __exit__(self, *args):
        self.file.close()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(job TEXT, id TEXT, PRIMARY KEY(job,id));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(at TEXT, event TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS discovery_sources(job TEXT, id TEXT, data TEXT,
                                                         PRIMARY KEY(job,id));
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def audit(self, event, detail):
        with self.db:
            self.db.execute('INSERT INTO audit VALUES(?,?,?)', (now(), event, str(detail)))

    def stop(self, reason):
        if not self.get('stop'):
            self.set('stop', {'reason': reason, 'at': now()})
            self.audit('stop', reason)

    def clear_stop(self, reason):
        self.audit('manual_clear_stop', reason)
        self.set('stop', None)

    def upsert(self, rows, job):
        with self.db:
            for row in rows:
                nid = str(row.get('note_id') or '')
                if not nid:
                    continue
                old = self.db.execute('SELECT data FROM notes WHERE id=?', (nid,)).fetchone()
                old = json.loads(old[0]) if old else {}
                if not complete(row) and any(row.get(k) for k in PROVENANCE_FIELDS):
                    source = {k: row.get(k) or '' for k in PROVENANCE_FIELDS}
                    self.db.execute('INSERT OR REPLACE INTO discovery_sources VALUES(?,?,?)',
                                    (job, nid, json.dumps(source, ensure_ascii=False)))
                merged = dict(old)
                for key, value in row.items():
                    if value is not None and value != '':
                        merged[key] = value
                if complete(old) and not complete(row):
                    merged.update(content=old['content'], content_scope='detail')
                    # 正文及其来源是一组；搜索摘要不能改写正文的详情来源。
                    for key in PROVENANCE_FIELDS:
                        merged[key] = old.get(key) or ''
                elif complete(row) and (not complete(old) or row['content'] != old.get('content')
                                        or any(row.get(k) for k in PROVENANCE_FIELDS)):
                    # 新正文没有来源时留空，不能错误继承上一版正文的路径。
                    for key in PROVENANCE_FIELDS:
                        merged[key] = row.get(key) or ''
                if old.get('url') and 'xsec_token=' in old['url'] and 'xsec_token=' not in str(row.get('url', '')):
                    merged['url'] = old['url']
                self.db.execute('INSERT OR REPLACE INTO notes VALUES(?,?)', (nid, json.dumps(merged, ensure_ascii=False)))
                self.db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?)', (job, nid))

    def rows(self, job, limit):
        values = self.ordered_rows(job)
        # 已完成记录优先，避免旧空摘要挡住后面的完整缓存而触发无谓访问。
        values.sort(key=lambda row: not complete(row))
        return values[:limit]

    def ordered_rows(self, job):
        rows = self.db.execute('''SELECT notes.data FROM jobs JOIN notes ON notes.id=jobs.id
                                 WHERE job=? ORDER BY jobs.rowid''', (job,))
        return [json.loads(r[0]) for r in rows]

    def find(self, nid):
        row = self.db.execute('SELECT data FROM notes WHERE id=?', (nid,)).fetchone()
        return json.loads(row[0]) if row else None

    def discovery_source(self, job, nid):
        row = self.db.execute('SELECT data FROM discovery_sources WHERE job=? AND id=?', (job, nid)).fetchone()
        return json.loads(row[0]) if row else {}

    def counts(self):
        day = datetime.now().astimezone().date().isoformat()
        daily = self.db.execute('SELECT count FROM usage WHERE day=?', (day,)).fetchone()
        total = self.db.execute('SELECT COALESCE(SUM(count),0) FROM usage').fetchone()[0]
        return (daily[0] if daily else 0), total

    def request(self):
        day = datetime.now().astimezone().date().isoformat()
        with self.db:
            self.db.execute('INSERT INTO usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1', (day,))

    def status(self):
        daily, total = self.counts()
        return {'stop': self.get('stop'), 'requests_today': daily, 'requests_total': total,
                'next_visit_at': self.get('next_visit_at', 0),
                'cached_notes': self.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]}


class Guard:
    def __init__(self, store, run_limit, daily_limit, total_limit, seconds, clock=time.monotonic,
                 task_key=None, task_limit=None):
        self.store, self.clock = store, clock
        self.started = clock()
        self.seconds = seconds
        self.run_limit, self.daily_limit, self.total_limit = run_limit, daily_limit, total_limit
        self.requests = 0
        self.reason = None
        self.task_key, self.task_limit = task_key, task_limit

    def task_requests(self):
        return int(self.store.get('task_requests:' + self.task_key, 0)) if self.task_key else 0

    def check_request_budget(self):
        daily, total = self.store.counts()
        for used, limit, label in ((self.requests, self.run_limit, 'run'),
                                   (daily, self.daily_limit, 'daily'),
                                   (total, self.total_limit, 'total'),
                                   (self.task_requests(), self.task_limit, 'task')):
            if limit is not None and used >= limit:
                self.halt(label + '_request_budget_exceeded')
                raise Stopped(self.reason)

    def halt(self, reason):
        self.reason = self.reason or reason
        self.store.stop(self.reason)

    def check(self):
        lock = self.store.get('stop')
        if lock:
            raise Stopped(lock['reason'])
        if self.clock() - self.started >= self.seconds:
            self.halt('runtime_budget_exceeded')
            raise Stopped(self.reason)

    def allow_request(self):
        self.check()
        self.check_request_budget()
        # 发送前持久化。重定向、资源请求和失败的尝试均计数，不只计页面数量。
        self.store.request()
        if self.task_key:
            self.store.set('task_requests:' + self.task_key, self.task_requests() + 1)
        self.requests += 1


def schedule_pause(store, minimum=10, maximum=60, probability=0.05,
                   long_min=300, long_max=600, rng=random, wall=time.time):
    short = rng.uniform(minimum, maximum)
    extra = rng.uniform(long_min, long_max) if rng.random() < probability else 0
    until = max(float(store.get('next_visit_at', 0)), wall() + short + extra)
    store.set('next_visit_at', until)
    return short, extra


def response_notice(status=200, payload=None, url=''):
    """仅识别本地归档实际出现的特定矛盾空响应，不推断其他接口的语义。"""
    if (status == 200 and urlparse(url).hostname == 'edith.xiaohongshu.com'
            and urlparse(url).path == '/api/sns/web/v1/search/onebox'
            and isinstance(payload, dict) and type(payload.get('code')) is int
            and payload == {'msg': '成功', 'data': {}, 'code': 0, 'success': False}):
        return 'onebox_empty_inconsistent_response'
    return None


def restriction_reason(status=200, payload=None, text='', url=''):
    if status in (403, 429):
        return f'http_{status}'
    if '/website-login/error' in url:
        return 'website_security_error'
    words = ('安全限制', 'IP存在风险', '安全验证', '访问频次异常', '网络环境存在风险',
             '请完成验证', '访问过于频繁', '请求过于频繁', '账号异常', '账号存在异常',
             '账号已被限制', '操作过于频繁', 'captcha', 'rate limit')
    message = text
    if isinstance(payload, dict):
        # 只检查响应状态信息，不扫描笔记正文，避免正文提及“验证码”被误判。
        message += ' '.join(str(payload.get(k) or '') for k in ('message', 'msg', 'error_msg', 'error'))
        if str(payload.get('code')) in ('300012', '-100', '-101', '-102'):
            return 'api_restriction_or_login'
        data = payload.get('data')
        if isinstance(data, dict):
            message += ' '.join(str(data.get(k) or '') for k in ('message', 'msg', 'error_msg'))
            if data.get('captcha') or data.get('verify_url') or data.get('verifyUrl'):
                return 'api_verification'
        if any(word.lower() in message.lower() for word in words):
            return 'restriction_message'
        if response_notice(status, payload, url):
            return None
        if payload.get('success') is False:
            return 'api_unsuccessful'  # 未知错误也停止，不继续试探。
        if payload.get('success') is not True and payload.get('code') is not None and str(payload['code']) not in ('0', '200'):
            return 'api_unknown_error_code'
    if any(word.lower() in message.lower() for word in words):
        return 'restriction_message'
    return None
