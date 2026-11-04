# -*- coding: utf-8 -*-
"""只读核查 P1 元数据缺口：哪些能从原始来源恢复，哪些确实不存在。

不修改任何产物。逐字段尝试已知来源：
- 小红书笔记：爬虫结构化输出的 title / published_at / collected_at
- 公众号 html：var ct（发布时间戳）
- 正文快照（manual/*.txt）：无时间信息
"""
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'processed/P1爬虫数据清洗/documents.json'
CACHE = ROOT / '.cache/小红书爬虫'
RAW_HTML = ROOT / 'data/微信公众号与景区官方/source_archive/raw_html'
MANUAL = ROOT / 'data/微信公众号与景区官方/manual'
CST = timezone(timedelta(hours=8))

FIELDS = ('title', 'publish_date', 'crawl_time')


def note_index():
    """小红书爬虫输出：作者→笔记，按作者索引（作者在语料内唯一）。"""
    idx = {}
    for f in CACHE.glob('*/notes_*.json'):
        for n in json.load(open(f, encoding='utf-8')):
            idx[n.get('author') or ''] = n
    return idx


def recover(doc, notes):
    """返回 {字段: (值或None, 依据)}。"""
    out = {}
    plat = doc.get('source_platform') or ''
    if '小红书' in plat:
        n = notes.get(doc.get('author') or '')
        if n:
            out['title'] = (n.get('title') or None, '爬虫输出 title')
            ts = n.get('published_at')
            out['publish_date'] = (datetime.fromtimestamp(ts / 1000, CST).strftime('%Y-%m-%d')
                                   if ts else None, '爬虫输出 published_at')
            out['crawl_time'] = (n.get('collected_at') or None, '爬虫输出 collected_at')
    html = RAW_HTML / (doc['doc_id'] + '.html')
    if html.exists():
        raw = html.read_text('utf-8', errors='ignore')
        m = re.search(r'var\s+ct\s*=\s*["\']?(\d{10})', raw)
        out.setdefault('publish_date',
                       (datetime.fromtimestamp(int(m.group(1)), CST).strftime('%Y-%m-%d')
                        if m else None, 'html var ct' if m else 'html 无时间字段'))
        out['crawl_time'] = (None, 'crawl_log 无请求时间记录')
    manual = MANUAL / (doc['doc_id'] + '.txt')
    if manual.exists():
        # 快照只是补充来源：已从 HTML 恢复到的值不能被"快照无日期"覆盖
        if not out.get('publish_date', (None,))[0]:
            out['publish_date'] = (None, '正文快照无日期')
        out.setdefault('crawl_time', (None, '正文快照无时间'))
    return out


def main():
    docs = json.load(open(DOCS, encoding='utf-8'))
    notes = note_index()
    rows = []
    for d in docs:
        miss = [f for f in FIELDS if not str(d.get(f) or '').strip()]
        if not miss:
            continue
        rec = recover(d, notes)
        for f in miss:
            val, why = rec.get(f, (None, '无已知来源'))
            rows.append({'doc_id': d['doc_id'], 'field': f,
                         '可恢复': '是' if val else '否',
                         '恢复值': val or '', '依据': why})
    print('%-14s %-12s %-6s %-12s %s' % ('doc_id', '字段', '可恢复', '恢复值', '依据'))
    for r in rows:
        print('%-14s %-12s %-6s %-12s %s' % (r['doc_id'], r['field'], r['可恢复'],
                                            r['恢复值'], r['依据']))
    ok = sum(1 for r in rows if r['可恢复'] == '是')
    print('\n缺口 %d 项：可恢复 %d，确实不存在 %d' % (len(rows), ok, len(rows) - ok))
    print('注意：即使可恢复，修改 documents.json 会改变 P1 产物哈希，触发全链重跑；'
          '下游阶段不读取这些字段。')


if __name__ == '__main__':
    main()
